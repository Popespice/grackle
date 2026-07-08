"""File-watching for ``grackle serve --watch``: hash-gated change detection.

Two backends share the same pure snapshot/diff/hash-gate core:

- :func:`_watch_poll` — stdlib mtime-polling, always available. A
  deadline-scheduled loop (mirroring
  :mod:`grackle.node_runtime.launcher`'s coverage-poll cadence) re-stats the
  tree every ``interval`` seconds.
- :func:`_watch_watchfiles` — event-driven, via the *optional*
  ``watchfiles`` dependency (``pip install grackle[watch]``). Never
  required: :func:`watch_changes` falls back to polling when it isn't
  importable.

Both backends only ever report a batch of changed paths after confirming the
file's **content** actually changed (SHA-256 of bytes, matching
:mod:`grackle.cache`'s own hash gate) — an FS event or an mtime bump alone
(atomic-save write-temp-then-rename, a touch, a checkout that only flips line
endings) is not enough. This is what keeps ``--watch`` quiet on saves that
don't change anything.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import time
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from grackle.paths import to_posix

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

log = structlog.get_logger()

# Extensions considered "parseable" — mirrors the walkers' own hardcoded
# discovery: python_parser/walker.py (`.py`), typescript_parser/walker.py
# (`.ts`/`.tsx`/`.mts`/`.cts`), go_parser/walker.py (`.go`), rust_parser/walker.py
# (`.rs`). Kept as a hardcoded frozenset here rather than a registry accessor
# because StaticParserAdapter does not expose an `.extensions` attribute (only
# RuntimeAdapter does) — adding one would touch the Protocol and all four
# adapters for a fixed, small set.
_PARSEABLE_EXTS = frozenset({".py", ".ts", ".tsx", ".mts", ".cts", ".go", ".rs"})

# Directories pruned from the walk. `.grackle` MUST stay excluded: a rebuild
# writes `.grackle/cache/{manifest.json,<hash>.json,*.tmp}` sidecars
# (cache.py), and watching them would self-trigger an infinite rebuild loop.
# The extension filter above independently breaks the same loop (sidecars are
# `.json`/`.tmp`, never in `_PARSEABLE_EXTS`) — this is belt-and-suspenders,
# and both guards are load-bearing; do not remove either.
_EXCLUDED_DIRS = frozenset(
    {".grackle", ".git", "node_modules", "target", "__pycache__", ".venv", "dist", "build"}
)

# posix_key -> (mtime_ns, size, sha256-of-bytes)
_Snapshot = dict[str, tuple[int, int, str]]

# Sentinel (mtime_ns, size) that can never match a real stat result. Used to
# poison a fallback entry (see _stat_and_hash) so a transient stat/read
# failure only ever grants a ONE-tick reprieve — the next tick's fast-path
# comparison can never coincidentally trust a value that was never actually
# re-observed — rather than silently persisting forever.
_STALE_STAT: tuple[int, int] = (-1, -1)


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_posix_key(path: Path, root: Path) -> str | None:
    """``to_posix(path, root)``, or ``None`` if it can't be computed right now.

    ``to_posix`` calls ``Path.resolve()``, which can raise ``ValueError`` (the
    resolved path isn't under ``root`` — e.g. a symlink pointing outside the
    served tree) *or* ``RuntimeError`` (a symlink loop). Either is a reason to
    skip this path for this tick, not to crash the whole watch loop — an
    earlier version only guarded the ``ValueError`` case in one call site and
    guarded nothing at all in ``_snapshot``, so a symlink loop anywhere under
    the served root could kill watch mode for the rest of the server's
    session with just a warning log.
    """
    try:
        return to_posix(path, root)
    except (ValueError, RuntimeError):
        return None


def _stat_and_hash(
    path: Path, fallback: tuple[int, int, str] | None
) -> tuple[int, int, str] | None:
    """Stat + hash ``path``, falling back to ``fallback`` on a transient OSError.

    Shared by both backends so their "a transient stat/read failure means
    unchanged this tick, not deleted" contract can't independently drift —
    an earlier version duplicated this logic in each backend, and the two
    copies had already diverged (one handled a bare ``stat()`` failure, the
    other didn't). Also owns the lazy-hash fast path (skip re-reading when
    ``(mtime, size)`` already matches ``fallback``) so every caller does
    exactly one ``stat()`` per file per tick, not two.

    On fallback, the returned ``(mtime, size)`` is poisoned to
    :data:`_STALE_STAT` rather than copying ``fallback`` verbatim, so a
    *later* tick can never fast-path-skip re-verifying this file by
    coincidentally matching a value that was never actually re-observed.
    Returns ``None`` (omit this key) only when there is no fallback to use
    and the file cannot be read right now.

    **Known limitation** (see ADR-0027): this "poisoned fallback, re-verify
    next tick" contract assumes a *next* opportunity to re-verify exists.
    ``_watch_poll`` always provides one (a full-tree re-scan every tick).
    ``_watch_watchfiles`` does not — a path is only re-examined when a *new*
    FS event names it again — so a transient failure racing a file's one and
    only reported event there can leave that file's hash permanently stale
    for the rest of the session, not just for one tick. Both backends are
    equally exposed to *some* form of this class of gap (this one here vs.
    ``_watch_poll``'s coarse-mtime aliasing); neither is engineered away in
    this chunk.
    """
    try:
        st = path.stat()
    except OSError:
        if fallback is None:
            return None
        return (*_STALE_STAT, fallback[2])

    if fallback is not None and st.st_mtime_ns == fallback[0] and st.st_size == fallback[1]:
        return fallback

    try:
        content = path.read_bytes()
    except OSError:
        if fallback is None:
            return None
        return (*_STALE_STAT, fallback[2])
    return (st.st_mtime_ns, st.st_size, _hash_bytes(content))


def _iter_parseable_files(root: Path) -> Iterator[Path]:
    """Yield every parseable file under ``root``, pruning excluded directories.

    Uses ``Path.walk()`` (stdlib, 3.12+) rather than ``rglob`` so excluded
    directories are pruned *during* the walk (mutating ``dirnames`` in place)
    instead of merely filtered after a full traversal — this matters for
    trees with a large ``node_modules``/``target``/``.venv``.
    ``follow_symlinks=False`` (the default) avoids symlink-cycle loops.
    """
    for dirpath, dirnames, filenames in root.walk():
        dirnames[:] = [d for d in dirnames if d not in _EXCLUDED_DIRS]
        for filename in filenames:
            candidate = dirpath / filename
            if candidate.suffix in _PARSEABLE_EXTS:
                yield candidate


def _snapshot(root: Path, previous: _Snapshot | None = None) -> _Snapshot:
    """Stat every parseable file under ``root`` into a content snapshot.

    Hashing is lazy — see :func:`_stat_and_hash`'s fast path: a quiet tree
    costs one stat per file, not one read+hash. A file that fails to stat
    *or* read (permission race, mid-write, a symlink loop) falls back to
    :func:`_stat_and_hash`'s fallback handling, or is simply omitted if it
    has no prior entry to fall back to (a brand-new file mid-write on its
    very first appearance).
    """
    prev = previous or {}
    result: _Snapshot = {}
    for path in _iter_parseable_files(root):
        key = _safe_posix_key(path, root)
        if key is None:
            continue
        entry = _stat_and_hash(path, prev.get(key))
        if entry is not None:
            result[key] = entry
    return result


def _diff(old: _Snapshot, new: _Snapshot) -> set[str]:
    """Return posix keys added, removed, or content-hash-changed between snapshots."""
    changed = {key for key, entry in new.items() if key not in old or old[key][2] != entry[2]}
    changed |= old.keys() - new.keys()
    return changed


async def _watch_poll(root: Path, interval: float) -> AsyncIterator[set[Path]]:
    """Deadline-scheduled mtime-poll watcher (stdlib only, always available).

    Primes the snapshot once *before* the first tick so startup never reports
    every file as "added" — the connect-time push already covers initial
    state, and a spurious first-tick rebuild would be redundant and, on a
    large tree, wasteful. Cadence mirrors
    ``node_runtime/launcher.py``'s coverage-poll loop: each wake is scheduled
    off the previous target (not off "now"), so a scan that overruns the
    interval doesn't stack back-to-back zero-wait ticks; a scan that overruns
    by more than a full interval resyncs from now.
    """
    snapshot = _snapshot(root)
    next_at = time.monotonic() + interval
    while True:
        wait_s = max(0.0, next_at - time.monotonic())
        await asyncio.sleep(wait_s)

        new_snapshot = _snapshot(root, snapshot)
        changed_keys = _diff(snapshot, new_snapshot)
        snapshot = new_snapshot

        next_at += interval
        now = time.monotonic()
        if next_at < now:
            next_at = now + interval

        if changed_keys:
            yield {root / key for key in changed_keys}


async def _watch_watchfiles(root: Path, interval: float) -> AsyncIterator[set[Path]]:
    """Event-driven watcher via the optional ``watchfiles`` dependency.

    ``watchfiles`` reports raw filesystem events — including an atomic-save's
    mtime bump with no content change — so every reported path still runs
    through the same byte-hash gate as the poller; the hash, not the FS
    event, is the source of truth for "did content actually change". Only
    the reported candidate paths are re-stated/hashed each batch (not a full
    tree re-scan), which is cheaper than :func:`_watch_poll` at the cost of
    depending on the optional package.
    """
    import watchfiles  # local import: only reached once the caller has confirmed availability

    def _watch_filter(_change: object, path: str) -> bool:
        p = Path(path)
        if p.suffix not in _PARSEABLE_EXTS:
            return False
        # Relative to root, NOT the raw absolute path — watchfiles reports
        # absolute paths, and root itself may sit under an ancestor
        # directory that happens to share a name with _EXCLUDED_DIRS (e.g.
        # a checkout under ~/build/<repo> or /opt/dist/<app>). Checking the
        # full absolute path's parts would then exclude every file in the
        # project, silently and permanently. Only directories BELOW root
        # count. `_safe_posix_key` also absorbs a symlink-loop RuntimeError,
        # not just an outside-root ValueError.
        rel_key = _safe_posix_key(p, root)
        if rel_key is None:
            return False
        return not any(part in _EXCLUDED_DIRS for part in rel_key.split("/"))

    snapshot = _snapshot(root)
    async for changes in watchfiles.awatch(
        root,
        watch_filter=_watch_filter,
        debounce=max(1, int(interval * 1000)),
        recursive=True,
    ):
        # A debounced batch can coalesce multiple raw FS events for the SAME
        # path into one `changes` set — e.g. a delete followed by a recreate
        # within one debounce window. `changes` is a plain set of
        # (Change, str) tuples with no ordering guarantee: Python's set
        # iteration order depends on hash placement, not chronological FS
        # event order. Resolve each affected path's CURRENT on-disk state
        # exactly once, rather than trusting whichever per-event Change type
        # the set happens to iterate first.
        affected: dict[str, Path] = {}
        for _change_type, raw_path in changes:
            path = Path(raw_path)
            key = _safe_posix_key(path, root)
            if key is None:
                continue  # defensive: outside root or a symlink loop
            affected[key] = path

        working = dict(snapshot)
        for key, path in affected.items():
            if not path.exists():
                working.pop(key, None)
                continue
            entry = _stat_and_hash(path, working.get(key))
            if entry is not None:
                working[key] = entry

        changed_keys = _diff(snapshot, working)
        snapshot = working
        if changed_keys:
            yield {root / key for key in changed_keys}


def watchfiles_available() -> bool:
    """Return True if the optional ``watchfiles`` package can be imported."""
    return importlib.util.find_spec("watchfiles") is not None


async def watch_changes(
    root: Path, interval: float, *, force_poll: bool = False
) -> AsyncIterator[set[Path]]:
    """Yield batches of changed native file paths as the project tree is edited.

    Picks the event-driven ``watchfiles`` backend when it is importable and
    ``force_poll`` is False; otherwise falls back to the stdlib mtime-poller.
    ``watchfiles`` is an optional dependency (``pip install grackle[watch]``)
    — never required for ``--watch`` to work.
    """
    if not force_poll and watchfiles_available():
        log.info("watch mode: using watchfiles backend", root=str(root), interval=interval)
        async for batch in _watch_watchfiles(root, interval):
            yield batch
    else:
        log.info("watch mode: using stdlib mtime-poll backend", root=str(root), interval=interval)
        async for batch in _watch_poll(root, interval):
            yield batch
