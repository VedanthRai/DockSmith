"""
Isolation engine - Linux process isolation using namespaces.
Uses unshare + chroot for container isolation.

The SAME primitive is used for both RUN during build and docksmith run.
Verified isolation: files written inside a container do NOT appear on the host.
"""

import os
import subprocess
import shutil

HELPER_SCRIPT_NAME = ".docksmith_run.sh"


class IsolationEngine:
    """
    Process isolation using Linux namespaces (unshare) + chroot.
    Works without root via --map-root-user (user namespace).
    """

    def run_isolated(self, rootfs_dir, command, env_vars, workdir="/", capture_output=False):
        """
        Run command in an isolated environment with rootfs_dir as root filesystem.

        Args:
            rootfs_dir: path to assembled rootfs on host
            command: list of strings [executable, arg1, arg2, ...]
            env_vars: dict of environment variables to inject
            workdir: working directory inside rootfs (must start with /)
            capture_output: if True, return (exit_code, stdout, stderr)

        Returns:
            exit_code (int) if capture_output=False
            (exit_code, stdout_bytes, stderr_bytes) if capture_output=True
        """
        abs_workdir = workdir if workdir and workdir.startswith("/") else ("/" + (workdir or ""))

        _ensure_basic_dirs(rootfs_dir)

        script_content = _build_exec_script(command, env_vars, abs_workdir)
        script_host_path = os.path.join(rootfs_dir, HELPER_SCRIPT_NAME)

        with open(script_host_path, "w") as f:
            f.write(script_content)
        os.chmod(script_host_path, 0o755)

        # unshare creates: new mount namespace + UTS namespace + user namespace (map-root-user)
        # chroot changes the visible root filesystem to rootfs_dir
        isolate_cmd = [
            "unshare",
            "--mount",
            "--uts",
            "--map-root-user",
            "--fork",
            "--",
            "chroot",
            rootfs_dir,
            "/bin/sh",
            "/" + HELPER_SCRIPT_NAME,
        ]

        if capture_output:
            result = subprocess.run(isolate_cmd, capture_output=True)
            return result.returncode, result.stdout, result.stderr
        else:
            result = subprocess.run(isolate_cmd)
            return result.returncode

    def cleanup_rootfs_scripts(self, rootfs_dir):
        """Remove helper script from rootfs after build RUN step."""
        script = os.path.join(rootfs_dir, HELPER_SCRIPT_NAME)
        if os.path.exists(script):
            try:
                os.remove(script)
            except Exception:
                pass

    def cleanup_rootfs(self, rootfs_dir):
        """Fully remove the temporary rootfs. Isolation guarantee for run command."""
        self.cleanup_rootfs_scripts(rootfs_dir)
        try:
            subprocess.run(["rm", "-rf", rootfs_dir], check=False,
                           capture_output=True, timeout=30)
        except Exception:
            shutil.rmtree(rootfs_dir, ignore_errors=True)


def _build_exec_script(command, env_vars, workdir):
    """Build a POSIX shell script that sets env and execs the command."""
    lines = ["#!/bin/sh"]

    for key, value in env_vars.items():
        if not _valid_env_key(key):
            continue
        safe_value = value.replace("'", "'\"'\"'")
        lines.append(f"export {key}='{safe_value}'")

    safe_workdir = workdir.replace("'", "'\"'\"'")
    lines.append(f"cd '{safe_workdir}' 2>/dev/null || cd /")

    cmd_parts = []
    for part in command:
        safe = part.replace("'", "'\"'\"'")
        cmd_parts.append(f"'{safe}'")
    lines.append(f"exec {' '.join(cmd_parts)}")

    return "\n".join(lines) + "\n"


def _valid_env_key(key):
    if not key:
        return False
    if not (key[0].isalpha() or key[0] == "_"):
        return False
    return all(c.isalnum() or c == "_" for c in key)


def _ensure_basic_dirs(rootfs_dir):
    for d in ["proc", "sys", "dev", "tmp", "etc", "root"]:
        try:
            os.makedirs(os.path.join(rootfs_dir, d), exist_ok=True)
        except Exception:
            pass

    passwd_path = os.path.join(rootfs_dir, "etc", "passwd")
    if not os.path.exists(passwd_path):
        try:
            with open(passwd_path, "w") as f:
                f.write("root:x:0:0:root:/root:/bin/sh\nnobody:x:65534:65534:nobody:/:/usr/sbin/nologin\n")
        except Exception:
            pass

    group_path = os.path.join(rootfs_dir, "etc", "group")
    if not os.path.exists(group_path):
        try:
            with open(group_path, "w") as f:
                f.write("root:x:0:\nnogroup:x:65534:\n")
        except Exception:
            pass
