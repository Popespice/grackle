from pathlib import Path

from grackle.adapters.base import Capabilities, ParseOptions, StaticGraph


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
