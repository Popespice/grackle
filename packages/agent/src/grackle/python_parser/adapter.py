"""PythonStaticParser — StaticParserAdapter for Python via stdlib ast."""

from __future__ import annotations

from typing import TYPE_CHECKING

from grackle.adapters.base import Capabilities

if TYPE_CHECKING:
    from pathlib import Path

    from grackle.adapters.base import ParseOptions, StaticGraph

_PYTHON_INDICATORS = frozenset({"pyproject.toml", "setup.py", "setup.cfg", ".python-version"})


class PythonStaticParser:
    """Static parser adapter for Python projects using stdlib ast."""

    language = "python"

    def detect(self, project_root: Path) -> bool:
        for name in _PYTHON_INDICATORS:
            if (project_root / name).exists():
                return True
        return next(project_root.rglob("*.py"), None) is not None

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
        from grackle.python_parser.walker import PythonAstWalker

        cache = CacheManager(project_root)
        return PythonAstWalker(project_root, options, cache).walk()
