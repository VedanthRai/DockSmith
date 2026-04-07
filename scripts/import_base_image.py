#!/usr/bin/env python3
"""
docksmith-import: Import base images into the Docksmith local store.

Usage:
  python3 scripts/import_base_image.py [--image alpine|busybox|python3]

This script imports a minimal Linux base image from a local tar or from
the system's Alpine/BusyBox installation. It does NOT require network access
at build/run time.

For WSL users: run this once before your first build.
"""

import sys
import os
import io
import json
import tarfile
import hashlib
import datetime
import datetime as _dt
import shutil
import subprocess
import argparse
import tempfile

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from docksmith.state import DocksmithState
from docksmith.image_store import ImageStore


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def create_minimal_rootfs_tar():
    """
    Create a minimal busybox-based rootfs tar.
    Uses the system's busybox or sh to build a working minimal image.
    """
    buf = io.BytesIO()
    added = set()

    def add_dir(tar, path):
        if path in added or path == "":
            return
        # Add parent first (sorted traversal for reproducibility)
        parent = os.path.dirname(path)
        if parent and parent != path:
            add_dir(tar, parent)
        if path in added:  # may have been added by parent recursion
            return
        info = tarfile.TarInfo(name=path)
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        info.mtime = 0
        info.uid = 0
        info.gid = 0
        tar.addfile(info)
        added.add(path)

    def add_file(tar, archive_name, content_bytes, mode=0o644):
        parent = os.path.dirname(archive_name)
        if parent:
            add_dir(tar, parent)
        info = tarfile.TarInfo(name=archive_name)
        info.size = len(content_bytes)
        info.mode = mode
        info.mtime = 0
        info.uid = 0
        info.gid = 0
        tar.addfile(info, io.BytesIO(content_bytes))
        added.add(archive_name)

    def add_host_file(tar, archive_name, host_path, mode=None):
        with open(host_path, "rb") as f:
            data = f.read()
        if mode is None:
            mode = os.stat(host_path).st_mode & 0o7777
        add_file(tar, archive_name, data, mode)

    def add_host_tree(tar, archive_prefix, host_dir):
        """Recursively add a host directory into the tar."""
        for dirpath, dirnames, filenames in os.walk(host_dir):
            dirnames.sort()
            rel = os.path.relpath(dirpath, host_dir)
            arc_dir = archive_prefix.rstrip("/") + ("/" + rel if rel != "." else "")
            arc_dir = arc_dir.strip("/")
            if arc_dir:
                add_dir(tar, arc_dir)
            for fname in sorted(filenames):
                abs_path = os.path.join(dirpath, fname)
                arc_path = arc_dir + "/" + fname if arc_dir else fname
                try:
                    add_host_file(tar, arc_path, abs_path)
                except Exception:
                    pass

    with tarfile.open(fileobj=buf, mode="w") as tar:
        # Core directories
        for d in ["bin", "sbin", "usr", "usr/bin", "usr/sbin", "usr/local",
                  "usr/local/bin", "lib", "lib64", "etc", "tmp", "var",
                  "var/tmp", "proc", "sys", "dev", "root", "home",
                  "usr/lib", "usr/lib/x86_64-linux-gnu"]:
            add_dir(tar, d)

        # /etc/passwd and /etc/group
        add_file(tar, "etc/passwd",
                 b"root:x:0:0:root:/root:/bin/sh\nnobody:x:65534:65534:nobody:/:/bin/false\n")
        add_file(tar, "etc/group",
                 b"root:x:0:\nnogroup:x:65534:\n")
        add_file(tar, "etc/hostname", b"container\n")
        add_file(tar, "etc/hosts",
                 b"127.0.0.1\tlocalhost\n::1\tlocalhost\n")

        # Copy sh
        sh_path = "/bin/sh"
        if os.path.exists(sh_path):
            add_host_file(tar, "bin/sh", sh_path, 0o755)

        # Copy key binaries from the host
        bins_to_copy = [
            ("/bin/echo", "bin/echo"),
            ("/bin/ls", "bin/ls"),
            ("/bin/cat", "bin/cat"),
            ("/bin/mkdir", "bin/mkdir"),
            ("/bin/chmod", "bin/chmod"),
            ("/bin/cp", "bin/cp"),
            ("/bin/mv", "bin/mv"),
            ("/bin/rm", "bin/rm"),
            ("/bin/env", "bin/env"),
            ("/usr/bin/env", "usr/bin/env"),
            ("/bin/true", "bin/true"),
            ("/bin/false", "bin/false"),
            ("/bin/grep", "bin/grep"),
            ("/bin/sed", "bin/sed"),
            ("/bin/awk", "usr/bin/awk"),
            ("/usr/bin/awk", "usr/bin/awk"),
            ("/bin/sort", "bin/sort"),
            ("/usr/bin/sort", "usr/bin/sort"),
            ("/bin/head", "bin/head"),
            ("/bin/tail", "bin/tail"),
            ("/usr/bin/python3", "usr/bin/python3"),
            ("/usr/bin/python3.12", "usr/bin/python3.12"),
        ]

        for host_path, arc_name in bins_to_copy:
            if os.path.exists(host_path) and arc_name not in added:
                try:
                    add_host_file(tar, arc_name, host_path, 0o755)
                except Exception:
                    pass

        # Copy required shared libraries (ldd-based)
        libs_needed = set()
        for host_path, _ in bins_to_copy:
            if os.path.exists(host_path):
                try:
                    result = subprocess.run(
                        ["ldd", host_path], capture_output=True, text=True
                    )
                    for line in result.stdout.splitlines():
                        parts = line.strip().split()
                        for p in parts:
                            if p.startswith("/") and os.path.exists(p):
                                libs_needed.add(p)
                        # Handle "libname => /path"
                        if "=>" in line:
                            idx = line.index("=>")
                            after = line[idx+2:].strip().split()[0] if line[idx+2:].strip() else ""
                            if after.startswith("/") and os.path.exists(after):
                                libs_needed.add(after)
                except Exception:
                    pass

        # Also add ld-linux
        for pattern in ["/lib64/ld-linux-x86-64.so.2", "/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"]:
            if os.path.exists(pattern):
                libs_needed.add(pattern)

        for lib_path in sorted(libs_needed):
            # Determine archive name
            if lib_path.startswith("/lib64/"):
                arc_name = lib_path[1:]  # strip leading /
            elif lib_path.startswith("/lib/"):
                arc_name = lib_path[1:]
            elif lib_path.startswith("/usr/lib/"):
                arc_name = lib_path[1:]
            else:
                arc_name = lib_path[1:]

            if arc_name not in added:
                try:
                    # Resolve symlinks for actual content
                    real_path = os.path.realpath(lib_path)
                    if os.path.isfile(real_path):
                        add_host_file(tar, arc_name, real_path, 0o755)
                        # Add symlink if needed
                        if real_path != lib_path and not os.path.basename(lib_path) == os.path.basename(real_path):
                            pass  # Skip complex symlink handling for now
                except Exception:
                    pass

        # Add python3 stdlib if python3 is being included
        python3_path = "/usr/lib/python3"
        python_ver_path = "/usr/lib/python3.12"
        for py_path in [python_ver_path, python3_path]:
            if os.path.isdir(py_path):
                arc_prefix = py_path[1:]  # strip leading /
                if arc_prefix not in added:
                    print(f"  Adding Python stdlib from {py_path}...")
                    add_host_tree(tar, arc_prefix, py_path)
                break

    return buf.getvalue()


def import_minimal_image(state, name="alpine", tag="3.18"):
    """Import a minimal Linux base image into the store."""
    store = ImageStore(state)

    # Check if already imported
    try:
        existing = store.get_image(name, tag)
        print(f"Image '{name}:{tag}' already exists (digest: {existing['digest'][:19]}...)")
        return existing
    except FileNotFoundError:
        pass

    print(f"Creating minimal base image '{name}:{tag}'...")
    print("  Collecting binaries and libraries from host system...")

    tar_bytes = create_minimal_rootfs_tar()

    digest = sha256_bytes(tar_bytes)
    layer_path = state.layer_path(digest)

    print(f"  Writing layer ({len(tar_bytes) // 1024} KB)...")
    with open(layer_path, "wb") as f:
        f.write(tar_bytes)

    manifest = {
        "name": name,
        "tag": tag,
        "digest": "",
        "created": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "config": {
            "Env": ["PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"],
            "Cmd": ["/bin/sh"],
            "WorkingDir": ""
        },
        "layers": [
            {
                "digest": digest,
                "size": len(tar_bytes),
                "createdBy": f"import {name}:{tag}"
            }
        ]
    }

    saved = store.save_image(manifest)
    print(f"  Imported '{name}:{tag}' successfully!")
    print(f"  Digest: {saved['digest'][:19]}...")
    return saved


def import_python_image(state, name="python", tag="3.12-slim"):
    """Import a Python-capable base image."""
    store = ImageStore(state)

    try:
        existing = store.get_image(name, tag)
        print(f"Image '{name}:{tag}' already exists (digest: {existing['digest'][:19]}...)")
        return existing
    except FileNotFoundError:
        pass

    # For python image, reuse the minimal image creation (it already includes python3)
    print(f"Creating base image '{name}:{tag}' with Python 3...")
    tar_bytes = create_minimal_rootfs_tar()

    digest = "sha256:" + hashlib.sha256(tar_bytes).hexdigest()
    layer_path = state.layer_path(digest)

    print(f"  Writing layer ({len(tar_bytes) // 1024} KB)...")
    with open(layer_path, "wb") as f:
        f.write(tar_bytes)

    manifest = {
        "name": name,
        "tag": tag,
        "digest": "",
        "created": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "config": {
            "Env": [
                "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "PYTHON_VERSION=3.12"
            ],
            "Cmd": ["python3"],
            "WorkingDir": ""
        },
        "layers": [
            {
                "digest": digest,
                "size": len(tar_bytes),
                "createdBy": f"import {name}:{tag}"
            }
        ]
    }

    saved = store.save_image(manifest)
    print(f"  Imported '{name}:{tag}' successfully!")
    print(f"  Digest: {saved['digest'][:19]}...")
    return saved


def main():
    parser = argparse.ArgumentParser(description="Import base images into Docksmith local store")
    parser.add_argument("--image", choices=["alpine", "python3", "all"],
                        default="all", help="Which base image to import")
    parser.add_argument("--name", help="Custom image name")
    parser.add_argument("--tag", help="Custom image tag")
    args = parser.parse_args()

    state = DocksmithState()
    print(f"Docksmith image store: {state.root}")
    print()

    if args.image in ("alpine", "all"):
        name = args.name or "alpine"
        tag = args.tag or "3.18"
        import_minimal_image(state, name, tag)
        print()

    if args.image in ("python3", "all"):
        name = args.name or "python"
        tag = args.tag or "3.12-slim"
        import_python_image(state, name, tag)
        print()

    print("Import complete. You can now run: docksmith build")


if __name__ == "__main__":
    main()
