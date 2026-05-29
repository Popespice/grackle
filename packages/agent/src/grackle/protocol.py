"""
Wire-format helpers for the grackle WebSocket protocol.

TypedDicts are defined here (not imported from _generated/) for the same reason
src/messages.ts is hand-written: json-schema-to-typescript and datamodel-code-generator
emit permissive types that don't capture all schema constraints (e.g. maxProperties: 0
cannot be expressed as dict[str, Never]). The generated _generated/messages.py is a
sanity-check artifact reviewed after schema changes, not a runtime dependency.
"""

import json
import uuid
from typing import TYPE_CHECKING, Any, TypedDict, cast

import jsonschema
import jsonschema.exceptions

from grackle.adapters.base import StaticGraph

if TYPE_CHECKING:
    from grackle.adapters.base import TraceEvent


class WsEnvelope(TypedDict):
    id: str
    type: str
    payload: dict[str, Any]


_ENVELOPE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "type": {"type": "string"},
        "payload": {"type": "object"},
    },
    "required": ["id", "type", "payload"],
    "additionalProperties": False,
}


class InvalidEnvelope(ValueError):
    """Raised when a raw message does not conform to the WsEnvelope schema."""


def parse_envelope(raw: str) -> WsEnvelope:
    """Parse and validate a raw JSON message as a WsEnvelope."""
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InvalidEnvelope(f"invalid JSON: {exc}") from exc
    try:
        jsonschema.validate(data, _ENVELOPE_SCHEMA)
    except jsonschema.exceptions.ValidationError as exc:
        raise InvalidEnvelope(exc.message) from exc
    return cast("WsEnvelope", data)


def make_pong(ping_id: str) -> str:
    """Return a serialized pong envelope, echoing ping_id as both id and payload.ping_id."""
    return json.dumps({"id": ping_id, "type": "pong", "payload": {"ping_id": ping_id}})


def make_static_graph(graph: StaticGraph) -> str:
    """Return a serialized static_graph envelope with a fresh UUID id."""
    return json.dumps({"id": str(uuid.uuid4()), "type": "static_graph", "payload": graph})


def make_source_response(request_id: str, path: str, source: str, encoding: str) -> str:
    """Return a serialized source_response envelope echoing the request id."""
    return json.dumps(
        {
            "id": request_id,
            "type": "source_response",
            "payload": {"path": path, "source": source, "encoding": encoding},
        }
    )


def make_source_error(request_id: str, path: str, reason: str) -> str:
    """Return a serialized source_error envelope echoing the request id."""
    return json.dumps(
        {
            "id": request_id,
            "type": "source_error",
            "payload": {"path": path, "reason": reason},
        }
    )


def make_trace_session_start(
    session_id: str,
    started_ns: int,
    source: str = "replay",
    *,
    seekable: bool = False,
) -> str:
    """Return a serialized trace_session_start envelope with a fresh UUID id.

    Args:
        session_id:  UUID identifying this session.
        started_ns:  Monotonic start timestamp in nanoseconds.
        source:      ``"replay"`` or ``"live"`` (open string per ADR-0004).
        seekable:    When ``True``, includes ``seekable: true`` in the payload
                     so the browser knows it may send ``trace_seek_request``
                     messages for this session.
    """
    payload: dict[str, Any] = {
        "session_id": session_id,
        "started_ns": started_ns,
        "source": source,
    }
    if seekable:
        payload["seekable"] = True
    return json.dumps(
        {
            "id": str(uuid.uuid4()),
            "type": "trace_session_start",
            "payload": payload,
        }
    )


def make_trace_event(event: "TraceEvent") -> str:
    """Return a serialized trace_event envelope wrapping one TraceEvent dict."""
    return json.dumps(
        {
            "id": str(uuid.uuid4()),
            "type": "trace_event",
            "payload": dict(event),
        }
    )


def make_trace_session_end(session_id: str, ended_ns: int, event_count: int) -> str:
    """Return a serialized trace_session_end envelope with a fresh UUID id."""
    return json.dumps(
        {
            "id": str(uuid.uuid4()),
            "type": "trace_session_end",
            "payload": {
                "session_id": session_id,
                "ended_ns": ended_ns,
                "event_count": event_count,
            },
        }
    )


def make_trace_window(
    request_id: str,
    session_id: str,
    start_index: int,
    events: "list[TraceEvent]",
    total: int,
) -> str:
    """Return a serialized trace_window envelope echoing the request id.

    Args:
        request_id:   ``id`` from the ``trace_seek_request`` being answered.
        session_id:   UUID of the trace session.
        start_index:  Absolute index of the first event in *events*.
        events:       The requested window of ``TraceEvent`` dicts.
        total:        Total number of events in the trace file.
    """
    return json.dumps(
        {
            "id": request_id,
            "type": "trace_window",
            "payload": {
                "session_id": session_id,
                "start_index": start_index,
                "events": [dict(ev) for ev in events],
                "total": total,
            },
        }
    )


def make_trace_seek_error(request_id: str, session_id: str, reason: str) -> str:
    """Return a serialized trace_seek_error envelope echoing the request id.

    Args:
        request_id:  ``id`` from the ``trace_seek_request`` being answered.
        session_id:  UUID from the failed request.
        reason:      Human-readable reason (open string; e.g. "session not found").
    """
    return json.dumps(
        {
            "id": request_id,
            "type": "trace_seek_error",
            "payload": {
                "session_id": session_id,
                "reason": reason,
            },
        }
    )


def make_trace_query_response(
    request_id: str,
    session_id: str,
    kind: str,
    at_index: int,
    data: dict[str, Any],
    *,
    error: str | None = None,
) -> str:
    """Return a serialized trace_query_response envelope echoing the request id.

    Args:
        request_id:  ``id`` from the ``trace_query_request`` being answered.
        session_id:  UUID of the trace session.
        kind:        Aggregate kind (``"cumulative_heat"``, ``"coverage"``, or ``"top_k"``).
        at_index:    Upper-bound index used to compute the aggregate.
        data:        Result data; shape depends on *kind*.
        error:       When set, the query could not be fulfilled; this field carries the reason.
    """
    payload: dict[str, Any] = {
        "session_id": session_id,
        "kind": kind,
        "at_index": at_index,
        "data": data,
    }
    if error is not None:
        payload["error"] = error
    return json.dumps({"id": request_id, "type": "trace_query_response", "payload": payload})


def make_session_list_response(request_id: str, sessions: list[dict[str, Any]]) -> str:
    """Return a serialized session_list_response envelope echoing the request id.

    Args:
        request_id:  ``id`` from the ``session_list_request`` being answered.
        sessions:    List of session metadata dicts (each matching ``SessionMeta`` shape).
    """
    return json.dumps(
        {
            "id": request_id,
            "type": "session_list_response",
            "payload": {"sessions": sessions},
        }
    )
