"""
Tests for layer builder:
- COPY layer reproducibility (same input → identical digest)
- RUN delta layer: added, changed, deleted files
- Whiteout entries for deleted files
- extract_layer applies whiteouts correctly
- Tar entries have zeroed timestamps
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import io
import tarfile
import tempfile
import shutil
import pytest

from docksmith.layer_builder import (
    create_copy_layer, create_run_layer, compute_tar_digest,
    extract_layer, snapshot_rootfs,
)


@pytest.fixture
def ctx(tmp_path):
    """A minimal build context with one file and one subdir."""
    (tmp_path / "app.py").write_bytes(b"print('hello')")
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "info.txt").write_bytes(b"info")
    return tmp_path


@pytest.fixture
def rootfs(tmp_path):
    d = tmp_path / "rootfs"
    d.mkdir()
    return d


# --- COPY layer reproducibility ---

def test_copy_layer_reproducible(ctx, rootfs):
    b1, _ = create_copy_layer(["app.py"], "/app/app.py", str(ctx), str(rootfs))
    b2, _ = create_copy_layer(["app.py"], "/app/app.py", str(ctx), str(rootfs))
    assert compute_tar_digest(b1) == compute_tar_digest(b2)


def test_copy_layer_changes_when_content_changes(ctx, rootfs):
    b1, _ = create_copy_layer(["app.py"], "/app/app.py", str(ctx), str(rootfs))
    (ctx / "app.py").write_bytes(b"print('changed')")
    b2, _ = create_copy_layer(["app.py"], "/app/app.py", str(ctx), str(rootfs))
    assert compute_tar_digest(b1) != compute_tar_digest(b2)


def test_copy_layer_timestamps_zeroed(ctx, rootfs):
    tar_bytes, _ = create_copy_layer(["app.py"], "/app/app.py", str(ctx), str(rootfs))
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        for m in tar.getmembers():
            assert m.mtime == 0, f"{m.name} has non-zero mtime {m.mtime}"


def test_copy_layer_entries_sorted(ctx, rootfs):
    tar_bytes, _ = create_copy_layer(["data"], "/app/data/", str(ctx), str(rootfs))
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        names = [m.name for m in tar.getmembers()]
    assert names == sorted(names)


def test_copy_no_match_raises(ctx, rootfs):
    with pytest.raises(FileNotFoundError):
        create_copy_layer(["nonexistent.py"], "/app/", str(ctx), str(rootfs))


# --- RUN delta layer ---

def test_run_layer_captures_new_file(tmp_path):
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    before = snapshot_rootfs(str(rootfs))
    (rootfs / "newfile.txt").write_bytes(b"created")
    after = snapshot_rootfs(str(rootfs))

    tar_bytes = create_run_layer(str(rootfs), before, after)
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        names = [m.name for m in tar.getmembers()]
    assert "newfile.txt" in names


def test_run_layer_captures_modified_file(tmp_path):
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    (rootfs / "file.txt").write_bytes(b"v1")
    before = snapshot_rootfs(str(rootfs))
    (rootfs / "file.txt").write_bytes(b"v2")
    after = snapshot_rootfs(str(rootfs))

    tar_bytes = create_run_layer(str(rootfs), before, after)
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        names = [m.name for m in tar.getmembers()]
    assert "file.txt" in names


def test_run_layer_unchanged_file_not_included(tmp_path):
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    (rootfs / "unchanged.txt").write_bytes(b"same")
    before = snapshot_rootfs(str(rootfs))
    after = snapshot_rootfs(str(rootfs))

    tar_bytes = create_run_layer(str(rootfs), before, after)
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        names = [m.name for m in tar.getmembers()]
    assert "unchanged.txt" not in names


def test_run_layer_timestamps_zeroed(tmp_path):
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    before = snapshot_rootfs(str(rootfs))
    (rootfs / "f.txt").write_bytes(b"data")
    after = snapshot_rootfs(str(rootfs))

    tar_bytes = create_run_layer(str(rootfs), before, after)
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        for m in tar.getmembers():
            assert m.mtime == 0


# --- Deletion / whiteout ---

def test_run_layer_deleted_file_produces_whiteout(tmp_path):
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    (rootfs / "todelete.txt").write_bytes(b"bye")
    before = snapshot_rootfs(str(rootfs))
    os.remove(str(rootfs / "todelete.txt"))
    after = snapshot_rootfs(str(rootfs))

    tar_bytes = create_run_layer(str(rootfs), before, after)
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        names = [m.name for m in tar.getmembers()]
    assert ".wh.todelete.txt" in names


def test_extract_layer_applies_whiteout(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    # Put a file in dest that should be deleted
    (dest / "victim.txt").write_bytes(b"should be gone")

    # Build a tar with a whiteout entry
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=".wh.victim.txt")
        info.size = 0
        info.mtime = 0
        tar.addfile(info, io.BytesIO(b""))
    buf.seek(0)

    extract_layer(buf.getvalue(), str(dest))
    assert not (dest / "victim.txt").exists()
    assert not (dest / ".wh.victim.txt").exists()


def test_run_layer_deletion_roundtrip(tmp_path):
    """Full roundtrip: file exists, RUN rm, layer applied, file gone."""
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    (rootfs / "removeme.txt").write_bytes(b"data")

    before = snapshot_rootfs(str(rootfs))
    os.remove(str(rootfs / "removeme.txt"))
    after = snapshot_rootfs(str(rootfs))

    tar_bytes = create_run_layer(str(rootfs), before, after)

    # Re-add the file to simulate a fresh rootfs that has the file
    dest = tmp_path / "apply"
    dest.mkdir()
    (dest / "removeme.txt").write_bytes(b"data")

    extract_layer(tar_bytes, str(dest))
    assert not (dest / "removeme.txt").exists()


# --- Digest reproducibility ---

def test_digest_reproducible_across_calls(ctx, rootfs):
    b1, _ = create_copy_layer(["app.py"], "/app/app.py", str(ctx), str(rootfs))
    b2, _ = create_copy_layer(["app.py"], "/app/app.py", str(ctx), str(rootfs))
    assert compute_tar_digest(b1) == compute_tar_digest(b2)
