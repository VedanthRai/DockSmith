"""
Image store - manages image manifests in ~/.docksmith/images/.
"""

import os
import json
import hashlib


class ImageStore:
    def __init__(self, state):
        self.state = state

    def get_image(self, name, tag):
        path = self.state.image_manifest_path(name, tag)
        if not os.path.exists(path):
            raise FileNotFoundError(f"Image '{name}:{tag}' not found in local store")
        with open(path, "r") as f:
            return json.load(f)

    def save_image(self, manifest):
        name = manifest["name"]
        tag = manifest["tag"]
        path = self.state.image_manifest_path(name, tag)

        # Compute digest: serialize canonically with digest="" then hash
        manifest_for_hash = dict(manifest)
        manifest_for_hash["digest"] = ""
        canonical = json.dumps(manifest_for_hash, sort_keys=True, separators=(",", ":"))
        digest = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
        manifest["digest"] = digest

        # Write to disk in the same canonical key order so the file is consistent
        with open(path, "w") as f:
            json.dump(manifest, f, indent=2, sort_keys=True)
        return manifest

    def list_images(self):
        images = []
        for fpath in self.state.list_image_files():
            try:
                with open(fpath, "r") as f:
                    images.append(json.load(f))
            except Exception:
                pass
        images.sort(key=lambda x: x.get("created", ""))
        return images

    def remove_image(self, name, tag):
        path = self.state.image_manifest_path(name, tag)
        if not os.path.exists(path):
            raise FileNotFoundError(f"Image '{name}:{tag}' not found")

        with open(path, "r") as f:
            manifest = json.load(f)

        # Collect digests still referenced by OTHER images
        referenced = set()
        for fpath in self.state.list_image_files():
            if os.path.abspath(fpath) == os.path.abspath(path):
                continue
            try:
                with open(fpath, "r") as f:
                    other = json.load(f)
                for layer in other.get("layers", []):
                    referenced.add(layer["digest"])
            except Exception:
                pass

        # Only delete layers not used by any other image
        for layer in manifest.get("layers", []):
            if layer["digest"] not in referenced:
                layer_path = self.state.layer_path(layer["digest"])
                if os.path.exists(layer_path):
                    os.remove(layer_path)

        # Remove manifest
        os.remove(path)

        # Clean up cache entries for removed layers
        self._cleanup_cache(manifest, referenced)

    def _cleanup_cache(self, manifest, keep_digests=None):
        index = self.state.load_cache_index()
        layer_digests = {l["digest"] for l in manifest.get("layers", [])}
        if keep_digests:
            layer_digests -= keep_digests
        changed = False
        for key in list(index.keys()):
            if index[key] in layer_digests:
                del index[key]
                changed = True
        if changed:
            self.state.save_cache_index(index)
