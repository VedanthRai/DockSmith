"""
Layer builder - creates content-addressed tar layers.
Layers are delta tars (only files added/modified by this step).
IMPORTANT: Reproducible tars - sorted entries, zeroed timestamps.
"""

import os
import io
import tarfile
import hashlib
import glob as glob_module
import stat


def _collect_files(pattern, context_dir):
    """Resolve glob pattern. If path is a directory, walk it recursively."""
    full_pattern = os.path.join(context_dir, pattern)
    matched = sorted(glob_module.glob(full_pattern, recursive=True))

    files = []
    for fpath in matched:
        if os.path.isfile(fpath):
            files.append((fpath, os.path.relpath(fpath, context_dir)))
        elif os.path.isdir(fpath):
            # rel is relative to fpath itself so archive name = dest + filename only
            # e.g. COPY data /app/data -> rel = "info.txt" -> dest = "/app/data/info.txt"
            for dp, dn, fn in os.walk(fpath):
                dn.sort()
                for fname in sorted(fn):
                    abs_path = os.path.join(dp, fname)
                    rel = os.path.relpath(abs_path, fpath)
                    files.append((abs_path, rel))
    return files


def create_copy_layer(src_patterns, dest_path, context_dir, rootfs_dir):
    """
    Create a tar layer for a COPY instruction.
    Returns: (tar_bytes, list_of_relative_src_paths)
    """
    all_files = []
    for pattern in src_patterns:
        all_files.extend(_collect_files(pattern, context_dir))

    if not all_files:
        raise FileNotFoundError(f"No files matched COPY sources: {src_patterns}")

    # Sort for reproducibility
    all_files = sorted(set(all_files), key=lambda x: x[1])

    # Determine if we're copying into a directory
    is_dir_dest = dest_path.endswith("/") or len(all_files) > 1

    buf = io.BytesIO()
    added_dirs = set()

    with tarfile.open(fileobj=buf, mode="w") as tar:
        for abs_src, rel_src in all_files:
            # Determine archive path
            if is_dir_dest:
                archive_name = dest_path.rstrip("/") + "/" + rel_src
            else:
                archive_name = dest_path
            archive_name = archive_name.lstrip("/")

            # Add parent directories
            _add_parent_dirs(tar, archive_name, added_dirs)

            # Add the file
            with open(abs_src, "rb") as f:
                file_data = f.read()

            info = tarfile.TarInfo(name=archive_name)
            info.size = len(file_data)
            info.mtime = 0
            info.mode = 0o644
            info.uid = 0
            info.gid = 0
            info.type = tarfile.REGTYPE
            tar.addfile(info, io.BytesIO(file_data))

    tar_bytes = buf.getvalue()
    return tar_bytes, [r for _, r in all_files]


def _add_parent_dirs(tar, archive_name, added_dirs):
    """Add parent directories to tar if not already added."""
    parts = archive_name.split("/")
    for depth in range(1, len(parts)):
        dir_path = "/".join(parts[:depth])
        if dir_path and dir_path not in added_dirs:
            dir_info = tarfile.TarInfo(name=dir_path)
            dir_info.type = tarfile.DIRTYPE
            dir_info.mode = 0o755
            dir_info.mtime = 0
            dir_info.uid = 0
            dir_info.gid = 0
            tar.addfile(dir_info)
            added_dirs.add(dir_path)


def create_run_layer(rootfs_dir, before_snapshot, after_snapshot):
    """
    Create a delta tar layer: files added/changed get their content included;
    files deleted get a whiteout entry (.wh.<name>) per OCI convention.
    """
    changed_paths = []
    for path, (mtime, size, digest) in after_snapshot.items():
        if path not in before_snapshot or before_snapshot[path][2] != digest:
            changed_paths.append(path)
    changed_paths.sort()

    # Deleted paths: present in before but gone from after
    deleted_paths = sorted(
        path for path in before_snapshot if path not in after_snapshot
    )

    buf = io.BytesIO()
    added_dirs = set()

    with tarfile.open(fileobj=buf, mode="w") as tar:
        # Whiteout entries for deleted files/dirs
        for rel_path in deleted_paths:
            parent = os.path.dirname(rel_path).lstrip("/")
            basename = os.path.basename(rel_path)
            wh_name = (parent + "/.wh." + basename).lstrip("/")
            _add_parent_dirs(tar, wh_name, added_dirs)
            info = tarfile.TarInfo(name=wh_name)
            info.size = 0
            info.mtime = 0
            info.mode = 0o644
            info.uid = 0
            info.gid = 0
            tar.addfile(info, io.BytesIO(b""))

        # Added/changed entries
        for rel_path in changed_paths:
            abs_path = os.path.join(rootfs_dir, rel_path.lstrip("/"))
            if not os.path.exists(abs_path):
                continue

            archive_name = rel_path.lstrip("/")

            if os.path.isdir(abs_path):
                if archive_name not in added_dirs:
                    dir_info = tarfile.TarInfo(name=archive_name)
                    dir_info.type = tarfile.DIRTYPE
                    dir_info.mode = 0o755
                    dir_info.mtime = 0
                    dir_info.uid = 0
                    dir_info.gid = 0
                    tar.addfile(dir_info)
                    added_dirs.add(archive_name)
            elif os.path.isfile(abs_path):
                _add_parent_dirs(tar, archive_name, added_dirs)
                with open(abs_path, "rb") as f:
                    file_data = f.read()
                info = tarfile.TarInfo(name=archive_name)
                info.size = len(file_data)
                info.mtime = 0
                info.mode = os.stat(abs_path).st_mode & 0o7777
                info.uid = 0
                info.gid = 0
                tar.addfile(info, io.BytesIO(file_data))

    return buf.getvalue()


def snapshot_rootfs(rootfs_dir):
    """
    Snapshot current state of rootfs.
    Returns dict of {relative_path: (mtime, size, sha256)}
    """
    snapshot = {}
    for dirpath, dirnames, filenames in os.walk(rootfs_dir):
        dirnames.sort()
        for fname in sorted(filenames):
            abs_path = os.path.join(dirpath, fname)
            rel_path = "/" + os.path.relpath(abs_path, rootfs_dir)
            try:
                st = os.stat(abs_path)
                with open(abs_path, "rb") as f:
                    content = f.read()
                digest = hashlib.sha256(content).hexdigest()
                snapshot[rel_path] = (st.st_mtime, st.st_size, digest)
            except (PermissionError, OSError):
                pass
        for dname in sorted(dirnames):
            abs_path = os.path.join(dirpath, dname)
            rel_path = "/" + os.path.relpath(abs_path, rootfs_dir)
            snapshot[rel_path] = (0, 0, "dir")
    return snapshot


def compute_tar_digest(tar_bytes):
    return "sha256:" + hashlib.sha256(tar_bytes).hexdigest()


def extract_layer(tar_bytes_or_path, dest_dir):
    """Extract a layer tar into dest_dir, safely."""
    os.makedirs(dest_dir, exist_ok=True)
    if isinstance(tar_bytes_or_path, bytes):
        buf = io.BytesIO(tar_bytes_or_path)
        with tarfile.open(fileobj=buf, mode="r") as tar:
            _safe_extract(tar, dest_dir)
    else:
        with tarfile.open(tar_bytes_or_path, mode="r") as tar:
            _safe_extract(tar, dest_dir)


def _safe_extract(tar, dest_dir):
    real_dest = os.path.realpath(dest_dir)
    for member in tar.getmembers():
        member_path = os.path.normpath(member.name)
        if member_path.startswith(".."):
            continue
        abs_path = os.path.join(dest_dir, member_path)
        if not os.path.realpath(abs_path).startswith(real_dest):
            continue

        # Handle OCI whiteout entries: .wh.<name> means delete <name>
        basename = os.path.basename(member_path)
        if basename.startswith(".wh."):
            target_name = basename[4:]
            target_path = os.path.join(os.path.dirname(abs_path), target_name)
            if os.path.isdir(target_path):
                import shutil as _shutil
                _shutil.rmtree(target_path, ignore_errors=True)
            elif os.path.lexists(target_path):
                os.remove(target_path)
            continue

        if member.isdir():
            os.makedirs(abs_path, exist_ok=True)
        elif member.isfile():
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            f = tar.extractfile(member)
            if f is not None:
                with open(abs_path, "wb") as out:
                    out.write(f.read())
                try:
                    os.chmod(abs_path, member.mode)
                except Exception:
                    pass
        elif member.issym():
            os.makedirs(os.path.dirname(abs_path), exist_ok=True)
            if os.path.lexists(abs_path):
                os.remove(abs_path)
            try:
                os.symlink(member.linkname, abs_path)
            except Exception:
                pass
