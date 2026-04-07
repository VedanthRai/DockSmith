"""
Tests for cache key determinism, CACHE HIT/MISS semantics,
cascade invalidation, and --no-cache behaviour.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tempfile
import shutil
import pytest
from docksmith.cache_manager import CacheManager
from docksmith.state import DocksmithState


@pytest.fixture
def tmp_state():
    d = tempfile.mkdtemp()
    state = DocksmithState(root=d)
    yield state
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def cache(tmp_state):
    return CacheManager(tmp_state)


# --- Determinism ---

def test_same_inputs_produce_same_key(cache):
    k1 = cache.compute_cache_key("sha256:abc", "COPY app.py /app/", "/app", {"A": "1"})
    k2 = cache.compute_cache_key("sha256:abc", "COPY app.py /app/", "/app", {"A": "1"})
    assert k1 == k2


def test_different_prev_digest_produces_different_key(cache):
    k1 = cache.compute_cache_key("sha256:aaa", "RUN echo hi", "/app", {})
    k2 = cache.compute_cache_key("sha256:bbb", "RUN echo hi", "/app", {})
    assert k1 != k2


def test_different_instruction_produces_different_key(cache):
    k1 = cache.compute_cache_key("sha256:aaa", "RUN echo hi", "/app", {})
    k2 = cache.compute_cache_key("sha256:aaa", "RUN echo bye", "/app", {})
    assert k1 != k2


def test_different_workdir_produces_different_key(cache):
    k1 = cache.compute_cache_key("sha256:aaa", "RUN echo hi", "/app", {})
    k2 = cache.compute_cache_key("sha256:aaa", "RUN echo hi", "/other", {})
    assert k1 != k2


def test_different_env_produces_different_key(cache):
    k1 = cache.compute_cache_key("sha256:aaa", "RUN echo hi", "/app", {"A": "1"})
    k2 = cache.compute_cache_key("sha256:aaa", "RUN echo hi", "/app", {"A": "2"})
    assert k1 != k2


def test_env_key_order_does_not_matter(cache):
    # ENV state is sorted by key, so insertion order must not affect the key
    k1 = cache.compute_cache_key("sha256:aaa", "RUN x", "", {"B": "2", "A": "1"})
    k2 = cache.compute_cache_key("sha256:aaa", "RUN x", "", {"A": "1", "B": "2"})
    assert k1 == k2


def test_copy_file_hashes_affect_key(cache):
    k1 = cache.compute_cache_key("sha256:aaa", "COPY f /", "", {}, {"f": "hash1"})
    k2 = cache.compute_cache_key("sha256:aaa", "COPY f /", "", {}, {"f": "hash2"})
    assert k1 != k2


def test_copy_file_hash_order_does_not_matter(cache):
    k1 = cache.compute_cache_key("sha256:aaa", "COPY . /", "", {}, {"b.py": "h2", "a.py": "h1"})
    k2 = cache.compute_cache_key("sha256:aaa", "COPY . /", "", {}, {"a.py": "h1", "b.py": "h2"})
    assert k1 == k2


# --- HIT / MISS semantics ---

def test_miss_when_no_entry(cache):
    key = cache.compute_cache_key("sha256:aaa", "RUN echo hi", "", {})
    assert cache.lookup(key) is None


def test_hit_after_store(cache, tmp_state):
    # Write a fake layer file so the existence check passes
    digest = "sha256:" + "a" * 64
    layer_path = tmp_state.layer_path(digest)
    with open(layer_path, "wb") as f:
        f.write(b"fake tar")

    key = cache.compute_cache_key("sha256:aaa", "RUN echo hi", "", {})
    cache.store(key, digest)
    assert cache.lookup(key) == digest


def test_miss_when_layer_file_missing(cache, tmp_state):
    digest = "sha256:" + "b" * 64
    # Store cache entry but do NOT write the layer file
    key = cache.compute_cache_key("sha256:aaa", "RUN echo hi", "", {})
    cache.store(key, digest)
    # Should be a miss because the layer file doesn't exist on disk
    assert cache.lookup(key) is None


def test_store_is_persistent(cache, tmp_state):
    digest = "sha256:" + "c" * 64
    layer_path = tmp_state.layer_path(digest)
    with open(layer_path, "wb") as f:
        f.write(b"data")

    key = cache.compute_cache_key("sha256:aaa", "RUN x", "", {})
    cache.store(key, digest)

    # Create a fresh CacheManager pointing at the same state
    cache2 = CacheManager(tmp_state)
    assert cache2.lookup(key) == digest


# --- compute_file_hashes ---

def test_file_hashes_deterministic(tmp_path):
    f = tmp_path / "hello.py"
    f.write_bytes(b"print('hello')")
    state = DocksmithState(root=str(tmp_path / "state"))
    cm = CacheManager(state)
    h1 = cm.compute_file_hashes([str(f)], str(tmp_path))
    h2 = cm.compute_file_hashes([str(f)], str(tmp_path))
    assert h1 == h2


def test_file_hashes_change_on_content_change(tmp_path):
    f = tmp_path / "hello.py"
    f.write_bytes(b"v1")
    state = DocksmithState(root=str(tmp_path / "state"))
    cm = CacheManager(state)
    h1 = cm.compute_file_hashes([str(f)], str(tmp_path))
    f.write_bytes(b"v2")
    h2 = cm.compute_file_hashes([str(f)], str(tmp_path))
    assert h1 != h2
