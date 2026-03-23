"""
Container runtime - assembles image filesystem and runs containers.
Uses the SAME isolation primitive as the build engine's RUN instruction.
"""

import os
import tempfile
import shutil

from .layer_builder import extract_layer
from .isolation import IsolationEngine


class ContainerRuntime:
    def __init__(self, state):
        self.state = state
        self.isolation = IsolationEngine()

    def run(self, image, cmd_override=None, env_overrides=None):
        """
        Assemble image filesystem, run container, return exit code.
        
        image: image manifest dict
        cmd_override: optional command list to override CMD
        env_overrides: optional dict of env var overrides
        """
        config = image.get("config", {})
        layers = image.get("layers", [])

        # Determine command
        if cmd_override:
            if isinstance(cmd_override, list) and len(cmd_override) == 1:
                command = ["/bin/sh", "-c", cmd_override[0]]
            elif isinstance(cmd_override, list):
                command = cmd_override
            else:
                command = ["/bin/sh", "-c", cmd_override]
        else:
            command = config.get("Cmd") or []

        if not command:
            raise ValueError(
                f"No CMD defined in image '{image['name']}:{image['tag']}' "
                "and no command provided at runtime."
            )

        # Build environment: image ENV + overrides
        env = {}
        for env_str in config.get("Env", []):
            if "=" in env_str:
                k, v = env_str.split("=", 1)
                env[k] = v
        if env_overrides:
            env.update(env_overrides)

        # Working directory
        workdir = config.get("WorkingDir") or "/"

        # Assemble rootfs in a temp directory
        rootfs = tempfile.mkdtemp(prefix="docksmith_run_")
        print(f"Assembling image filesystem ({len(layers)} layers)...")

        try:
            for i, layer_meta in enumerate(layers):
                layer_path = self.state.layer_path(layer_meta["digest"])
                if not os.path.exists(layer_path):
                    raise FileNotFoundError(
                        f"Layer {layer_meta['digest'][:16]}... is missing. "
                        f"Image may be corrupted."
                    )
                extract_layer(layer_path, rootfs)

            print(f"Starting container...")
            print(f"{'='*60}")

            exit_code = self.isolation.run_isolated(
                rootfs,
                command,
                env,
                workdir=workdir
            )

            print(f"{'='*60}")
            return exit_code

        finally:
            # Always clean up rootfs (this is the isolation guarantee)
            self.isolation.cleanup_rootfs(rootfs)
            # Final verification: rootfs should not exist
            if os.path.exists(rootfs):
                shutil.rmtree(rootfs, ignore_errors=True)
