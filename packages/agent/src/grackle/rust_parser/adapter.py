"""RustStaticParser — StaticParserAdapter for Rust via Tree-sitter."""

from __future__ import annotations

from typing import TYPE_CHECKING

from grackle.adapters.base import Capabilities

if TYPE_CHECKING:
    from pathlib import Path

    from grackle.adapters.base import ParseOptions, StaticGraph

_RUST_INDICATORS = frozenset({"Cargo.toml"})
_RUST_EXTENSIONS = frozenset({".rs"})


class RustStaticParser:
    """Static parser adapter for Rust projects using Tree-sitter."""

    language = "rust"

    def detect(self, project_root: Path) -> bool:
        for name in _RUST_INDICATORS:
            if (project_root / name).exists():
                return True
        return any(f.suffix in _RUST_EXTENSIONS for f in project_root.rglob("*") if f.is_file())

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
        from grackle.rust_parser.walker import RustWalker

        cache = CacheManager(project_root)
        return RustWalker(project_root, options, cache).walk()
