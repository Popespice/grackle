"""Unit tests for grackle.protocol — every wire-message builder (Phase 8.6, A9).

protocol.py had no dedicated test (covered only indirectly via server tests).
Each builder is asserted to:
  - emit valid JSON,
  - carry the exact 'type' discriminator,
  - include the schema-required payload fields with correct values,
  - use the right 'id' (echoed request id vs fresh UUID),
  - round-trip through parse_envelope without raising,
  - validate against packages/shared-types/schema/messages.schema.json (the
    single source of truth for the wire format).

This file also folds in the Python side of the schema<->builder parity guard
(Phase 8.6, A1): a SET-PARTITION check that the schema's message-type consts
are exactly (the set of types builders emit) U REQUEST_ONLY_TYPES, disjoint and
jointly exhaustive. Builders are discovered by introspection so a NEW make_*
that nobody wired into the test trips a failure (anti-rot guard).

NOTE: protocol.py's make_* enumeration is also consumed by the wire-parity
check. If a builder is added/removed/renamed, update the tables below in
lockstep.
"""

from __future__ import annotations

import inspect
import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from jsonschema.validators import Draft202012Validator

from grackle import protocol
from grackle.protocol import InvalidEnvelope, parse_envelope

if TYPE_CHECKING:
    from grackle.adapters.base import StaticGraph, TraceEvent


def _repo_root() -> Path:
    here = Path(__file__).resolve()
    for parent in (here, *here.parents):
        if (parent / "packages/shared-types/schema/messages.schema.json").exists():
            return parent
    raise RuntimeError("repo root with messages.schema.json not found")


@pytest.fixture(scope="module")
def messages_schema() -> dict[str, Any]:
    path = _repo_root() / "packages/shared-types/schema/messages.schema.json"
    return cast("dict[str, Any]", json.loads(path.read_text(encoding="utf-8")))


def _validate_against(schema: dict[str, Any], def_name: str, obj: Any) -> None:
    """Validate obj against messages.schema.json#/$defs/<def_name>."""
    ref_schema = {"$defs": schema["$defs"], "$ref": f"#/$defs/{def_name}"}
    Draft202012Validator(ref_schema).validate(obj)


def _is_uuid(s: str) -> bool:
    try:
        uuid.UUID(s)
    except ValueError:
        return False
    return True


def _sample_graph() -> StaticGraph:
    return {"version": 1, "language": "python", "nodes": [], "edges": []}


def _sample_event() -> TraceEvent:
    return {
        "event": "call",
        "node_id": "a.py:f",
        "ts_ns": 1,
        "thread_id": 1,
        "frame_depth": 0,
    }


# ---------------------------------------------------------------------------
# Every builder round-trips through parse_envelope and matches the envelope
# AND validates against its specific schema $def.
# ---------------------------------------------------------------------------


def _all_messages() -> list[tuple[str, str, str]]:
    """Return (builder-output-str, expected_type, schema_def_name) per builder."""
    graph = _sample_graph()
    event = _sample_event()
    return [
        (protocol.make_pong("ping-1"), "pong", "PongMessage"),
        (protocol.make_static_graph(graph), "static_graph", "StaticGraphMessage"),
        (
            protocol.make_source_response("r", "a/b.py", "x = 1\n", "utf-8"),
            "source_response",
            "ReadSourceResponse",
        ),
        (
            protocol.make_source_error("r", "a/b.py", "not_found"),
            "source_error",
            "ReadSourceError",
        ),
        (
            protocol.make_trace_session_start("sid", 100, "replay"),
            "trace_session_start",
            "TraceSessionStartMessage",
        ),
        (
            protocol.make_trace_session_start("sid", 100, "live", seekable=True),
            "trace_session_start",
            "TraceSessionStartMessage",
        ),
        (protocol.make_trace_event(event), "trace_event", "TraceEventMessage"),
        (
            protocol.make_trace_session_end("sid", 200, 5),
            "trace_session_end",
            "TraceSessionEndMessage",
        ),
        (
            protocol.make_trace_window("r", "sid", 0, [event], 1),
            "trace_window",
            "TraceWindowMessage",
        ),
        (
            protocol.make_trace_seek_error("r", "sid", "not seekable"),
            "trace_seek_error",
            "TraceSeekError",
        ),
        (
            protocol.make_trace_query_response("r", "sid", "coverage", 10, {"count": 3}),
            "trace_query_response",
            "TraceQueryResponse",
        ),
        (
            protocol.make_session_list_response("r", []),
            "session_list_response",
            "SessionListResponse",
        ),
    ]


def test_every_builder_round_trips_and_matches_schema(
    messages_schema: dict[str, Any],
) -> None:
    for raw, expected_type, def_name in _all_messages():
        env = parse_envelope(raw)  # must not raise
        assert env["type"] == expected_type
        assert set(env.keys()) == {"id", "type", "payload"}
        _validate_against(messages_schema, def_name, env)


# ---------------------------------------------------------------------------
# Per-builder: type + required payload fields + id semantics.
# ---------------------------------------------------------------------------


def test_make_pong_echoes_ping_id() -> None:
    env = json.loads(protocol.make_pong("p-42"))
    assert env["type"] == "pong"
    assert env["id"] == "p-42"
    assert env["payload"] == {"ping_id": "p-42"}


def test_make_static_graph_fresh_uuid_and_payload_is_graph() -> None:
    graph = _sample_graph()
    env = json.loads(protocol.make_static_graph(graph))
    assert env["type"] == "static_graph"
    assert _is_uuid(env["id"])
    assert env["payload"] == graph


def test_make_source_response_echoes_id_and_fields() -> None:
    env = json.loads(protocol.make_source_response("req", "svc/auth.py", "code", "utf-8"))
    assert env["type"] == "source_response"
    assert env["id"] == "req"
    assert env["payload"] == {
        "path": "svc/auth.py",
        "source": "code",
        "encoding": "utf-8",
    }


def test_make_source_error_echoes_id_and_fields() -> None:
    env = json.loads(protocol.make_source_error("req", "svc/auth.py", "forbidden"))
    assert env["type"] == "source_error"
    assert env["id"] == "req"
    assert env["payload"] == {"path": "svc/auth.py", "reason": "forbidden"}


def test_make_trace_session_start_default_source_replay_no_seekable() -> None:
    env = json.loads(protocol.make_trace_session_start("sid", 99))
    assert env["type"] == "trace_session_start"
    assert _is_uuid(env["id"])
    assert env["payload"] == {"session_id": "sid", "started_ns": 99, "source": "replay"}
    assert "seekable" not in env["payload"]  # omitted when False


def test_make_trace_session_start_seekable_true_included() -> None:
    env = json.loads(protocol.make_trace_session_start("sid", 99, "live", seekable=True))
    assert env["payload"]["source"] == "live"
    assert env["payload"]["seekable"] is True


def test_make_trace_event_wraps_dict_and_fresh_uuid() -> None:
    event: TraceEvent = {
        "event": "return",
        "node_id": "a.py:f",
        "ts_ns": 5,
        "thread_id": 1,
        "frame_depth": 0,
    }
    env = json.loads(protocol.make_trace_event(event))
    assert env["type"] == "trace_event"
    assert _is_uuid(env["id"])
    assert env["payload"] == event


def test_make_trace_session_end_fields() -> None:
    env = json.loads(protocol.make_trace_session_end("sid", 222, 7))
    assert env["type"] == "trace_session_end"
    assert _is_uuid(env["id"])
    assert env["payload"] == {"session_id": "sid", "ended_ns": 222, "event_count": 7}


def test_make_trace_window_echoes_id_and_lists_events() -> None:
    event: TraceEvent = {
        "event": "call",
        "node_id": "a.py:f",
        "ts_ns": 1,
        "thread_id": 1,
        "frame_depth": 0,
    }
    env = json.loads(protocol.make_trace_window("req", "sid", 3, [event, event], 10))
    assert env["type"] == "trace_window"
    assert env["id"] == "req"
    p = env["payload"]
    assert p["session_id"] == "sid"
    assert p["start_index"] == 3
    assert p["total"] == 10
    assert p["events"] == [event, event]


def test_make_trace_seek_error_echoes_id() -> None:
    env = json.loads(protocol.make_trace_seek_error("req", "sid", "session not found"))
    assert env["type"] == "trace_seek_error"
    assert env["id"] == "req"
    assert env["payload"] == {"session_id": "sid", "reason": "session not found"}


def test_make_trace_query_response_no_error_omits_field() -> None:
    env = json.loads(protocol.make_trace_query_response("req", "sid", "top_k", 12, {"entries": []}))
    assert env["type"] == "trace_query_response"
    assert env["id"] == "req"
    assert env["payload"] == {
        "session_id": "sid",
        "kind": "top_k",
        "at_index": 12,
        "data": {"entries": []},
    }
    assert "error" not in env["payload"]


def test_make_trace_query_response_with_error_includes_field() -> None:
    env = json.loads(
        protocol.make_trace_query_response("req", "sid", "coverage", 0, {}, error="boom")
    )
    assert env["payload"]["error"] == "boom"


def test_make_session_list_response_echoes_id_and_sessions() -> None:
    sessions: list[dict[str, Any]] = [
        {
            "id": "s1",
            "label": "run",
            "started_ns": 1,
            "ended_ns": 2,
            "source_path": "trace.jsonl",
            "event_count": 0,
            "language": "python",
        }
    ]
    env = json.loads(protocol.make_session_list_response("req", sessions))
    assert env["type"] == "session_list_response"
    assert env["id"] == "req"
    assert env["payload"] == {"sessions": sessions}


# ---------------------------------------------------------------------------
# parse_envelope — the only validating function in the module.
# ---------------------------------------------------------------------------


def test_parse_envelope_accepts_valid() -> None:
    env = parse_envelope(json.dumps({"id": "x", "type": "ping", "payload": {}}))
    assert env == {"id": "x", "type": "ping", "payload": {}}


def test_parse_envelope_rejects_non_json() -> None:
    with pytest.raises(InvalidEnvelope, match="invalid JSON"):
        parse_envelope("{not json")


def test_parse_envelope_rejects_missing_required_field() -> None:
    with pytest.raises(InvalidEnvelope):
        parse_envelope(json.dumps({"type": "ping", "payload": {}}))  # no id


def test_parse_envelope_rejects_extra_top_level_key() -> None:
    with pytest.raises(InvalidEnvelope):
        parse_envelope(json.dumps({"id": "x", "type": "ping", "payload": {}, "extra": 1}))


def test_invalid_envelope_is_value_error() -> None:
    assert issubclass(InvalidEnvelope, ValueError)


# ---------------------------------------------------------------------------
# Schema <-> builder SET-PARTITION parity (Phase 8.6, A1, Python side).
#
# The hand-written builders in grackle.protocol must only ever emit message
# `type` values that exist in the JSON schema, and every agent->browser type
# in the schema must have a builder. Browser->agent request types are parsed
# (not built) and are listed explicitly here as the allowed builder-less set.
# ---------------------------------------------------------------------------

# Browser->agent request messages: parsed via parse_envelope, never built.
# Keep in sync with messages.schema.json. A new request type here, or a new
# agent->browser type without a builder, will trip an assertion below.
REQUEST_ONLY_TYPES = frozenset(
    {
        "ping",
        "read_source",
        "trace_seek_request",
        "trace_query_request",
        "session_list_request",
        "session_load_request",
    }
)

# Minimal placeholder args per builder so we can call it and read the emitted
# "type". Values are throwaway — only the envelope "type" field is asserted.
_BUILDER_ARGS: dict[str, tuple[Any, ...]] = {
    "make_pong": ("id",),
    "make_static_graph": (_sample_graph(),),
    "make_source_response": ("id", "a.py", "src", "utf-8"),
    "make_source_error": ("id", "a.py", "not_found"),
    "make_trace_session_start": ("sid", 0),
    "make_trace_event": (_sample_event(),),
    "make_trace_session_end": ("sid", 0, 0),
    "make_trace_window": ("rid", "sid", 0, [], 0),
    "make_trace_seek_error": ("rid", "sid", "reason"),
    "make_trace_query_response": ("rid", "sid", "coverage", 0, {}),
    "make_session_list_response": ("rid", []),
}


def _schema_types(schema: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for defn in schema.get("$defs", {}).values():
        for branch in defn.get("allOf", []):
            const = branch.get("properties", {}).get("type", {}).get("const")
            if isinstance(const, str):
                out.add(const)
    return out


def _builders() -> dict[str, Any]:
    return {
        name: fn
        for name, fn in inspect.getmembers(protocol, inspect.isfunction)
        if name.startswith("make_")
    }


def _emitted_type(name: str, fn: Any) -> str:
    args = _BUILDER_ARGS[name]
    raw = fn(*args)
    return str(json.loads(raw)["type"])


def test_every_builder_is_in_args_table() -> None:
    """Catch a newly added make_* builder that nobody wired into the table."""
    assert set(_builders()) == set(_BUILDER_ARGS), (
        "protocol.make_* builders and _BUILDER_ARGS are out of sync; "
        "update _BUILDER_ARGS in this test"
    )


def test_builders_emit_schema_types(messages_schema: dict[str, Any]) -> None:
    schema_types = _schema_types(messages_schema)
    for name, fn in _builders().items():
        emitted = _emitted_type(name, fn)
        assert emitted in schema_types, (
            f"{name} emits type {emitted!r} which is not in messages.schema.json"
        )


def test_built_and_request_types_partition_schema(
    messages_schema: dict[str, Any],
) -> None:
    """Schema types == (types a builder emits) U (request-only types), disjoint."""
    schema_types = _schema_types(messages_schema)
    built = {_emitted_type(n, f) for n, f in _builders().items()}

    overlap = built & REQUEST_ONLY_TYPES
    assert not overlap, f"types both built and listed request-only: {sorted(overlap)}"

    covered = built | REQUEST_ONLY_TYPES
    missing = schema_types - covered
    extra = covered - schema_types
    assert not missing, (
        f"schema types with neither a builder nor a request-only entry: "
        f"{sorted(missing)} — add a make_* builder or list it in REQUEST_ONLY_TYPES"
    )
    assert not extra, f"types in builders/request-only not in schema: {sorted(extra)}"
