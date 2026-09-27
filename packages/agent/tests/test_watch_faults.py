"""Campaign T6-4 (docs/test-campaigns/phase-12.md): watch-mode fault injection.

Every test here drives the REAL watch loop — ``serve(watch=True)`` running
``server._watch_loop`` over ``grackle.watcher``'s stdlib poller — and injects
its fault at a precise point inside the rebuild rather than racing a timer:

- **A file vanishing mid-rebuild.** The walkers enumerate files with
  ``rglob`` and then call ``CacheManager.get(path)``, which hashes the file
  before anything else reads it. The fault is injected at exactly that call,
  on the watch executor's thread, so the file disappears after the walker
  committed to it and before it is read. That is the widest window a real
  deletion (an editor's rename-then-write save, a ``git checkout``, a codegen
  step that cleans its output directory) can land in.
- **Rebuild serialization.** ``_watch_loop`` awaits each rebuild before it
  pulls the next batch of changes, and the rebuild runs on a
  ``max_workers=1`` executor. Either alone keeps two watch rebuilds from
  running at once; the caches they share (``meta_cache``,
  ``predicted_ctx.cache``) and the tree-sitter parser singleton are unlocked,
  so losing both would race them. The pin records every
  ``_build_static_graph`` entry and exit and asserts the in-flight count never
  exceeds one while edits land mid-rebuild.

The connect-time parse is deliberately NOT serialized against a watch rebuild
(ADR-0027 §5: it stays inline on the event loop); these tests arm their hooks
only after the connect-time push has been received, so every recorded build is
a watch rebuild.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import threading
import time
from typing import TYPE_CHECKING, Any

import pytest
from websockets.asyncio.client import connect

import grackle.server as server_module
from grackle.adapters.base import ParseOptions
from grackle.cache import CacheManager
from grackle.python_parser.adapter import PythonStaticParser
from grackle.typescript_parser.adapter import TypeScriptStaticParser

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from conftest import StartServer

# The poll interval every test here runs the watcher at. Small so a missed
# tick costs little wall-clock; the pins never depend on its exact value.
_INTERVAL = 0.05


async def _start_watch_server(
    start_server: StartServer, root: Path, interval: float = _INTERVAL
) -> tuple[asyncio.Task[None], int]:
    """``serve(watch=True)`` on the deterministic stdlib poller.

    Files under ``root`` must exist before this is called: the watch task
    snapshots its baseline before ``serve`` reports ready, so only edits made
    after this returns are changes.
    """
    return await start_server(root=root, watch=True, watch_interval=interval, watch_poll=True)


async def _stop_server(task: asyncio.Task[None]) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _recv_graph(ws: Any, timeout: float = 5.0) -> dict[str, Any]:
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    msg: dict[str, Any] = json.loads(raw)
    assert msg["type"] == "static_graph", msg["type"]
    return msg


def _node_ids(msg: dict[str, Any]) -> set[str]:
    return {str(n["id"]) for n in msg["payload"]["nodes"]}


async def _wait_for(event: threading.Event, timeout: float) -> bool:
    """Wait for a ``threading.Event`` without blocking the event loop."""
    deadline = time.monotonic() + timeout
    while not event.is_set():
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.01)
    return True


class _VanishingLookup:
    """Stands in for ``CacheManager.get``; once armed, deletes ``victim`` at
    the moment a walker looks it up.

    At that call the walker has already enumerated the file (``rglob``) and
    committed to it; nothing has read it yet. With ``blink=True`` the file
    comes back byte-for-byte, mtime included, when the enclosing
    ``_build_static_graph`` returns: absent for the whole rest of the rebuild,
    present again before the watch loop can take its next snapshot — a
    transient absence the watcher itself can never observe.
    """

    def __init__(self, victim: Path, *, blink: bool = False) -> None:
        self.victim = victim
        self.blink = blink
        self.armed = threading.Event()
        self.fired = threading.Event()
        self.fired_on_thread = ""
        self._saved: tuple[bytes, int, int] | None = None
        self._real_get = CacheManager.get

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _get(cache: CacheManager, path: Path) -> dict[str, Any] | None:
            return self._lookup(cache, path)

        monkeypatch.setattr(CacheManager, "get", _get)
        if self.blink:
            real_build = server_module._build_static_graph

            def _build(root: Path, meta_cache: Any, predicted_ctx: Any) -> Any:
                try:
                    return real_build(root, meta_cache, predicted_ctx)
                finally:
                    self._restore()

            monkeypatch.setattr(server_module, "_build_static_graph", _build)

    def _lookup(self, cache: CacheManager, path: Path) -> dict[str, Any] | None:
        if self.armed.is_set() and path.name == self.victim.name:
            self.armed.clear()
            self.fired_on_thread = threading.current_thread().name
            st = self.victim.stat()
            self._saved = (self.victim.read_bytes(), st.st_atime_ns, st.st_mtime_ns)
            self.victim.unlink()
            self.fired.set()
        return self._real_get(cache, path)

    def _restore(self) -> None:
        if self._saved is not None:
            data, atime_ns, mtime_ns = self._saved
            self._saved = None
            self.victim.write_bytes(data)
            os.utime(self.victim, ns=(atime_ns, mtime_ns))


def _three_file_project(root: Path) -> Path:
    (root / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    (root / "b.py").write_text("def g():\n    pass\n", encoding="utf-8")
    victim = root / "c.py"
    victim.write_text("def h():\n    pass\n", encoding="utf-8")
    return victim


_A_WITH_F2 = "def f():\n    pass\n\n\ndef f2():\n    pass\n"


# ---------------------------------------------------------------------------
# A file vanishing mid-rebuild
# ---------------------------------------------------------------------------


async def test_watch_loop_recovers_from_a_file_deleted_mid_rebuild(
    start_server: StartServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file deleted while a watch rebuild is parsing must not end watch mode,
    and the next graph a client receives must carry both the edit that
    triggered the rebuild and the deletion.

    Today the vanished file makes the whole rebuild fail (see the ledgered
    root cause below), the loop drops it, and the watcher's next tick sees the
    deletion and rebuilds — one tick late, but correct. The assertions hold
    for that path and for a fixed parser that skips the vanished file in the
    first rebuild; they fail if a failed rebuild escapes the loop or ends it.
    """
    victim = _three_file_project(tmp_path)
    hook = _VanishingLookup(victim)
    hook.install(monkeypatch)
    task, port = await _start_watch_server(start_server, tmp_path)
    try:
        async with connect(f"ws://127.0.0.1:{port}") as ws:
            first = await _recv_graph(ws)
            assert "c.py:h" in _node_ids(first)

            hook.armed.set()
            (tmp_path / "a.py").write_text(_A_WITH_F2, encoding="utf-8")

            after_edit = await _recv_graph(ws, timeout=5.0)
            assert hook.fired.is_set(), "the deletion never landed inside a rebuild"
            assert hook.fired_on_thread.startswith("grackle-watch-rebuild"), hook.fired_on_thread
            ids = _node_ids(after_edit)
            assert "a.py:f2" in ids, "the edit that triggered the rebuild never reached the client"
            assert not any(i.startswith("c.py") for i in ids), sorted(ids)

            # Still alive: a later, unrelated edit is picked up and broadcast.
            (tmp_path / "b.py").write_text(
                "def g():\n    pass\n\n\ndef g2():\n    pass\n", encoding="utf-8"
            )
            later = await _recv_graph(ws, timeout=5.0)
            later_ids = _node_ids(later)
            assert "b.py:g2" in later_ids
            assert "a.py:f2" in later_ids
            assert not any(i.startswith("c.py") for i in later_ids)
    finally:
        await _stop_server(task)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "T6-4: a watch rebuild aborted by a file vanishing mid-parse is dropped "
        "after the watcher has already consumed the edit that triggered it, so a "
        "byte-identical restore leaves every client stale indefinitely "
        "(docs/test-campaigns/phase-12.md)"
    ),
)
async def test_watch_edit_survives_a_file_blinking_mid_rebuild(
    start_server: StartServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that is absent while a rebuild parses it, and back with the same
    bytes before the next tick, must not cost the client the edit that
    triggered the rebuild.

    Realistic sources of such a blink: an editor that saves by renaming the
    original aside and writing a new file, ``git stash && git stash pop``, a
    codegen step that cleans its output directory and regenerates it
    unchanged.

    The mechanism: the vanished file makes ``_build_static_graph`` return
    ``None`` (see the ledgered parse test below), ``_watch_loop`` skips the
    broadcast, and the watcher's snapshot has already advanced past the edit
    to ``a.py``. The next tick sees ``c.py`` unchanged — same bytes — so
    nothing is ever rebuilt again until an unrelated edit happens along.

    Expected: once the edits stop, the client holds the graph a fresh parse of
    the disk would produce (``a.py:f2`` present, ``c.py:h`` present). Making
    the parser skip the vanished file is not enough on its own: that rebuild
    would broadcast a graph *without* ``c.py``, which the next tick again sees
    no reason to replace. A fix has to make the watcher re-examine what a
    failed or partial rebuild could not read.

    Precondition failures raise ``RuntimeError`` so they cannot pass for the
    expected ``AssertionError``.
    """
    victim = _three_file_project(tmp_path)
    hook = _VanishingLookup(victim, blink=True)
    hook.install(monkeypatch)
    task, port = await _start_watch_server(start_server, tmp_path)
    try:
        async with connect(f"ws://127.0.0.1:{port}") as ws:
            first = await _recv_graph(ws)
            if "c.py:h" not in _node_ids(first):
                raise RuntimeError(f"probe precondition: initial graph lacks c.py:h: {first}")

            hook.armed.set()
            (tmp_path / "a.py").write_text(_A_WITH_F2, encoding="utf-8")
            if not await _wait_for(hook.fired, 5.0):
                raise RuntimeError("probe precondition: the blink never landed inside a rebuild")
            if victim.read_text(encoding="utf-8") != "def h():\n    pass\n":
                raise RuntimeError("probe precondition: c.py was not restored")

            # 40 poll ticks: ample time for any retry a fix introduces.
            deadline = time.monotonic() + 2.0
            latest: set[str] | None = None
            while (remaining := deadline - time.monotonic()) > 0:
                try:
                    latest = _node_ids(await _recv_graph(ws, timeout=remaining))
                except TimeoutError:
                    break
                if {"a.py:f2", "c.py:h"} <= latest:
                    break
            assert latest is not None, (
                "no static_graph reached the client after the edit: the rebuild the "
                "blink aborted was dropped and the watcher never retried it"
            )
            assert {"a.py:f2", "c.py:h"} <= latest, sorted(latest)
    finally:
        await _stop_server(task)


@pytest.mark.xfail(
    strict=True,
    raises=FileNotFoundError,
    reason=(
        "T6-4: a file that vanishes between the walker's enumeration and "
        "CacheManager.get's hash raises FileNotFoundError out of the whole parse "
        "instead of being skipped like a read error (docs/test-campaigns/phase-12.md)"
    ),
)
@pytest.mark.parametrize(
    ("adapter_cls", "ext", "keep", "gone"),
    [
        pytest.param(
            PythonStaticParser,
            ".py",
            "def f():\n    pass\n",
            "def g():\n    pass\n",
            id="python",
        ),
        pytest.param(
            TypeScriptStaticParser,
            ".ts",
            "export function f() {}\n",
            "export function g() {}\n",
            id="tree-sitter",
        ),
    ],
)
def test_parse_skips_a_file_that_vanishes_before_its_cache_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    adapter_cls: Callable[[], PythonStaticParser | TypeScriptStaticParser],
    ext: str,
    keep: str,
    gone: str,
) -> None:
    """The root cause under the watch-mode finding above, one layer down.

    Both walkers already treat an unreadable file as a warning, not a failed
    parse (``except OSError`` around ``read_bytes()`` → ``"read error"``). But
    each first calls ``CacheManager.get(path)``, whose ``_hash_file`` opens the
    file with no guard, so a file that vanished after ``rglob`` listed it
    raises out of ``walk()`` before that guard is reached. ``_build_static_graph``
    turns the exception into ``None``: a watch rebuild is dropped, and a
    client connecting at that moment receives no ``static_graph`` at all.

    Expected: the parse returns the graph of what is on disk — the surviving
    file present, the vanished one absent.
    """
    (tmp_path / f"a{ext}").write_text(keep, encoding="utf-8")
    victim = tmp_path / f"b{ext}"
    victim.write_text(gone, encoding="utf-8")
    hook = _VanishingLookup(victim)
    hook.install(monkeypatch)
    hook.armed.set()

    graph = adapter_cls().parse(tmp_path, ParseOptions())

    assert hook.fired.is_set()
    ids = {str(n["id"]) for n in graph["nodes"]}
    assert f"a{ext}" in ids
    assert not any(i.startswith(f"b{ext}") for i in ids), sorted(ids)


# ---------------------------------------------------------------------------
# Rebuild serialization
# ---------------------------------------------------------------------------


class _RebuildRecorder:
    """Wraps ``_build_static_graph`` and records how many run at once.

    With ``block_first`` the first call parks until ``release`` is set, which
    holds a rebuild in flight for as long as the test needs to land edits
    under it. ``delay`` stretches every other call.
    """

    def __init__(
        self,
        real: Callable[..., Any],
        *,
        block_first: bool = False,
        delay: float = 0.0,
    ) -> None:
        self._real = real
        self._block_first = block_first
        self._delay = delay
        self._lock = threading.Lock()
        self.calls = 0
        self.in_flight = 0
        self.max_in_flight = 0
        self.first_started = threading.Event()
        self.release = threading.Event()

    def __call__(self, root: Path, meta_cache: Any, predicted_ctx: Any) -> Any:
        with self._lock:
            self.calls += 1
            nth = self.calls
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if nth == 1 and self._block_first:
                self.first_started.set()
                self.release.wait(10.0)
            elif self._delay:
                time.sleep(self._delay)
            return self._real(root, meta_cache, predicted_ctx)
        finally:
            with self._lock:
                self.in_flight -= 1


async def test_watch_rebuilds_never_overlap_when_edits_land_mid_rebuild(
    start_server: StartServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two edits landing while a watch rebuild is still running must not start
    a second, overlapping ``_build_static_graph``; they must be rebuilt after
    it, not dropped.

    The first rebuild is parked (not slowed) so the window is held open for
    ten poll ticks deterministically. A loop that submitted rebuilds without
    awaiting them, on an executor with more than one worker, would start the
    second rebuild inside that window.
    """
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("def f():\n    pass\n", encoding="utf-8")
    b.write_text("def g():\n    pass\n", encoding="utf-8")
    task, port = await _start_watch_server(start_server, tmp_path)
    recorder = _RebuildRecorder(server_module._build_static_graph, block_first=True)
    try:
        async with connect(f"ws://127.0.0.1:{port}") as ws:
            await _recv_graph(ws)
            monkeypatch.setattr(server_module, "_build_static_graph", recorder)

            try:
                a.write_text(_A_WITH_F2, encoding="utf-8")
                assert await _wait_for(recorder.first_started, 5.0), "no rebuild started"
                # Two more edits land while rebuild 1 is parked; each changes
                # the file's size so no mtime granularity can hide it.
                b.write_text("def g():\n    pass\n\n\ndef g2():\n    pass\n", encoding="utf-8")
                await asyncio.sleep(0.1)
                a.write_text(_A_WITH_F2 + "\n\ndef f3():\n    pass\n", encoding="utf-8")
                await asyncio.sleep(10 * _INTERVAL)
                calls_while_parked = recorder.calls
                max_while_parked = recorder.max_in_flight
            finally:
                recorder.release.set()

            await _recv_graph(ws, timeout=5.0)  # rebuild 1
            last = await _recv_graph(ws, timeout=5.0)  # the edits that landed under it
    finally:
        recorder.release.set()
        await _stop_server(task)

    assert calls_while_parked == 1, (
        f"{calls_while_parked} rebuilds started while the first was still running"
    )
    assert max_while_parked == 1
    assert recorder.max_in_flight == 1
    assert recorder.calls >= 2, "the edits made during rebuild 1 were never rebuilt"
    assert {"a.py:f3", "b.py:g2"} <= _node_ids(last)


@pytest.mark.hammer
async def test_watch_rebuilds_never_overlap_hammer(
    start_server: StartServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Many rapid edits at random spacing, every rebuild stretched to widen any
    overlap window: the in-flight count must stay at one throughout, and the
    last edit must still reach the client."""
    rng = random.Random(0x7604)
    target = tmp_path / "a.py"
    target.write_text("def f0():\n    pass\n", encoding="utf-8")
    task, port = await _start_watch_server(start_server, tmp_path)
    recorder = _RebuildRecorder(server_module._build_static_graph, delay=0.03)
    edits = 80
    try:
        async with connect(f"ws://127.0.0.1:{port}") as ws:
            await _recv_graph(ws)
            monkeypatch.setattr(server_module, "_build_static_graph", recorder)
            for i in range(1, edits + 1):
                body = "".join(f"def f{k}():\n    pass\n\n\n" for k in range(i + 1))
                target.write_text(body, encoding="utf-8")
                await asyncio.sleep(rng.uniform(0.0, 2 * _INTERVAL))

            want = f"a.py:f{edits}"
            deadline = time.monotonic() + 15.0
            seen = False
            while not seen and (remaining := deadline - time.monotonic()) > 0:
                try:
                    seen = want in _node_ids(await _recv_graph(ws, timeout=remaining))
                except TimeoutError:
                    break
    finally:
        await _stop_server(task)

    assert seen, f"{want} never reached the client"
    assert recorder.max_in_flight == 1, recorder.max_in_flight
    assert recorder.calls >= 2, recorder.calls
