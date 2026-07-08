"""Unit tests for grackle.watcher's pure snapshot/diff/hash-gate core and both backends."""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from grackle import watcher
from grackle.watcher import _diff, _snapshot, watch_changes

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _to_agen(it: AsyncIterator[set[Path]]) -> AsyncGenerator[set[Path], None]:
    """Narrow the public AsyncIterator return type to the concrete generator type.

    All three watcher generators are, at runtime, genuine async generators
    (they contain `yield`); the public annotation is the more abstract
    `AsyncIterator` (consumed via `async for`), so tests that need early
    cancellation (`.aclose()`) or `asyncio.create_task(gen.__anext__())`
    narrow the type here rather than widening the public API's annotation.
    """
    return cast("AsyncGenerator[set[Path], None]", it)


# ---------------------------------------------------------------------------
# Pure snapshot/diff core
# ---------------------------------------------------------------------------


def test_hash_gate_noop_on_identical_rewrite(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    _write(f, "x = 1\n")
    snap1 = _snapshot(tmp_path)

    # Rewrite IDENTICAL bytes — bumps mtime (and, on some filesystems, may
    # not even do that) but must not register as a change.
    _write(f, "x = 1\n")
    snap2 = _snapshot(tmp_path, snap1)

    assert _diff(snap1, snap2) == set()


def test_detect_modify(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    _write(f, "x = 1\n")
    snap1 = _snapshot(tmp_path)

    _write(f, "x = 2\n")
    snap2 = _snapshot(tmp_path, snap1)

    assert _diff(snap1, snap2) == {"a.py"}


def test_detect_add(tmp_path: Path) -> None:
    _write(tmp_path / "a.py", "x = 1\n")
    snap1 = _snapshot(tmp_path)

    _write(tmp_path / "b.py", "y = 2\n")
    snap2 = _snapshot(tmp_path, snap1)

    assert _diff(snap1, snap2) == {"b.py"}


def test_detect_delete(tmp_path: Path) -> None:
    a = tmp_path / "a.py"
    _write(a, "x = 1\n")
    _write(tmp_path / "b.py", "y = 2\n")
    snap1 = _snapshot(tmp_path)

    a.unlink()
    snap2 = _snapshot(tmp_path, snap1)

    assert _diff(snap1, snap2) == {"a.py"}


def test_loop_break_grackle_cache_ignored(tmp_path: Path) -> None:
    """A watch-triggered rebuild writes .grackle/cache/*; watching it must never self-trigger."""
    _write(tmp_path / "a.py", "x = 1\n")
    snap1 = _snapshot(tmp_path)

    cache_dir = tmp_path / ".grackle" / "cache"
    cache_dir.mkdir(parents=True)
    _write(cache_dir / "manifest.json", '{"entries": {}}')
    _write(cache_dir / "deadbeef.json", '{"nodes": [], "edges": []}')
    snap2 = _snapshot(tmp_path, snap1)

    assert _diff(snap1, snap2) == set()
    assert not any(key.startswith(".grackle/") for key in snap2)


def test_ignore_tmp_and_nonparseable(tmp_path: Path) -> None:
    _write(tmp_path / "a.py", "x = 1\n")
    snap1 = _snapshot(tmp_path)

    _write(tmp_path / "foo.tmp", "junk")
    _write(tmp_path / "data.json", "{}")
    _write(tmp_path / "x.jsonl.part", "{}")
    snap2 = _snapshot(tmp_path, snap1)

    assert _diff(snap1, snap2) == set()
    assert set(snap2) == {"a.py"}


def test_safe_posix_key_outside_root_returns_none(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    outside = tmp_path_factory.mktemp("outside")
    target = outside / "shared.py"
    target.write_text("x = 1\n", encoding="utf-8")

    assert watcher._safe_posix_key(target, tmp_path) is None


def test_safe_posix_key_symlink_loop_returns_none(tmp_path: Path) -> None:
    loop_link = tmp_path / "loop.py"
    loop_link.symlink_to(loop_link)

    assert watcher._safe_posix_key(loop_link, tmp_path) is None


def test_snapshot_skips_symlink_outside_root_without_crashing(
    tmp_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Regression: a symlinked .py file resolving outside root used to raise an
    uncaught ValueError from _snapshot (no try/except around to_posix at all),
    which would propagate out of the watch loop and permanently end watch mode
    for the rest of the server session.
    """
    outside = tmp_path_factory.mktemp("outside")
    (outside / "shared.py").write_text("x = 1\n", encoding="utf-8")
    _write(tmp_path / "a.py", "y = 2\n")
    (tmp_path / "linked.py").symlink_to(outside / "shared.py")

    snap = _snapshot(tmp_path)  # must not raise

    assert "a.py" in snap
    assert "linked.py" not in snap


def test_snapshot_skips_symlink_loop_without_crashing(tmp_path: Path) -> None:
    """Regression: a self-referential symlink used to raise an uncaught RuntimeError
    ("Symlink loop from ...") from _snapshot, distinct from (and not caught by) the
    ValueError-only guard the ancestor-path fix added to _watch_filter alone.
    """
    _write(tmp_path / "a.py", "y = 2\n")
    loop_link = tmp_path / "loop.py"
    loop_link.symlink_to(loop_link)

    snap = _snapshot(tmp_path)  # must not raise

    assert "a.py" in snap
    assert "loop.py" not in snap


def test_posix_normalization_nested_dir(tmp_path: Path) -> None:
    nested = tmp_path / "src" / "pkg"
    nested.mkdir(parents=True)
    _write(nested / "mod.py", "x = 1\n")

    snap = _snapshot(tmp_path)

    assert "src/pkg/mod.py" in snap
    assert not any("\\" in key for key in snap)


def test_crlf_lf_is_a_change(tmp_path: Path) -> None:
    """Byte hashing (not text) means a CRLF<->LF flip IS a real change — a documented contract."""
    f = tmp_path / "a.py"
    f.write_bytes(b"x = 1\r\n")
    snap1 = _snapshot(tmp_path)

    f.write_bytes(b"x = 1\n")
    snap2 = _snapshot(tmp_path, snap1)

    assert _diff(snap1, snap2) == {"a.py"}


def test_read_failure_falls_back_to_prior_hash_but_poisons_stat(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read() failure this tick reuses the prior HASH (no reported change)...

    ...but must NOT reuse the prior (mtime, size) verbatim: that would let a
    later tick's fast-path wrongly trust a value that was never actually
    re-observed, if the real stat ever happens to coincide with the stale
    one. The fallback entry's (mtime, size) is poisoned instead, forcing a
    real re-verification on the very next tick regardless of what the file's
    stat looks like then.
    """
    f = tmp_path / "a.py"
    _write(f, "x = 1\n")
    snap1 = _snapshot(tmp_path)
    assert "a.py" in snap1

    real_read_bytes = Path.read_bytes

    def _flaky_read_bytes(self: Path) -> bytes:
        if self.name == "a.py":
            raise OSError("simulated mid-write race")
        return real_read_bytes(self)

    # Force the mtime 5s into the future so the fast-path can't skip
    # re-reading regardless of filesystem mtime granularity (touch() alone
    # risks landing in the same coarse mtime bucket on some CI runners/
    # filesystems, which would make this test flaky for an environment
    # reason unrelated to the fix under test).
    new_mtime_ns = snap1["a.py"][0] + 5_000_000_000
    os.utime(f, ns=(new_mtime_ns, new_mtime_ns))
    monkeypatch.setattr(Path, "read_bytes", _flaky_read_bytes)
    snap2 = _snapshot(tmp_path, snap1)

    assert snap2["a.py"][2] == snap1["a.py"][2]  # same hash -> no reported change this tick
    assert snap2["a.py"][:2] != snap1["a.py"][:2]  # (mtime, size) poisoned, NOT copied verbatim
    assert _diff(snap1, snap2) == set()


def test_stat_failure_falls_back_to_prior_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stat() failure (not just a read() failure) must ALSO fall back, not drop the file.

    Regression test: an earlier version only handled read() failures, so a
    transient stat() failure on an existing, unmodified file silently
    dropped it from the snapshot, and _diff reported it as a deletion.
    """
    f = tmp_path / "a.py"
    _write(f, "x = 1\n")
    snap1 = _snapshot(tmp_path)
    assert "a.py" in snap1

    real_stat = Path.stat

    def _flaky_stat(self: Path, *, follow_symlinks: bool = True) -> object:
        if self.name == "a.py":
            raise OSError("simulated transient stat race")
        return real_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", _flaky_stat)
    snap2 = _snapshot(tmp_path, snap1)

    assert "a.py" in snap2  # NOT dropped
    assert snap2["a.py"][2] == snap1["a.py"][2]
    assert _diff(snap1, snap2) == set()  # not reported as a deletion


# ---------------------------------------------------------------------------
# Async backends
# ---------------------------------------------------------------------------


async def test_watch_poll_yields_a_batch_on_real_change(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    _write(f, "x = 1\n")

    gen = _to_agen(watcher._watch_poll(tmp_path, interval=0.1))
    task = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0.03)  # let the initial snapshot prime before editing
    _write(f, "x = 2\n")

    try:
        batch = await asyncio.wait_for(task, timeout=5.0)
    finally:
        with contextlib.suppress(Exception):
            await gen.aclose()

    assert {p.name for p in batch} == {"a.py"}


async def test_watch_poll_noop_rewrite_yields_nothing(tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    _write(f, "x = 1\n")

    gen = _to_agen(watcher._watch_poll(tmp_path, interval=0.05))
    task = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0.02)
    _write(f, "x = 1\n")  # identical content

    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(task, timeout=0.5)
    # wait_for's timeout cancels the task when nothing arrives in time — that
    # cancellation (not a returned batch) is what proves the no-op rewrite
    # never triggered a yield.
    assert task.cancelled()

    with contextlib.suppress(Exception):
        await gen.aclose()


async def test_watch_poll_cancellation_does_not_hang(tmp_path: Path) -> None:
    _write(tmp_path / "a.py", "x = 1\n")

    gen = _to_agen(watcher._watch_poll(tmp_path, interval=5.0))  # long: definitely mid-sleep
    task = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0.05)

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
        await asyncio.wait_for(task, timeout=2.0)
    assert task.done()


async def test_watch_changes_falls_back_to_poll_when_watchfiles_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(watcher, "watchfiles_available", lambda: False)
    f = tmp_path / "a.py"
    _write(f, "x = 1\n")

    gen = _to_agen(watch_changes(tmp_path, interval=0.1))
    task = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0.03)
    _write(f, "x = 2\n")

    try:
        batch = await asyncio.wait_for(task, timeout=5.0)
    finally:
        with contextlib.suppress(Exception):
            await gen.aclose()

    assert {p.name for p in batch} == {"a.py"}


async def test_watch_changes_forces_poll_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """force_poll=True must pick the poller even when watchfiles IS importable."""
    monkeypatch.setattr(watcher, "watchfiles_available", lambda: True)
    calls: list[str] = []

    async def _fake_poll(root: Path, interval: float) -> AsyncIterator[set[Path]]:
        calls.append("poll")
        return
        yield  # pragma: no cover - unreachable; makes this an async generator function

    monkeypatch.setattr(watcher, "_watch_poll", _fake_poll)

    gen = watch_changes(tmp_path, interval=0.05, force_poll=True)
    with contextlib.suppress(StopAsyncIteration):
        await gen.__anext__()

    assert calls == ["poll"]


async def test_watch_watchfiles_detects_change(tmp_path: Path) -> None:
    pytest.importorskip("watchfiles")
    assert watcher.watchfiles_available()

    f = tmp_path / "a.py"
    _write(f, "x = 1\n")

    gen = _to_agen(watcher._watch_watchfiles(tmp_path, interval=0.1))
    task = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0.3)  # give the OS-level watcher time to start
    _write(f, "x = 2\n")

    try:
        batch = await asyncio.wait_for(task, timeout=10.0)
    finally:
        with contextlib.suppress(Exception):
            await gen.aclose()

    assert {p.name for p in batch} == {"a.py"}


async def test_watch_watchfiles_ignores_excluded_dir_under_root(tmp_path: Path) -> None:
    """The common case: a parseable file directly under root/node_modules must stay
    excluded after the ancestor-path fix switched _watch_filter to check the path
    *relative to root* — only the ancestor-of-root case was covered by the fix's own
    regression test; this closes the more common "excluded dir nested under root" gap.
    """
    pytest.importorskip("watchfiles")

    excluded = tmp_path / "node_modules" / "pkg"
    excluded.mkdir(parents=True)
    f = excluded / "vendored.py"
    _write(f, "x = 1\n")

    gen = _to_agen(watcher._watch_watchfiles(tmp_path, interval=0.1))
    task = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0.3)  # give the OS-level watcher time to start
    _write(f, "x = 2\n")

    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(task, timeout=2.0)
    assert task.cancelled()  # no batch was yielded — the edit was correctly excluded

    with contextlib.suppress(Exception):
        await gen.aclose()


async def test_watch_watchfiles_root_under_excluded_ancestor_name(tmp_path: Path) -> None:
    """Regression: root sitting under an ancestor literally named e.g. 'build' must not
    silently exclude every file. watchfiles reports absolute paths; matching
    _EXCLUDED_DIRS against every component of the absolute path (rather than only the
    part relative to root) would make _watch_filter reject everything whenever the
    served project happens to sit under a directory named .grackle/.git/node_modules/
    target/__pycache__/.venv/dist/build anywhere above --root.
    """
    pytest.importorskip("watchfiles")

    root = tmp_path / "build" / "myproj"  # "build" is an ANCESTOR of root, not a subdir under it
    root.mkdir(parents=True)
    f = root / "a.py"
    _write(f, "x = 1\n")

    gen = _to_agen(watcher._watch_watchfiles(root, interval=0.1))
    task = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0.3)  # give the OS-level watcher time to start
    _write(f, "x = 2\n")

    try:
        batch = await asyncio.wait_for(task, timeout=10.0)
    finally:
        with contextlib.suppress(Exception):
            await gen.aclose()

    assert {p.name for p in batch} == {"a.py"}


async def test_watch_watchfiles_coalesced_add_then_delete_same_path_keeps_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A batch reporting BOTH an add and a delete for the same still-existing file must
    resolve to its real current state (present, correctly hashed) — not lose the entry
    just because the 'deleted' event for that path is processed after the 'added' one
    within the same coalesced batch. watchfiles' per-batch `changes` is a plain set of
    (Change, str) tuples with no chronological ordering guarantee; this must not matter.
    """
    pytest.importorskip("watchfiles")
    import watchfiles

    f = tmp_path / "a.py"
    _write(f, "x = 1\n")  # the file genuinely exists throughout this test

    async def _fake_awatch(
        *args: object, **kwargs: object
    ) -> AsyncIterator[list[tuple[object, str]]]:
        # "added" listed before "deleted" for the SAME still-existing path —
        # the exact order under which an order-trusting implementation would
        # let the delete win and incorrectly drop the entry.
        yield [(watchfiles.Change.added, str(f)), (watchfiles.Change.deleted, str(f))]
        # A second, later batch: an identical rewrite of the same file. If
        # the first batch had wrongly dropped the entry, this would look
        # like a brand-new file appearing (a spurious reported change).
        yield [(watchfiles.Change.modified, str(f))]
        # Neither batch above should produce a reported change if the code
        # under test is correct, so this generator must stay open (like a
        # real awatch() would, waiting for the next FS event) rather than
        # exhausting — an exhausted generator raises StopAsyncIteration
        # almost immediately, which would masquerade as "no change" even if
        # a bug had already surfaced. This sleep is never actually reached
        # within the test's short timeout window.
        await asyncio.sleep(100)
        yield []  # pragma: no cover - unreachable within the test

    monkeypatch.setattr(watchfiles, "awatch", _fake_awatch)

    gen = _to_agen(watcher._watch_watchfiles(tmp_path, interval=0.1))
    task = asyncio.create_task(gen.__anext__())

    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(task, timeout=0.5)
    # Neither fake batch changes the file's actual content, so a correct
    # implementation reports nothing for either — wait_for's timeout cancels
    # the still-pending task, which is what proves no batch was yielded. If
    # the entry had been wrongly dropped, the second batch would look like a
    # new file and the task would have resolved with a batch instead.
    assert task.cancelled()

    with contextlib.suppress(Exception):
        await gen.aclose()
