from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NotRequired, Protocol, TypedDict, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class Capabilities:
    files: bool = False
    classes: bool = False
    functions: bool = False
    imports: bool = False
    calls: bool = False
    runtime_tracing: bool = False
    annotations: bool = False


@dataclass(frozen=True, slots=True)
class ParseOptions:
    exclude_patterns: tuple[str, ...] = ()
    include_external: bool = False
    follow_imports: bool = True


@dataclass(frozen=True, slots=True)
class TraceOptions:
    """Options controlling the runtime tracer.

    Attributes:
        include_line_events: Emit an event for every executed line in addition
            to call/return/exception events. Significantly increases event
            volume; disabled by default.
        max_events: Hard cap on events *emitted* (``None`` = unlimited). When
            the cap is reached the tracer stops and raises ``TraceCapExceeded``.
            Under real-time streaming (``--stream``), the cap counts events
            passed to the sink, not events successfully delivered over the
            network: backpressure-dropped events still count toward the cap.
    """

    include_line_events: bool = False
    max_events: int | None = None


# Hand-written TypedDicts — kept locally rather than imported from
# grackle._generated/ for the same reason protocol.py does (the generated
# tree is gitignored and tests/library code must not depend on it).
# Parity with packages/shared-types/schema/graph.schema.json is enforced by
# review during schema changes; _generated/graph.py is the reference shape.
# See ADR-0003 and ADR-0004.
class GraphNode(TypedDict):
    id: str
    kind: str
    name: str
    path: str
    line: NotRequired[int]
    metadata: NotRequired[dict[str, Any]]


class GraphEdge(TypedDict):
    source: str
    target: str
    kind: str
    metadata: NotRequired[dict[str, Any]]


class StaticGraph(TypedDict):
    version: int
    language: str
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    metadata: NotRequired[dict[str, Any]]


class TraceEvent(TypedDict):
    """A single runtime trace event.

    Parity with packages/shared-types/schema/trace.schema.json. The core
    fields are all required; ``metadata`` is optional per the schema and so
    is the only ``NotRequired`` field. (The earlier ``total=False`` form
    made every key optional, which masked missing-field bugs.)
    """

    event: str  # "call" | "return" | "line" | "exception"
    node_id: str  # POSIX-relative static-graph node id
    ts_ns: int  # monotonic nanoseconds (time.monotonic_ns())
    thread_id: int  # threading.get_ident()
    frame_depth: int  # 0 = outermost frame
    metadata: NotRequired[dict[str, Any]]  # event-specific payload


class TraceCapExceeded(RuntimeError):
    """Raised by the tracer when TraceOptions.max_events is exceeded."""


@runtime_checkable
class StaticParserAdapter(Protocol):
    language: str

    def detect(self, project_root: Path) -> bool: ...
    def capabilities(self) -> Capabilities: ...
    def parse(self, project_root: Path, options: ParseOptions) -> StaticGraph: ...


@runtime_checkable
class RuntimeAdapter(Protocol):
    language: str

    def capabilities(self) -> Capabilities: ...
    def trace(self, script: Path, root: Path, options: TraceOptions) -> Iterator[TraceEvent]: ...
    def trace_streaming(
        self,
        script: Path,
        root: Path,
        options: TraceOptions,
        sink: Callable[[TraceEvent], None],
    ) -> None: ...
