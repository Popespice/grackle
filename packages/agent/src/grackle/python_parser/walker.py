"""AST walker: enumerate Python files, check cache, parse on miss, aggregate."""

from __future__ import annotations

import ast
import fnmatch
import hashlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from grackle.adapters.base import ParseOptions, StaticGraph
    from grackle.cache import CacheManager

from grackle.paths import to_posix
from grackle.python_parser.hints import extract_hints
from grackle.python_parser.resolver import resolve_graph
from grackle.python_parser.visitors import FileVisitor, GraphBuilder


def _hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _is_excluded(path: Path, root: Path, patterns: tuple[str, ...]) -> bool:
    """Return True if path matches any exclude pattern (gitignore-style, best-effort).

    Checks both the POSIX-relative path and the bare filename so patterns
    like ``*.pyc`` and ``src/test_*.py`` both work.
    """
    posix = to_posix(path, root)
    name = path.name
    return any(fnmatch.fnmatch(posix, p) or fnmatch.fnmatch(name, p) for p in patterns)


class PythonAstWalker:
    """Walk a project root, parse ``.py`` files with stdlib ``ast``, and return
    a ``StaticGraph`` aggregated from per-file partials.

    Cache interaction: ``cache.get(path)`` reads the file internally to verify
    its SHA-256; on a miss we read the file a second time for parsing. The
    double-read creates a narrow race window (file changes between reads), which
    is an accepted limitation for 2.C — see docs/cross-platform.md risk #10.
    """

    def __init__(self, root: Path, options: ParseOptions, cache: CacheManager) -> None:
        self._root = root
        self._options = options
        self._cache = cache

    def walk(self) -> StaticGraph:
        nodes: list[Any] = []
        edges: list[Any] = []
        hints: list[Any] = []
        warnings: list[str] = []

        for py_file in sorted(self._root.rglob("*.py")):
            if _is_excluded(py_file, self._root, self._options.exclude_patterns):
                continue

            partial = self._cache.get(py_file)
            if partial is not None:
                nodes.extend(partial.get("nodes", []))
                edges.extend(partial.get("edges", []))
                hints.extend(partial.get("hints", []))
                continue

            # Cache miss — read the file, hash it, parse it.
            try:
                content = py_file.read_bytes()
            except OSError as exc:
                warnings.append(f"{to_posix(py_file, self._root)}: read error: {exc}")
                continue

            content_hash = _hash_bytes(content)

            try:
                # Decode with replacement so non-UTF-8 files don't abort the walk.
                # Files with an explicit encoding declaration (# -*- coding: latin-1 -*)
                # may parse incorrectly if the non-UTF-8 bytes survive replacement;
                # ast.parse will raise SyntaxError in that case and we warn + skip.
                source = content.decode("utf-8", errors="replace")
                tree = ast.parse(source, filename=str(py_file))
            except SyntaxError as exc:
                warnings.append(f"{to_posix(py_file, self._root)}: syntax error: {exc}")
                continue

            file_id = to_posix(py_file, self._root)
            builder = GraphBuilder()
            FileVisitor(file_id, builder).visit(tree)

            file_hints = extract_hints(source, file_id)
            partial_dict = builder.partial()
            partial_dict["hints"] = file_hints
            self._cache.set(py_file, content_hash, partial_dict)
            nodes.extend(partial_dict["nodes"])
            edges.extend(partial_dict["edges"])
            hints.extend(file_hints)

        metadata: dict[str, Any] = {}
        if warnings:
            metadata["parse_warnings"] = warnings
        if hints:
            metadata["cross_language_hints"] = hints

        graph: StaticGraph = {
            "version": 1,
            "language": "python",
            "nodes": nodes,
            "edges": edges,
        }
        if metadata:
            graph["metadata"] = metadata
        return resolve_graph(graph)
