"""
State management - handles ~/.docksmith/ directory layout.
"""

import os
import json


class DocksmithState:
    def __init__(self, root=None):
        self.root = root or os.path.expanduser("~/.docksmith")
        self.images_dir = os.path.join(self.root, "images")
        self.layers_dir = os.path.join(self.root, "layers")
        self.cache_dir = os.path.join(self.root, "cache")
        self._init_dirs()

    def _init_dirs(self):
        for d in [self.images_dir, self.layers_dir, self.cache_dir]:
            os.makedirs(d, exist_ok=True)

    # Image manifest paths
    def image_manifest_path(self, name, tag):
        safe_name = name.replace("/", "_")
        return os.path.join(self.images_dir, f"{safe_name}_{tag}.json")

    # Layer paths
    def layer_path(self, digest):
        """Return filesystem path for a layer tar given its digest (sha256:...)."""
        hex_hash = digest.split(":")[-1]
        return os.path.join(self.layers_dir, hex_hash + ".tar")

    # Cache paths
    def cache_index_path(self):
        return os.path.join(self.cache_dir, "index.json")

    def load_cache_index(self):
        path = self.cache_index_path()
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
        return {}

    def save_cache_index(self, index):
        path = self.cache_index_path()
        with open(path, "w") as f:
            json.dump(index, f, indent=2)

    def list_image_files(self):
        files = []
        for fname in os.listdir(self.images_dir):
            if fname.endswith(".json"):
                files.append(os.path.join(self.images_dir, fname))
        return files
