"""Integration tests for the session library + aggregation engine (Phase 8.3).

These exercise the full server over a real WebSocket with both a
``--trace-source`` and a ``--store``:

- ``static_graph`` carries agent-computed ``metadata.hub_score`` / ``cycles``.
- ``trace_query_request`` (cumulative_heat) returns per-node counts for the
  file-replay session.
- ``session_list_request`` returns the trace-source indexed into the store.
- ``session_load_request`` replays the stored session **and** that loaded
  session is itself queryable — the regression guard for loaded sessions
  losing their aggregates.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING, Any, cast

import pytest
from websockets.asyncio.client import connect

from grackle.server import serve
from grackle.session_store import SessionStore

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path


def _make_event(node_id: str, i: int) -> dict[str, Any]:
    return {
        "event": "call",
        "node_id": node_id,
        "ts_ns": i * 1_000_000,
        "thread_id": 1,
        "frame_depth": 0,
        "metadata": {},
    }


def _write_trace(path: Path) -> None:
    # A appears 3×, B 2× → deterministic cumulative-heat expectations.
    events = [
        _make_event("a.py:fn_a", 0),
        _make_event("a.py:fn_b", 1),
        _make_event("a.py:fn_a", 2),
        _make_event("a.py:fn_a", 3),
        _make_event("a.py:fn_b", 4),
    ]
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


async def _recv_until(ws: Any, type_: str, timeout: float = 5.0) -> dict[str, Any]:
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError(f"timed out waiting for {type_!r}")
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        msg = cast("dict[str, Any]", json.loads(raw))
        if msg["type"] == type_:
            return msg


async def _request(ws: Any, type_: str, payload: dict[str, Any], reply_type: str) -> dict[str, Any]:
    import uuid

    req_id = str(uuid.uuid4())
    await ws.send(json.dumps({"id": req_id, "type": type_, "payload": payload}))
    reply = await _recv_until(ws, reply_type)
    assert reply["id"] == req_id, f"reply id {reply['id']} != request id {req_id}"
    return reply


@pytest.fixture
async def server_with_store(
    free_port: int, tmp_path: Path
) -> AsyncGenerator[tuple[int, Path], None]:
    """Server started with a trace file + a session store."""
    trace_path = tmp_path / "trace.jsonl"
    _write_trace(trace_path)
    # A real Python source file so registry.detect(root) succeeds and a
    # static_graph (with agent-computed metadata) is pushed on connect.
    (tmp_path / "mod.py").write_text(
        "def fn_b():\n    return 1\n\n\ndef fn_a():\n    return fn_b()\n",
        encoding="utf-8",
    )
    store = SessionStore.open(tmp_path / "sessions.db")
    task = asyncio.create_task(
        serve(
            "127.0.0.1",
            free_port,
            root=tmp_path,
            trace_source=trace_path,
            pace=False,
            store=store,
        )
    )
    await asyncio.sleep(0.05)
    yield free_port, trace_path
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_trace_query_cumulative_heat(server_with_store: tuple[int, Path]) -> None:
    """trace_query_request(cumulative_heat) returns per-node counts for the replay session."""
    port, _ = server_with_store
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        start = await _recv_until(ws, "trace_session_start")
        sid = start["payload"]["session_id"]

        reply = await _request(
            ws,
            "trace_query_request",
            {"session_id": sid, "kind": "cumulative_heat", "at_index": 5},
            "trace_query_response",
        )
        assert reply["payload"].get("error") is None
        assert reply["payload"]["data"] == {"a.py:fn_a": 3, "a.py:fn_b": 2}


async def test_trace_query_unknown_session(server_with_store: tuple[int, Path]) -> None:
    """A query for an unknown session id returns an error response, not a crash."""
    port, _ = server_with_store
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        await _recv_until(ws, "trace_session_start")
        reply = await _request(
            ws,
            "trace_query_request",
            {"session_id": "does-not-exist", "kind": "cumulative_heat", "at_index": 5},
            "trace_query_response",
        )
        assert reply["payload"].get("error") == "session not found"


async def test_static_graph_has_agent_metadata(server_with_store: tuple[int, Path]) -> None:
    """static_graph carries agent-computed hub_score + cycles in metadata."""
    port, _ = server_with_store
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        graph_msg = await _recv_until(ws, "static_graph")
        metadata = graph_msg["payload"].get("metadata", {})
        assert "hub_score" in metadata
        assert "cycles" in metadata
        assert isinstance(metadata["hub_score"], list)
        # Agent hub entries are the compact {node_id, score} wire form.
        for entry in metadata["hub_score"]:
            assert set(entry) == {"node_id", "score"}


async def test_session_list_includes_trace_source(server_with_store: tuple[int, Path]) -> None:
    """The --trace-source file is indexed into the store and listed."""
    port, trace_path = server_with_store
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        await _recv_until(ws, "trace_session_start")
        reply = await _request(ws, "session_list_request", {}, "session_list_response")
        sessions = reply["payload"]["sessions"]
        assert len(sessions) == 1
        assert sessions[0]["label"] == trace_path.name
        assert sessions[0]["event_count"] == 5


async def test_loaded_session_is_queryable(server_with_store: tuple[int, Path]) -> None:
    """A session loaded from the store supports cumulative-heat queries (regression).

    Guards the bug where loaded sessions got a JsonlIndex but no TraceAggregates
    and a session_id that did not match, so cumulative-heat silently failed.
    """
    port, _ = server_with_store
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        await _recv_until(ws, "trace_session_start")  # the auto-replay session

        listed = await _request(ws, "session_list_request", {}, "session_list_response")
        stored_id = listed["payload"]["sessions"][0]["id"]

        # Load it — the agent replays it as a fresh seekable session.
        await ws.send(
            json.dumps(
                {
                    "id": "load-1",
                    "type": "session_load_request",
                    "payload": {"session_id": stored_id},
                }
            )
        )
        loaded_start = await _recv_until(ws, "trace_session_start")
        assert loaded_start["payload"]["session_id"] == stored_id
        assert loaded_start["payload"].get("seekable") is True

        # The loaded session must answer cumulative-heat queries, just like replay.
        reply = await _request(
            ws,
            "trace_query_request",
            {"session_id": stored_id, "kind": "cumulative_heat", "at_index": 5},
            "trace_query_response",
        )
        assert reply["payload"].get("error") is None
        assert reply["payload"]["data"] == {"a.py:fn_a": 3, "a.py:fn_b": 2}
