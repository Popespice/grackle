from dataclasses import dataclass
from pathlib import Path
from typing import Any, NotRequired, Protocol, TypedDict, runtime_checkable


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


# TraceEvent stays permissive until Phase 6 fleshes out the wire format.
type TraceEvent = dict[str, Any]


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
