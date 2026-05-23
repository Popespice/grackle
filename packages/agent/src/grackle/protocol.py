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
) -> str:
    """Return a serialized trace_session_start envelope with a fresh UUID id."""
    return json.dumps(
        {
            "id": str(uuid.uuid4()),
            "type": "trace_session_start",
            "payload": {
                "session_id": session_id,
                "started_ns": started_ns,
                "source": source,
            },
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
