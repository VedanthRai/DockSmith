"""
Build engine - parses and executes Docksmithfile instructions.
Manages layers, cache, and image assembly.
"""

import os
import sys
import time
import hashlib
import datetime
import glob as glob_module
import shutil
import tempfile
import json

from .parser import parse_docksmithfile, ParseError
from .image_store import ImageStore
from .layer_builder import (
    create_copy_layer, create_run_layer, compute_tar_digest,
    extract_layer, snapshot_rootfs
)
from .cache_manager import CacheManager
from .isolation import IsolationEngine


class BuildError(Exception):
    pass


class BuildEngine:
    def __init__(self, state, no_cache=False):
        self.state = state
        self.no_cache = no_cache
        self.store = ImageStore(state)
        self.cache = CacheManager(state)
        self.isolation = IsolationEngine()

    def build(self, docksmithfile, context_dir, name, tag):
        """
        Parse and execute a Docksmithfile.
        Returns the final image manifest dict.
        """
        try:
            instructions = parse_docksmithfile(docksmithfile)
        except ParseError as e:
            raise BuildError(str(e))

        if not instructions or instructions[0].name != "FROM":
            raise BuildError("Docksmithfile must start with a FROM instruction")

        # Count steps
        total_steps = len(instructions)

        # Build state
        layers = []           # list of layer manifest dicts
        env_state = {}        # accumulated ENV key=values
        workdir = ""          # current WORKDIR
        cmd = None            # CMD value
        prev_layer_digest = None  # digest of last layer-producing step
        cache_busted = False  # once True, all subsequent steps are misses

        # Track if all steps were cache hits (for manifest timestamp preservation)
        # --no-cache always counts as a miss for timestamp purposes
        all_cache_hits = not self.no_cache
        existing_created = None

        # Assemble rootfs in a temp directory that persists for the build
        build_rootfs = tempfile.mkdtemp(prefix="docksmith_build_")

        try:
            for step_idx, instr in enumerate(instructions):
                step_num = step_idx + 1
                print(f"Step {step_num}/{total_steps} : {instr.raw_text}", end="", flush=True)

                if instr.name == "FROM":
                    # Load base image
                    image_name = instr.args["image"]
                    image_tag = instr.args["tag"]
                    print()  # FROM always prints step with no cache/timing

                    try:
                        base_image = self.store.get_image(image_name, image_tag)
                    except FileNotFoundError:
                        raise BuildError(
                            f"Base image '{image_name}:{image_tag}' not found in local store. "
                            f"Run 'bash setup.sh' to import base images, or: "
                            f"python3 scripts/import_base_image.py"
                        )

                    # Extract base image layers into build rootfs
                    for layer_meta in base_image.get("layers", []):
                        layer_path = self.state.layer_path(layer_meta["digest"])
                        if not os.path.exists(layer_path):
                            raise BuildError(
                                f"Layer {layer_meta['digest'][:16]}... is missing from disk. "
                                f"The base image may be corrupted."
                            )
                        extract_layer(layer_path, build_rootfs)

                    # Copy base image layers into our layers list
                    layers = list(base_image.get("layers", []))

                    # Copy base config
                    base_config = base_image.get("config", {})
                    env_state = {}
                    for env_str in base_config.get("Env", []):
                        if "=" in env_str:
                            k, v = env_str.split("=", 1)
                            env_state[k] = v
                    workdir = base_config.get("WorkingDir", "")
                    cmd = base_config.get("Cmd")

                    # The "previous digest" for cache key purposes is the base image manifest digest
                    prev_layer_digest = base_image.get("digest", "")

                    # Check if image already exists (for timestamp preservation)
                    try:
                        existing_img = self.store.get_image(name, tag)
                        existing_created = existing_img.get("created")
                    except FileNotFoundError:
                        existing_created = None

                elif instr.name == "WORKDIR":
                    workdir = instr.args["path"]
                    # Create dir in current rootfs if it doesn't exist
                    # (will be created silently before next layer-producing instruction)
                    print()  # no cache status

                elif instr.name == "ENV":
                    env_state[instr.args["key"]] = instr.args["value"]
                    print()  # no cache status

                elif instr.name == "CMD":
                    cmd = instr.args["cmd"]
                    print()

                elif instr.name == "COPY":
                    step_start = time.time()
                    srcs = instr.args["srcs"]
                    dest = instr.args["dest"]

                    # Resolve source files for cache key computation
                    resolved_files = self._resolve_copy_sources(srcs, context_dir)
                    file_hashes = self.cache.compute_file_hashes(
                        [f[0] for f in resolved_files], context_dir
                    )

                    # Compute cache key
                    cache_key = self.cache.compute_cache_key(
                        prev_layer_digest,
                        instr.raw_text,
                        workdir,
                        env_state,
                        file_hashes
                    )

                    cached_digest = None
                    if not self.no_cache and not cache_busted:
                        cached_digest = self.cache.lookup(cache_key)

                    if cached_digest:
                        # Cache HIT
                        elapsed = time.time() - step_start
                        print(f" [CACHE HIT] {elapsed:.2f}s")

                        # Extract the cached layer into rootfs
                        layer_path = self.state.layer_path(cached_digest)
                        extract_layer(layer_path, build_rootfs)

                        layer_size = os.path.getsize(layer_path)
                        layers.append({
                            "digest": cached_digest,
                            "size": layer_size,
                            "createdBy": instr.raw_text
                        })
                        prev_layer_digest = cached_digest
                    else:
                        # Cache MISS
                        all_cache_hits = False
                        cache_busted = True

                        # Ensure workdir exists before COPY
                        if workdir:
                            self._ensure_workdir(build_rootfs, workdir)

                        # Execute COPY - create layer tar
                        tar_bytes, copied = create_copy_layer(
                            srcs, dest, context_dir, build_rootfs
                        )
                        digest = compute_tar_digest(tar_bytes)

                        # Write layer to disk
                        layer_path = self.state.layer_path(digest)
                        with open(layer_path, "wb") as f:
                            f.write(tar_bytes)

                        # Extract into rootfs (use path, not bytes, for consistency)
                        extract_layer(layer_path, build_rootfs)

                        # Update cache
                        if not self.no_cache:
                            self.cache.store(cache_key, digest)

                        elapsed = time.time() - step_start
                        print(f" [CACHE MISS] {elapsed:.2f}s")

                        layer_size = len(tar_bytes)
                        layers.append({
                            "digest": digest,
                            "size": layer_size,
                            "createdBy": instr.raw_text
                        })
                        prev_layer_digest = digest

                elif instr.name == "RUN":
                    step_start = time.time()
                    command_text = instr.args["command"]

                    # Compute cache key
                    cache_key = self.cache.compute_cache_key(
                        prev_layer_digest,
                        instr.raw_text,
                        workdir,
                        env_state
                    )

                    cached_digest = None
                    if not self.no_cache and not cache_busted:
                        cached_digest = self.cache.lookup(cache_key)

                    if cached_digest:
                        # Cache HIT
                        elapsed = time.time() - step_start
                        print(f" [CACHE HIT] {elapsed:.2f}s")

                        layer_path = self.state.layer_path(cached_digest)
                        extract_layer(layer_path, build_rootfs)

                        layer_size = os.path.getsize(layer_path)
                        layers.append({
                            "digest": cached_digest,
                            "size": layer_size,
                            "createdBy": instr.raw_text
                        })
                        prev_layer_digest = cached_digest
                    else:
                        # Cache MISS - execute RUN in isolation
                        all_cache_hits = False
                        cache_busted = True

                        # Ensure workdir exists
                        if workdir:
                            self._ensure_workdir(build_rootfs, workdir)

                        # Snapshot before (before helper script is written)
                        before = snapshot_rootfs(build_rootfs)

                        # Build run environment: image ENV + current env_state
                        run_env = dict(env_state)
                        if workdir:
                            run_env["PWD"] = workdir

                        # Execute RUN in isolation
                        exit_code = self.isolation.run_isolated(
                            build_rootfs,
                            ["/bin/sh", "-c", command_text],
                            run_env,
                            workdir=workdir or "/"
                        )

                        # Clean up helper script BEFORE after-snapshot so it
                        # doesn't leak into the delta layer
                        self.isolation.cleanup_rootfs_scripts(build_rootfs)

                        if exit_code != 0:
                            raise BuildError(
                                f"RUN command failed with exit code {exit_code}: {command_text}"
                            )

                        # Snapshot after
                        after = snapshot_rootfs(build_rootfs)

                        # Create delta layer
                        tar_bytes = create_run_layer(build_rootfs, before, after)
                        digest = compute_tar_digest(tar_bytes)

                        # Write layer
                        layer_path = self.state.layer_path(digest)
                        with open(layer_path, "wb") as f:
                            f.write(tar_bytes)

                        # Update cache
                        if not self.no_cache:
                            self.cache.store(cache_key, digest)

                        elapsed = time.time() - step_start
                        print(f" [CACHE MISS] {elapsed:.2f}s")

                        layer_size = len(tar_bytes)
                        layers.append({
                            "digest": digest,
                            "size": layer_size,
                            "createdBy": instr.raw_text
                        })
                        prev_layer_digest = digest

        finally:
            # Clean up build rootfs
            try:
                shutil.rmtree(build_rootfs, ignore_errors=True)
            except Exception:
                pass

        # Determine created timestamp
        if all_cache_hits and existing_created:
            created = existing_created
        else:
            created = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Build final env list
        env_list = [f"{k}={v}" for k, v in sorted(env_state.items())]

        # Assemble manifest
        manifest = {
            "name": name,
            "tag": tag,
            "digest": "",  # will be computed by save_image
            "created": created,
            "config": {
                "Env": env_list,
                "Cmd": cmd or [],
                "WorkingDir": workdir or ""
            },
            "layers": layers
        }

        # Save image
        saved = self.store.save_image(manifest)
        return saved

    def _resolve_copy_sources(self, srcs, context_dir):
        """Resolve COPY source patterns to absolute paths."""
        resolved = []
        for src in srcs:
            pattern = os.path.join(context_dir, src)
            matched = sorted(glob_module.glob(pattern, recursive=True))
            for fpath in matched:
                if os.path.isfile(fpath):
                    resolved.append((fpath, os.path.relpath(fpath, context_dir)))
                elif os.path.isdir(fpath):
                    for dp, dn, fn in os.walk(fpath):
                        dn.sort()
                        for fname in sorted(fn):
                            abs_path = os.path.join(dp, fname)
                            rel = os.path.relpath(abs_path, fpath)
                            resolved.append((abs_path, rel))
        resolved.sort(key=lambda x: x[1])
        return resolved

    def _ensure_workdir(self, rootfs_dir, workdir):
        """Silently create workdir in rootfs if it doesn't exist."""
        abs_path = os.path.join(rootfs_dir, workdir.lstrip("/"))
        os.makedirs(abs_path, exist_ok=True)
