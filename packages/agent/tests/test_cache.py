from __future__ import annotations

import hashlib
import json
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from grackle.cache import CacheManager, _hash_file

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _make_file(path: Path, content: bytes = b"hello") -> Path:
    path.write_bytes(content)
    return path


# ---------------------------------------------------------------------------
# hash_file
# ---------------------------------------------------------------------------


def test_hash_file_stable(tmp_path: Path) -> None:
    f = _make_file(tmp_path / "a.py", b"print('hello')")
    h1 = _hash_file(f)
    h2 = _hash_file(f)
    assert h1 == h2
    assert h1 == _sha256(b"print('hello')")


def test_hash_file_changes_when_content_changes(tmp_path: Path) -> None:
    f = _make_file(tmp_path / "b.py", b"v1")
    h1 = _hash_file(f)
    f.write_bytes(b"v2")
    h2 = _hash_file(f)
    assert h1 != h2


# ---------------------------------------------------------------------------
# Round-trip: set then get
# ---------------------------------------------------------------------------


def test_round_trip(tmp_path: Path) -> None:
    cache = CacheManager(tmp_path)
    src = _make_file(tmp_path / "mod.py", b"x = 1")
    h = _hash_file(src)
    partial = {"nodes": [{"id": "mod.py:x"}], "edges": []}

    cache.set(src, h, partial)
    result = cache.get(src)

    assert result == partial


def test_get_miss_no_manifest(tmp_path: Path) -> None:
    cache = CacheManager(tmp_path)
    src = _make_file(tmp_path / "mod.py", b"x = 1")
    assert cache.get(src) is None


def test_get_miss_after_file_change(tmp_path: Path) -> None:
    cache = CacheManager(tmp_path)
    src = _make_file(tmp_path / "mod.py", b"v1")
    h = _hash_file(src)
    cache.set(src, h, {"nodes": [], "edges": []})

    src.write_bytes(b"v2")  # file changed — hash mismatch
    assert cache.get(src) is None


def test_set_idempotent_same_hash(tmp_path: Path) -> None:
    cache = CacheManager(tmp_path)
    src = _make_file(tmp_path / "mod.py", b"same")
    h = _hash_file(src)
    partial: dict[str, Any] = {"nodes": [], "edges": []}

    cache.set(src, h, partial)
    cache.set(src, h, partial)  # second write — should not raise or corrupt
    assert cache.get(src) == partial


# ---------------------------------------------------------------------------
# Sidecar file naming
# ---------------------------------------------------------------------------


def test_sidecar_named_by_hash(tmp_path: Path) -> None:
    cache = CacheManager(tmp_path)
    src = _make_file(tmp_path / "mod.py", b"content")
    h = _hash_file(src)
    cache.set(src, h, {})

    cache_dir = tmp_path / ".grackle" / "cache"
    assert (cache_dir / f"{h}.json").exists()


# ---------------------------------------------------------------------------
# Atomic-write durability: stale .tmp does not corrupt
# ---------------------------------------------------------------------------


def test_stale_tmp_overwritten_by_new_set(tmp_path: Path) -> None:
    cache = CacheManager(tmp_path)
    src = _make_file(tmp_path / "mod.py", b"content")
    h = _hash_file(src)

    # Pre-create a stale .tmp for the manifest (simulates a crash before rename)
    manifest_path = tmp_path / ".grackle" / "cache" / "manifest.json"
    stale_tmp = manifest_path.with_suffix(".tmp")
    stale_tmp.write_text('{"stale": true}', encoding="utf-8")

    # set() writes a new .tmp and renames it — stale .tmp is overwritten
    cache.set(src, h, {"nodes": [], "edges": []})

    # manifest is now valid; get() finds the entry
    assert cache.get(src) == {"nodes": [], "edges": []}


def test_atomic_write_uses_tmp_then_rename(tmp_path: Path) -> None:
    # Verify that _atomic_write produces the correct final file content
    # (we can't intercept the rename, but we can verify the file is consistent).
    from grackle.cache import _atomic_write

    dest = tmp_path / "out.json"
    _atomic_write(dest, '{"ok": true}')
    assert dest.read_text(encoding="utf-8") == '{"ok": true}'
    assert not dest.with_suffix(".tmp").exists()


# ---------------------------------------------------------------------------
# Evict
# ---------------------------------------------------------------------------


def test_evict_removes_sidecar_and_manifest_entry(tmp_path: Path) -> None:
    cache = CacheManager(tmp_path)
    src = _make_file(tmp_path / "mod.py", b"content")
    h = _hash_file(src)
    cache.set(src, h, {"nodes": [], "edges": []})

    cache_dir = tmp_path / ".grackle" / "cache"
    assert (cache_dir / f"{h}.json").exists()

    cache.evict(src)

    assert not (cache_dir / f"{h}.json").exists()
    assert cache.get(src) is None


def test_evict_nonexistent_is_noop(tmp_path: Path) -> None:
    cache = CacheManager(tmp_path)
    src = _make_file(tmp_path / "mod.py", b"content")
    cache.evict(src)  # should not raise


def test_evict_does_not_affect_other_entries(tmp_path: Path) -> None:
    cache = CacheManager(tmp_path)
    a = _make_file(tmp_path / "a.py", b"a")
    b = _make_file(tmp_path / "b.py", b"b")
    ha = _hash_file(a)
    hb = _hash_file(b)
    partial: dict[str, Any] = {"nodes": [], "edges": []}
    cache.set(a, ha, partial)
    cache.set(b, hb, partial)

    cache.evict(a)

    assert cache.get(a) is None
    assert cache.get(b) == partial


# ---------------------------------------------------------------------------
# Resilience: corrupted / missing manifest
# ---------------------------------------------------------------------------


def test_corrupted_manifest_returns_none(tmp_path: Path) -> None:
    cache = CacheManager(tmp_path)
    manifest_path = tmp_path / ".grackle" / "cache" / "manifest.json"
    manifest_path.write_text("not json!!!", encoding="utf-8")

    src = _make_file(tmp_path / "mod.py", b"x")
    assert cache.get(src) is None


def test_missing_sidecar_returns_none(tmp_path: Path) -> None:
    cache = CacheManager(tmp_path)
    src = _make_file(tmp_path / "mod.py", b"x")
    h = _hash_file(src)

    # Write manifest entry but not the sidecar file
    manifest = {"entries": {"mod.py": {"hash": h, "partial_path": f"{h}.json"}}}
    manifest_path = tmp_path / ".grackle" / "cache" / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    assert cache.get(src) is None


# ---------------------------------------------------------------------------
# Concurrent access — no lost updates
# ---------------------------------------------------------------------------


def test_concurrent_set_no_lost_updates(tmp_path: Path) -> None:
    cache = CacheManager(tmp_path)
    n = 20
    files = []
    for i in range(n):
        f = _make_file(tmp_path / f"mod{i}.py", f"content {i}".encode())
        files.append(f)

    errors: list[Exception] = []

    def worker(f: Path) -> None:
        try:
            h = _hash_file(f)
            cache.set(f, h, {"id": f.name})
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f,)) for f in files]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Threads raised errors: {errors}"

    # Every file should now be in the cache
    for f in files:
        result = cache.get(f)
        assert result is not None, f"{f.name} missing from cache after concurrent writes"
        assert result["id"] == f.name


# ---------------------------------------------------------------------------
# flush (documented no-op)
# ---------------------------------------------------------------------------


def test_flush_is_noop(tmp_path: Path) -> None:
    cache = CacheManager(tmp_path)
    cache.flush()  # should not raise


def test_flush_after_set_is_noop(tmp_path: Path) -> None:
    cache = CacheManager(tmp_path)
    src = _make_file(tmp_path / "mod.py", b"x")
    h = _hash_file(src)
    cache.set(src, h, {})
    cache.flush()
    assert cache.get(src) == {}
