"""TypeScriptStaticParser — StaticParserAdapter for TypeScript via Tree-sitter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from grackle.adapters.base import Capabilities

if TYPE_CHECKING:
    from pathlib import Path

    from grackle.adapters.base import ParseOptions, StaticGraph

_TS_INDICATORS = frozenset({"tsconfig.json", "tsconfig.base.json", "package.json"})
_TS_EXTENSIONS = frozenset({".ts", ".tsx", ".mts", ".cts"})


class TypeScriptStaticParser:
    """Static parser adapter for TypeScript projects using Tree-sitter."""

    language = "typescript"

    def detect(self, project_root: Path) -> bool:
        for name in _TS_INDICATORS:
            if (project_root / name).exists():
                return True
        return any(f.suffix in _TS_EXTENSIONS for f in project_root.rglob("*") if f.is_file())

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
        from grackle.typescript_parser.walker import TSWalker

        cache = CacheManager(project_root)
        return TSWalker(project_root, options, cache).walk()
