"""
Build cache manager.
Cache keys are deterministic hashes of: previous layer digest, instruction text,
workdir, env state, and (for COPY) source file hashes.
"""

import hashlib
import json
import os


class CacheManager:
    def __init__(self, state):
        self.state = state

    def compute_cache_key(self, prev_layer_digest, instruction_text, workdir, env_state, copy_file_hashes=None):
        """
        Compute a deterministic cache key.
        
        prev_layer_digest: digest of previous layer (or base image manifest digest)
        instruction_text: full instruction text as in Docksmithfile
        workdir: current WORKDIR value (empty string if not set)
        env_state: dict of accumulated ENV values
        copy_file_hashes: for COPY only - dict of {path: sha256} sorted by path
        """
        parts = [
            prev_layer_digest or "",
            instruction_text,
            workdir or "",
        ]

        # ENV state: sorted by key for determinism
        if env_state:
            env_str = "\n".join(f"{k}={v}" for k, v in sorted(env_state.items()))
        else:
            env_str = ""
        parts.append(env_str)

        # COPY file hashes: sorted by path
        if copy_file_hashes:
            file_hash_str = "\n".join(
                f"{path}:{digest}"
                for path, digest in sorted(copy_file_hashes.items())
            )
            parts.append(file_hash_str)

        combined = "\x00".join(parts)
        return "sha256:" + hashlib.sha256(combined.encode()).hexdigest()

    def lookup(self, cache_key):
        """
        Look up a cache key. Returns layer_digest if hit and layer file exists, else None.
        """
        index = self.state.load_cache_index()
        layer_digest = index.get(cache_key)
        if layer_digest:
            layer_path = self.state.layer_path(layer_digest)
            if os.path.exists(layer_path):
                return layer_digest
        return None

    def store(self, cache_key, layer_digest):
        """Store a cache entry."""
        index = self.state.load_cache_index()
        index[cache_key] = layer_digest
        self.state.save_cache_index(index)

    def compute_file_hashes(self, file_paths, context_dir):
        """Compute SHA-256 hashes for a list of absolute file paths."""
        hashes = {}
        for abs_path in file_paths:
            rel = os.path.relpath(abs_path, context_dir)
            try:
                with open(abs_path, "rb") as f:
                    hashes[rel] = hashlib.sha256(f.read()).hexdigest()
            except Exception:
                hashes[rel] = ""
        return hashes
