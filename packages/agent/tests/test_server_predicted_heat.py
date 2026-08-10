"""Tests for `metadata.predicted_heat` injection into the pushed static graph
(Phase 12.2). Extends the harness style of test_server_static_graph_push.py
and test_server_watch.py.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from websockets.asyncio.client import connect

from grackle import ml_bridge
from grackle.adapters import registry
from grackle.adapters.base import ParseOptions
from grackle.python_runtime.aggregates import TraceAggregates
from grackle.server import serve

if TYPE_CHECKING:
    from collections.abc import Iterator

_TINY_PYTHON_APP = Path(__file__).parent.parent.parent.parent / "fixtures" / "tiny-python-app"


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    ml_bridge.reset_cache()
    yield
    ml_bridge.reset_cache()


@pytest.fixture
def trained_model(tmp_path: Path) -> Path:
    adapter = registry.get_static("python")
    assert adapter is not None
    graph = adapter.parse(_TINY_PYTHON_APP, ParseOptions())
    agg = TraceAggregates.build(_TINY_PYTHON_APP / "trace.golden.jsonl")
    heat = agg.cumulative_heat_all(len(agg))
    heat.pop("<unresolved>", None)
    out = tmp_path / "heat-model.npz"
    ml_bridge.train_and_save(graph, heat, epochs=3, seed=0, out=out)
    return out


def _bump_mtime_forward(path: Path, seconds: float = 5.0) -> None:
    """Force ``path``'s mtime forward by ``seconds`` — guaranteed to differ
    from whatever it was before, even on a filesystem/CI runner whose mtime
    resolution is too coarse to distinguish two back-to-back writes (mirrors
    ``tests/test_watcher.py``'s helper of the same name; see its docstring
    for the specific Windows-CI hazard this avoids — a naive sleep()-then-
    touch() was found flaky on at least one runner for exactly this cache)."""
    current_ns = path.stat().st_mtime_ns
    new_ns = current_ns + int(seconds * 1_000_000_000)
    os.utime(path, ns=(new_ns, new_ns))


def _free_port() -> int:
    """A second free port, independent of the `free_port` fixture (which can
    only be requested once per test via normal fixture injection)."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def _recv_json(ws: Any, timeout: float = 5.0) -> dict[str, Any]:
    raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
    result: dict[str, Any] = json.loads(raw)
    return result


async def _start_server(
    port: int, root: Path = _TINY_PYTHON_APP, **kwargs: Any
) -> asyncio.Task[None]:
    task = asyncio.create_task(serve("127.0.0.1", port, root=root, **kwargs))
    await asyncio.sleep(0.05)
    return task


async def _stop_server(task: asyncio.Task[None]) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# presence / absence
# ---------------------------------------------------------------------------


async def test_predicted_heat_present_with_model(free_port: int, trained_model: Path) -> None:
    task = await _start_server(free_port, model_path=trained_model)
    try:
        async with connect(f"ws://127.0.0.1:{free_port}") as ws:
            data = await _recv_json(ws)
        payload = data["payload"]
        assert "predicted_heat" in payload["metadata"]
        ph = payload["metadata"]["predicted_heat"]
        assert ph["model_version"] == 1
        node_ids = {n["id"] for n in payload["nodes"]}
        assert {s["node_id"] for s in ph["scores"]} == node_ids
        for s in ph["scores"]:
            assert 0.0 <= s["score"] <= 1.0
    finally:
        await _stop_server(task)


async def test_predicted_heat_absent_without_model(free_port: int) -> None:
    task = await _start_server(free_port)
    try:
        async with connect(f"ws://127.0.0.1:{free_port}") as ws:
            data = await _recv_json(ws)
        assert sorted(data["payload"]["metadata"].keys()) == ["cycles", "hub_score"]
    finally:
        await _stop_server(task)


async def test_predicted_heat_byte_identity_absent_vs_gate_closed_with_model(
    free_port: int, trained_model: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The serialized payload with no model configured equals the payload
    with a model configured but the ML gate forced closed — absence is
    byte-identical regardless of WHY predicted_heat is missing."""
    task_absent = await _start_server(free_port)
    try:
        async with connect(f"ws://127.0.0.1:{free_port}") as ws:
            absent = await _recv_json(ws)
    finally:
        await _stop_server(task_absent)

    monkeypatch.setattr(ml_bridge, "learn_available", lambda: False)
    port2 = _free_port()
    task_gated = await _start_server(port2, model_path=trained_model)
    try:
        async with connect(f"ws://127.0.0.1:{port2}") as ws:
            gated = await _recv_json(ws)
    finally:
        await _stop_server(task_gated)

    assert sorted(gated["payload"]["metadata"].keys()) == ["cycles", "hub_score"]
    assert json.dumps(absent["payload"], sort_keys=True) == json.dumps(
        gated["payload"], sort_keys=True
    )


def test_byte_identity_comparison_is_discriminating() -> None:
    """Mutation check: proves the sort_keys=True payload comparison above
    would actually catch a leaked predicted_heat key, not pass vacuously."""
    baseline = {"metadata": {"hub_score": [], "cycles": []}, "nodes": [], "edges": []}
    leaked = {
        "metadata": {"hub_score": [], "cycles": [], "predicted_heat": None},
        "nodes": [],
        "edges": [],
    }
    assert json.dumps(baseline, sort_keys=True) != json.dumps(leaked, sort_keys=True)


# ---------------------------------------------------------------------------
# caching
# ---------------------------------------------------------------------------


async def test_predicted_heat_cached_across_connects_recomputed_on_mtime_bump(
    free_port: int, trained_model: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []
    real_predict = ml_bridge.predict_scores

    def _counting_predict(graph: Any, model_path: Path) -> dict[str, Any]:
        calls.append(model_path)
        result: dict[str, Any] = real_predict(graph, model_path)
        return result

    monkeypatch.setattr(ml_bridge, "predict_scores", _counting_predict)

    task = await _start_server(free_port, model_path=trained_model)
    try:
        async with connect(f"ws://127.0.0.1:{free_port}") as ws:
            await _recv_json(ws)
        async with connect(f"ws://127.0.0.1:{free_port}") as ws:
            await _recv_json(ws)
        assert len(calls) == 1, "second connect with an unchanged model must hit the cache"

        _bump_mtime_forward(trained_model)  # guaranteed-distinct mtime, not wall-clock drift

        async with connect(f"ws://127.0.0.1:{free_port}") as ws:
            await _recv_json(ws)
        assert len(calls) == 2, "an mtime bump must bust the cache and recompute"
    finally:
        await _stop_server(task)


async def test_predicted_heat_invalidates_on_rename_with_unchanged_edge_topology(
    free_port: int, tmp_path: Path, trained_model: Path
) -> None:
    """Regression: the predicted-heat cache key must be sensitive to node
    identity, not just edge topology. A rename with no edges attached to the
    renamed node leaves _graph_signature (edge-topology-only, used for
    hub_score/cycles) unchanged — if predicted_heat's cache reused that
    signature, a stale scores list referencing the OLD node_id would be
    reattached to the new graph, which no longer has that node at all."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")

    task = await _start_server(free_port, root=root, model_path=trained_model)
    try:
        async with connect(f"ws://127.0.0.1:{free_port}") as ws:
            first = await _recv_json(ws)
        first_ph = first["payload"]["metadata"]["predicted_heat"]
        first_node_ids = {n["id"] for n in first["payload"]["nodes"]}
        first_score_ids = {s["node_id"] for s in first_ph["scores"]}
        assert first_score_ids == first_node_ids
        assert "a.py:f" in first_node_ids

        # Rename f -> g with a blank-line shift (line changes too) — zero
        # edges before or after, so _graph_signature (edge-count/edge-hash
        # only) is IDENTICAL for both graphs: (2 nodes, 0 edges, checksum 0).
        (root / "a.py").write_text("\n\n\ndef g():\n    pass\n", encoding="utf-8")

        # New server instance (not --watch) so this proves the bug reproduces
        # on a plain reconnect after an edit, not only under watch mode.
    finally:
        await _stop_server(task)

    port2 = _free_port()
    task2 = await _start_server(port2, root=root, model_path=trained_model)
    try:
        async with connect(f"ws://127.0.0.1:{port2}") as ws:
            second = await _recv_json(ws)
    finally:
        await _stop_server(task2)

    second_ph = second["payload"]["metadata"]["predicted_heat"]
    second_node_ids = {n["id"] for n in second["payload"]["nodes"]}
    assert "a.py:g" in second_node_ids
    assert "a.py:f" not in second_node_ids

    second_score_ids = {s["node_id"] for s in second_ph["scores"]}
    assert second_score_ids == second_node_ids, (
        f"predicted_heat.scores must reference the CURRENT graph's node_ids, "
        f"not stale ones from before the rename: got {second_score_ids}, "
        f"expected {second_node_ids}"
    )


# ---------------------------------------------------------------------------
# broken model
# ---------------------------------------------------------------------------


async def test_predicted_heat_corrupt_model_no_key_no_crash(
    free_port: int, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.npz"
    bad.write_bytes(b"not a real npz file")

    task = await _start_server(free_port, model_path=bad)
    try:
        async with connect(f"ws://127.0.0.1:{free_port}") as ws:
            data = await _recv_json(ws)
        assert "predicted_heat" not in data["payload"]["metadata"]
    finally:
        await _stop_server(task)

    out = capsys.readouterr().out
    assert "predicted_heat prediction failed" in out


async def test_predicted_heat_gate_closed_with_model_warns_once_not_per_connect(
    free_port: int,
    trained_model: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Regression: a model configured against an unavailable/broken
    grackle-nn install must log its warning ONCE, not on every connect —
    mirroring the corrupt-model branch's caching of the (known-bad)
    outcome, which this branch previously lacked."""
    monkeypatch.setattr(ml_bridge, "learn_available", lambda: False)

    task = await _start_server(free_port, model_path=trained_model)
    try:
        for _ in range(3):
            async with connect(f"ws://127.0.0.1:{free_port}") as ws:
                data = await _recv_json(ws)
            assert "predicted_heat" not in data["payload"]["metadata"]
    finally:
        await _stop_server(task)

    out = capsys.readouterr().out
    assert out.count("predicted_heat unavailable") == 1


async def test_predicted_heat_missing_model_file_no_key_no_warning(
    free_port: int, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unconfigured/never-created model is the common case, not a fault —
    it must not log a warning on every connect."""
    never_created = tmp_path / "does-not-exist.npz"

    task = await _start_server(free_port, model_path=never_created)
    try:
        async with connect(f"ws://127.0.0.1:{free_port}") as ws:
            data = await _recv_json(ws)
        assert "predicted_heat" not in data["payload"]["metadata"]
    finally:
        await _stop_server(task)

    out = capsys.readouterr().out
    assert "predicted_heat" not in out


# ---------------------------------------------------------------------------
# watch mode
# ---------------------------------------------------------------------------


async def test_watch_mode_rebroadcast_carries_predicted_heat(
    free_port: int, tmp_path: Path, trained_model: Path
) -> None:
    """The discriminating test for the A1 revision: predicted_heat must be
    injected from _build_static_graph itself, not just the connect-time
    _push_static_graph — otherwise a watch-mode re-broadcast would silently
    drop it."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.py").write_text("def f():\n    pass\n", encoding="utf-8")

    task = asyncio.create_task(
        serve(
            "127.0.0.1",
            free_port,
            root=root,
            watch=True,
            watch_interval=0.1,
            watch_poll=True,
            model_path=trained_model,
        )
    )
    await asyncio.sleep(0.05)
    try:
        async with connect(f"ws://127.0.0.1:{free_port}") as ws:
            first = await _recv_json(ws)
            assert first["type"] == "static_graph"
            # A trained-on-tiny-python-app model scored against this
            # different tiny fixture: predicted_heat still injects (feature
            # extraction works on ANY graph), even though the node_ids won't
            # match the training graph's — coverage is what's asserted here,
            # not score accuracy.
            assert "predicted_heat" in first["payload"]["metadata"]

            (root / "b.py").write_text("def g():\n    pass\n", encoding="utf-8")

            second = await _recv_json(ws, timeout=5.0)
            assert second["type"] == "static_graph"
            assert len(second["payload"]["nodes"]) > len(first["payload"]["nodes"])
            assert "predicted_heat" in second["payload"]["metadata"]
            second_scores = second["payload"]["metadata"]["predicted_heat"]["scores"]
            assert len(second_scores) == len(second["payload"]["nodes"])
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
