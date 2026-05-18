"""GoStaticParser — StaticParserAdapter for Go via Tree-sitter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from grackle.adapters.base import Capabilities

if TYPE_CHECKING:
    from pathlib import Path

    from grackle.adapters.base import ParseOptions, StaticGraph

_GO_INDICATORS = frozenset({"go.mod", "go.sum"})
_GO_EXTENSIONS = frozenset({".go"})


class GoStaticParser:
    """Static parser adapter for Go projects using Tree-sitter."""

    language = "go"

    def detect(self, project_root: Path) -> bool:
        for name in _GO_INDICATORS:
            if (project_root / name).exists():
                return True
        return any(f.suffix in _GO_EXTENSIONS for f in project_root.rglob("*") if f.is_file())

    def capabilities(self) -> Capabilities:
        return Capabilities(
            files=True,
            classes=True,
            functions=True,
            imports=True,
            calls=True,
        )

    def parse(self, project_root: Path, options: ParseOptions) -> StaticGraph:
        from grackle.cache import CacheManager
        from grackle.go_parser.walker import GoWalker

        cache = CacheManager(project_root)
        return GoWalker(project_root, options, cache).walk()
