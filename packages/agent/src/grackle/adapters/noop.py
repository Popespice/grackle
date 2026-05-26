from __future__ import annotations

from typing import TYPE_CHECKING

from grackle.adapters.base import Capabilities, ParseOptions, StaticGraph

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from grackle.adapters.base import TraceEvent, TraceOptions


class NoOpStaticParser:
    language: str = "noop"

    def detect(self, project_root: Path) -> bool:
        return False

    def capabilities(self) -> Capabilities:
        return Capabilities()

    def parse(self, project_root: Path, options: ParseOptions) -> StaticGraph:
        return {"version": 1, "language": "noop", "nodes": [], "edges": []}


class NoOpRuntimeAdapter:
    language: str = "noop"

    def capabilities(self) -> Capabilities:
        return Capabilities()

    def trace(self, script: Path, root: Path, options: TraceOptions) -> Iterator[TraceEvent]:
        yield from ()

    def trace_streaming(
        self,
        script: Path,
        root: Path,
        options: TraceOptions,
        sink: Callable[[TraceEvent], None],
    ) -> None:
        pass
