"""End-to-end tests for `grackle serve --watch` (ADR-0027)."""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import json
import threading
import time
from typing import TYPE_CHECKING, Any

from websockets.asyncio.client import connect

import grackle.server as server_module
from grackle.server import serve

if TYPE_CHECKING:
    from pathlib import Path


async def _recv_json(ws: Any, timeout: float = 5.0) -> dict[str, Any]:
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    result: dict[str, Any] = json.loads(raw)
    return result


async def _start_watch_server(free_port: int, root: Path, watch_interval: float = 0.1) -> Any:
    """Start `serve(watch=True, watch_poll=True)` and wait for it to come up.

    `watch_poll=True` forces the deterministic stdlib poller regardless of
    whether the optional `watchfiles` package is installed, so these tests
    aren't sensitive to which backend happens to be available.

    Files under `root` must already exist *before* calling this — the watch
    task primes its baseline snapshot once at startup, so any file present
    at this point is part of the baseline (not a "change" the watcher will
    ever report), and only edits made *after* this returns are observable.
    """
    task = asyncio.create_task(
        serve(
            "127.0.0.1",
            free_port,
            root=root,
            watch=True,
            watch_interval=watch_interval,
            watch_poll=True,
        )
    )
    await asyncio.sleep(0.05)
    return task


async def _stop_server(task: Any) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_watch_add_file_rebroadcasts_growth(free_port: int, tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    task = await _start_watch_server(free_port, tmp_path)
    try:
        async with connect(f"ws://127.0.0.1:{free_port}") as ws:
            first = await _recv_json(ws)
            assert first["type"] == "static_graph"
            first_nodes = len(first["payload"]["nodes"])

            (tmp_path / "b.py").write_text("def g():\n    pass\n", encoding="utf-8")

            second = await _recv_json(ws, timeout=5.0)
            assert second["type"] == "static_graph"
            assert len(second["payload"]["nodes"]) > first_nodes
    finally:
        await _stop_server(task)


async def test_watch_identical_rewrite_produces_no_push(free_port: int, tmp_path: Path) -> None:
    f = tmp_path / "a.py"
    f.write_text("def f():\n    pass\n", encoding="utf-8")
    task = await _start_watch_server(free_port, tmp_path)
    try:
        async with connect(f"ws://127.0.0.1:{free_port}") as ws:
            first = await _recv_json(ws)
            assert first["type"] == "static_graph"

            content = f.read_text(encoding="utf-8")
            f.write_text(content, encoding="utf-8")  # byte-identical rewrite

            # Several poll ticks' worth of quiet time, then confirm via a
            # ping/pong interleave that no static_graph snuck through —
            # mirrors test_server_static_graph_push.py's "ping still works"
            # trick, used here to prove the *absence* of a push.
            await asyncio.sleep(0.4)
            await ws.send(json.dumps({"id": "p1", "type": "ping", "payload": {}}))
            reply = await _recv_json(ws, timeout=2.0)
            assert reply["type"] == "pong"
            assert reply["id"] == "p1"
    finally:
        await _stop_server(task)


async def test_watch_delete_file_rebroadcasts_shrink(free_port: int, tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    b = tmp_path / "b.py"
    b.write_text("def g():\n    pass\n", encoding="utf-8")
    task = await _start_watch_server(free_port, tmp_path)
    try:
        async with connect(f"ws://127.0.0.1:{free_port}") as ws:
            first = await _recv_json(ws)
            assert first["type"] == "static_graph"
            first_nodes = len(first["payload"]["nodes"])

            b.unlink()

            second = await _recv_json(ws, timeout=5.0)
            assert second["type"] == "static_graph"
            assert len(second["payload"]["nodes"]) < first_nodes
    finally:
        await _stop_server(task)


async def test_watch_broadcasts_to_all_connected_clients(free_port: int, tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    task = await _start_watch_server(free_port, tmp_path)
    try:
        async with (
            connect(f"ws://127.0.0.1:{free_port}") as ws1,
            connect(f"ws://127.0.0.1:{free_port}") as ws2,
        ):
            first1 = await _recv_json(ws1)
            first2 = await _recv_json(ws2)
            assert first1["type"] == "static_graph"
            assert first2["type"] == "static_graph"

            (tmp_path / "b.py").write_text("def g():\n    pass\n", encoding="utf-8")

            second1 = await _recv_json(ws1, timeout=5.0)
            second2 = await _recv_json(ws2, timeout=5.0)
            assert second1["type"] == "static_graph"
            assert second2["type"] == "static_graph"
            assert len(second1["payload"]["nodes"]) > len(first1["payload"]["nodes"])
            assert len(second2["payload"]["nodes"]) > len(first2["payload"]["nodes"])
    finally:
        await _stop_server(task)


async def test_watch_shutdown_with_pending_change_does_not_hang(
    free_port: int, tmp_path: Path
) -> None:
    """Cancelling `serve()` must cancel+reap the watch task, never hang (ADR-0027 guard #2)."""
    (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    # A long interval guarantees the watch loop is parked in its poll sleep
    # (not mid-tick) at the moment we cancel — the scenario most likely to
    # hang if the watch task were never cancelled in serve()'s finally.
    task = await _start_watch_server(free_port, tmp_path, watch_interval=5.0)

    (tmp_path / "b.py").write_text("def g():\n    pass\n", encoding="utf-8")  # a pending change

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5.0)
    assert task.done()


async def test_watch_rebuild_cancellation_does_not_block_shutdown(
    free_port: int, tmp_path: Path, monkeypatch: Any
) -> None:
    """Cancelling serve() WHILE a rebuild is genuinely in-flight must not block on it.

    Regression test for a review finding: the watch-triggered rebuild used to run
    fully inline on the event loop with zero await points, so cancellation couldn't
    be delivered until a slow parse finished (empirically ~3s in the review's repro).
    It now runs on a dedicated executor (server.py's watch_executor), so serve()'s
    own asyncio-level shutdown sequence completes promptly even while the
    underlying thread is still working on the parse.

    This measures TOTAL wall-clock time across a FIXED sequence (write -> a
    fixed short await -> cancel -> confirm done) rather than trying to detect
    "the rebuild has started" first. That's deliberate: under the buggy
    (inline) version, the blocking call freezes the ENTIRE event loop, so ANY
    coroutine sharing that loop — including a poll loop meant to detect
    "started", and even a `run_in_executor`-based one, since resuming from it
    still requires the same loop to process the completion callback — would
    itself stall until the blocking call finishes, making a "wait for start,
    then time the cancel" test pass for the wrong reason (nothing left to
    cancel by the time it measures) regardless of whether the fix works. A
    fixed-length await is not fooled by this: under the fix, the whole
    sequence takes about as long as the fixed wait plus a prompt cancel; under
    the bug, the fixed wait itself gets swallowed by the in-flight blocking
    rebuild once the watch loop's poll tick starts it, inflating the total
    toward the full ~2s parse duration — exactly the symptom under test.
    """
    real_build = server_module._build_static_graph
    rebuild_done = threading.Event()

    def _slow_build(root: Any, meta_cache: Any) -> Any:
        try:
            time.sleep(2.0)
            return real_build(root, meta_cache)
        finally:
            # Signals the background thread has actually finished, so this
            # test can wait for it before returning — otherwise the orphaned
            # thread (a real, intended side effect of wait=False shutdown)
            # keeps running for ~1.7s into whichever test runs next in the
            # same pytest session, a latent source of cross-test flakiness.
            rebuild_done.set()

    (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    task = await _start_watch_server(free_port, tmp_path, watch_interval=0.1)

    async with connect(f"ws://127.0.0.1:{free_port}") as ws:
        await _recv_json(ws)  # initial static_graph — unpatched, so this is fast

        # Patch only AFTER the initial connect-time push, so only the
        # subsequent WATCH-triggered rebuild is made slow.
        monkeypatch.setattr(server_module, "_build_static_graph", _slow_build)

        t0 = time.monotonic()
        (tmp_path / "b.py").write_text("def g():\n    pass\n", encoding="utf-8")

        # A fixed, generous wait for the watch loop's 0.1s poll interval to
        # notice the change and begin the (now slow) rebuild — not a
        # start-detection poll; see the docstring for why that would be
        # unreliable here.
        await asyncio.sleep(0.3)

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5.0)
        elapsed = time.monotonic() - t0

    assert task.done()
    # Fixed: ~0.3s wait + a prompt cancel, comfortably under 1s. Buggy
    # (inline): the 0.3s wait itself gets swallowed by the in-flight blocking
    # rebuild, pushing the total toward the full ~2s parse duration.
    assert elapsed < 1.0

    # Let the orphaned background thread actually finish before this test
    # function returns, so it can't overlap with (and add flaky CPU/disk
    # contention to) whatever test runs next in the same pytest session.
    loop = asyncio.get_running_loop()
    finished_in_time = await loop.run_in_executor(None, rebuild_done.wait, 5.0)
    assert finished_in_time, "orphaned watch-rebuild thread never finished"


async def test_watch_uses_a_dedicated_executor_not_the_loop_default(
    free_port: int, tmp_path: Path, monkeypatch: Any
) -> None:
    """Regression test for a review finding: test_watch_rebuild_cancellation_does_not_block_shutdown
    cannot itself discriminate "a dedicated executor" from "loop.run_in_executor(None, ...)" (the
    loop's shared default executor) — the specific hazard the latter reintroduces
    (asyncio's shutdown_default_executor() blocking on outstanding default-executor work) only
    surfaces when the event loop/Runner itself is torn down, which for a pytest-asyncio test
    happens in a fixture-teardown phase AFTER the test body's own assertions already passed.
    So instead of timing an indirect side effect, this directly verifies the STRUCTURAL property
    the fix depends on: serve() constructs a genuinely separate ThreadPoolExecutor for watch mode
    (with its own distinguishing thread_name_prefix), never passing None to run_in_executor.
    """
    created_kwargs: list[dict[str, Any]] = []
    real_init = concurrent.futures.ThreadPoolExecutor.__init__

    def _tracking_init(self: Any, *args: Any, **kwargs: Any) -> None:
        created_kwargs.append(kwargs)
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(concurrent.futures.ThreadPoolExecutor, "__init__", _tracking_init)

    (tmp_path / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")
    task = await _start_watch_server(free_port, tmp_path)
    try:
        async with connect(f"ws://127.0.0.1:{free_port}"):
            pass
    finally:
        await _stop_server(task)

    assert any(kw.get("thread_name_prefix") == "grackle-watch-rebuild" for kw in created_kwargs)
