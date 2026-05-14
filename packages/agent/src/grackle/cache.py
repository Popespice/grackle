"""Content-hash cache manager for partial graph results.

CacheManager stores per-file partial graphs under ``.grackle/cache/`` using
a two-level layout: a manifest JSON file (``manifest.json``) maps
POSIX-relative source paths → ``{hash, partial_path}`` entries, and each
partial is stored as a sidecar ``<sha256>.json`` file.

All writes are atomic (write to ``.tmp`` then ``Path.rename()``) so a crash
or kill mid-write leaves the cache in a consistent state. All public methods
are safe under (a) multiple threads in one process and (b) multiple processes
sharing the same project root, via a per-root in-process ``threading.Lock``
combined with an OS-level file lock on ``.grackle/cache/.lock``.

Resilience: malformed manifests (invalid JSON, valid JSON of the wrong shape)
are treated as empty rather than raising. Sidecars that aren't a JSON object
are likewise treated as a cache miss.
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
from contextlib import contextmanager
from typing import IO, TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

from grackle.paths import to_posix

# ---------------------------------------------------------------------------
# Cross-platform file locking — POSIX (fcntl) + Windows (msvcrt)
# ---------------------------------------------------------------------------

if sys.platform == "win32":
    import msvcrt

    def _file_lock_acquire(fh: IO[bytes]) -> None:
        # msvcrt.locking locks N bytes from the current file position;
        # the byte must exist, so callers ensure the lock file has ≥ 1 byte.
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)

    def _file_lock_release(fh: IO[bytes]) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _file_lock_acquire(fh: IO[bytes]) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)

    def _file_lock_release(fh: IO[bytes]) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


# ---------------------------------------------------------------------------
# Hashing + atomic write helpers
# ---------------------------------------------------------------------------


def _hash_file(path: Path) -> str:
    """Return the SHA-256 hex digest of the file at ``path``."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_write(dest: Path, data: str) -> None:
    """Write ``data`` to ``dest`` atomically via a sibling ``.tmp`` + rename."""
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(data, encoding="utf-8")
    tmp.rename(dest)


def _normalize_manifest(data: Any) -> dict[str, Any]:
    """Coerce arbitrary JSON ``data`` to a manifest-shaped dict.

    Unknown top-level keys are preserved (forward-compat); ``entries`` is
    reset to an empty dict if it isn't already a dict.
    """
    if not isinstance(data, dict):
        return {"entries": {}}
    if not isinstance(data.get("entries"), dict):
        data["entries"] = {}
    return data


# ---------------------------------------------------------------------------
# CacheManager
# ---------------------------------------------------------------------------


class CacheManager:
    """Content-hash cache over a project tree.

    Args:
        root: Path to the project root. Used to compute POSIX-relative
            manifest keys via ``to_posix()``. Need not be absolute — both
            ``root`` and queried paths are ``.resolve()``-d internally.

    Concurrency: safe for multiple threads in one process AND multiple
    processes sharing the same root. In-process safety comes from a
    per-root ``threading.Lock`` shared across instances; cross-process
    safety comes from an exclusive lock on ``.grackle/cache/.lock``.
    """

    # Per-root in-process locks, keyed by resolved POSIX root path.
    # Two CacheManager instances on the same root share one threading.Lock,
    # so they don't clobber each other within a single process.
    _root_locks: ClassVar[dict[str, threading.Lock]] = {}
    _root_locks_meta: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, root: Path) -> None:
        self._root = root
        self._cache_dir = root / ".grackle" / "cache"
        self._manifest_path = self._cache_dir / "manifest.json"
        self._lock_path = self._cache_dir / ".lock"
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        # Ensure the lock file exists with at least 1 byte (Windows
        # msvcrt.locking requires the locked byte to exist).
        try:
            with self._lock_path.open("xb") as fh:
                fh.write(b"\0")
        except FileExistsError:
            pass

        # Per-root in-process lock (shared across instances on the same root).
        key = str(root.resolve())
        with type(self)._root_locks_meta:
            self._inproc_lock = type(self)._root_locks.setdefault(key, threading.Lock())

    # ------------------------------------------------------------------
    # Locking
    # ------------------------------------------------------------------

    @contextmanager
    def _locked(self) -> Iterator[None]:
        """Acquire in-process lock, then OS-level file lock; release in reverse."""
        with self._inproc_lock, self._lock_path.open("r+b") as fh:
            _file_lock_acquire(fh)
            try:
                yield
            finally:
                _file_lock_release(fh)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_manifest(self) -> dict[str, Any]:
        """Load + shape-validate the manifest. Returns ``{"entries": {}}`` on failure."""
        try:
            raw = self._manifest_path.read_text(encoding="utf-8")
            return _normalize_manifest(json.loads(raw))
        except (FileNotFoundError, json.JSONDecodeError):
            return {"entries": {}}

    def _save_manifest(self, manifest: dict[str, Any]) -> None:
        _atomic_write(self._manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, path: Path) -> dict[str, Any] | None:
        """Return the cached partial graph for ``path``, or ``None`` on miss.

        A cache hit requires the file's current SHA-256 to match the manifest
        entry, the entry to have a ``hash`` + ``partial_path`` of the right
        shape, and the sidecar to be readable JSON of object shape.
        """
        posix_key = to_posix(path, self._root)
        content_hash = _hash_file(path)

        with self._locked():
            manifest = self._load_manifest()
            entry = manifest["entries"].get(posix_key)
            if not isinstance(entry, dict):
                return None
            if entry.get("hash") != content_hash:
                return None
            partial_name = entry.get("partial_path")
            if not isinstance(partial_name, str):
                return None

        partial_path = self._cache_dir / partial_name
        try:
            raw = partial_path.read_text(encoding="utf-8")
            result = json.loads(raw)
        except (FileNotFoundError, json.JSONDecodeError):
            return None
        if not isinstance(result, dict):
            return None
        return result

    def set(self, path: Path, content_hash: str, partial: dict[str, Any]) -> None:
        """Store ``partial`` for ``path`` under ``content_hash``.

        The partial is written atomically as ``<hash>.json``; the manifest is
        updated atomically afterward.
        """
        posix_key = to_posix(path, self._root)
        partial_name = f"{content_hash}.json"
        partial_path = self._cache_dir / partial_name
        partial_json = json.dumps(partial, ensure_ascii=False)

        with self._locked():
            _atomic_write(partial_path, partial_json)
            manifest = self._load_manifest()
            manifest["entries"][posix_key] = {
                "hash": content_hash,
                "partial_path": partial_name,
            }
            self._save_manifest(manifest)

    def evict(self, path: Path) -> None:
        """Remove the cache entry for ``path`` (both manifest entry and sidecar).

        No-op if ``path`` has no entry.
        """
        posix_key = to_posix(path, self._root)
        with self._locked():
            manifest = self._load_manifest()
            entry = manifest["entries"].pop(posix_key, None)
            if isinstance(entry, dict):
                sidecar_name = entry.get("partial_path")
                if isinstance(sidecar_name, str):
                    (self._cache_dir / sidecar_name).unlink(missing_ok=True)
                self._save_manifest(manifest)

    def flush(self) -> None:
        """No-op: all writes are already atomic and immediately durable."""
