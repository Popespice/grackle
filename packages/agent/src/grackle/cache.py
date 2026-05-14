"""Content-hash cache manager for partial graph results.

CacheManager stores per-file partial graphs under ``.grackle/cache/`` using
a two-level layout: a manifest JSON file (``manifest.json``) maps
POSIX-relative source paths → ``{hash, partial_path}`` entries, and each
partial is stored as a sidecar ``<sha256>.json`` file.

All writes are atomic (write to ``.tmp`` then ``Path.rename()``) so a crash
or kill mid-write leaves the cache in a consistent state. All public methods
are thread-safe.
"""

from __future__ import annotations

import hashlib
import json
import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

from grackle.paths import to_posix


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


class CacheManager:
    """Content-hash cache over a project tree.

    Args:
        root: Absolute path to the project root. Used to compute
            POSIX-relative manifest keys via ``to_posix()``.
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._cache_dir = root / ".grackle" / "cache"
        self._manifest_path = self._cache_dir / "manifest.json"
        self._lock = threading.Lock()
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_manifest(self) -> dict[str, Any]:
        """Load the manifest from disk. Returns ``{"entries": {}}`` on any error."""
        try:
            raw = self._manifest_path.read_text(encoding="utf-8")
            data: dict[str, Any] = json.loads(raw)
            return data
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
        entry *and* the sidecar partial to be readable valid JSON.
        """
        posix_key = to_posix(path, self._root)
        content_hash = _hash_file(path)

        with self._lock:
            manifest = self._load_manifest()
            entry = manifest.get("entries", {}).get(posix_key)
            if entry is None or entry.get("hash") != content_hash:
                return None
            partial_name: str = entry["partial_path"]

        partial_path = self._cache_dir / partial_name
        try:
            raw = partial_path.read_text(encoding="utf-8")
            result: dict[str, Any] = json.loads(raw)
            return result
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def set(self, path: Path, content_hash: str, partial: dict[str, Any]) -> None:
        """Store ``partial`` for ``path`` under ``content_hash``.

        The partial is written atomically as ``<hash>.json``; the manifest is
        updated atomically afterward.
        """
        posix_key = to_posix(path, self._root)
        partial_name = f"{content_hash}.json"
        partial_path = self._cache_dir / partial_name
        partial_json = json.dumps(partial, ensure_ascii=False)

        with self._lock:
            _atomic_write(partial_path, partial_json)
            manifest = self._load_manifest()
            manifest.setdefault("entries", {})[posix_key] = {
                "hash": content_hash,
                "partial_path": partial_name,
            }
            self._save_manifest(manifest)

    def evict(self, path: Path) -> None:
        """Remove the cache entry for ``path`` (both manifest entry and sidecar).

        No-op if ``path`` has no entry.
        """
        posix_key = to_posix(path, self._root)
        with self._lock:
            manifest = self._load_manifest()
            entry = manifest.get("entries", {}).pop(posix_key, None)
            if entry is not None:
                sidecar = self._cache_dir / entry["partial_path"]
                sidecar.unlink(missing_ok=True)
                self._save_manifest(manifest)

    def flush(self) -> None:
        """No-op: all writes are already atomic and immediately durable."""
