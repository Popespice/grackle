"""Tests for `metadata.predicted_heat` injection into the pushed static graph
(Phase 12.2). Extends the harness style of test_server_static_graph_push.py
and test_server_watch.py.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time
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


async def _start_server(port: int, **kwargs: Any) -> asyncio.Task[None]:
    task = asyncio.create_task(serve("127.0.0.1", port, root=_TINY_PYTHON_APP, **kwargs))
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

        time.sleep(0.05)
        os.utime(trained_model, None)  # bump mtime without changing content

        async with connect(f"ws://127.0.0.1:{free_port}") as ws:
            await _recv_json(ws)
        assert len(calls) == 2, "an mtime bump must bust the cache and recompute"
    finally:
        await _stop_server(task)


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
