"""Abstract Tree-sitter walker base.

Subclasses implement ``file_extensions``, ``language_name``, and
``visit_tree``; this base handles file enumeration, exclusion, cache
management, and result aggregation — mirroring PythonAstWalker's structure
with the parse step delegated to the Tree-sitter runtime.
"""

from __future__ import annotations

import abc
import fnmatch
import hashlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from tree_sitter import Tree

    from grackle.adapters.base import ParseOptions, StaticGraph
    from grackle.cache import CacheManager
    from grackle.python_parser.visitors import GraphBuilder

from grackle.paths import to_posix
from grackle.tree_sitter_runtime import get_parser


def _hash_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _is_excluded(path: Path, root: Path, patterns: tuple[str, ...]) -> bool:
    posix = to_posix(path, root)
    name = path.name
    return any(fnmatch.fnmatch(posix, p) or fnmatch.fnmatch(name, p) for p in patterns)


class TreeSitterWalker(abc.ABC):
    """Walk a project root, parse source files with Tree-sitter, and return
    a StaticGraph aggregated from per-file partials.

    Subclasses must implement:
        ``file_extensions`` — tuple of file suffixes, e.g. ``('.ts', '.tsx')``
        ``language_name``   — tree-sitter language key, e.g. ``"typescript"``
        ``visit_tree``      — parse one file's syntax tree into a GraphBuilder
    """

    def __init__(
        self,
        root: Path,
        options: ParseOptions,
        cache: CacheManager,
    ) -> None:
        self._root = root
        self._options = options
        self._cache = cache

    @property
    @abc.abstractmethod
    def file_extensions(self) -> tuple[str, ...]:
        """File extensions to enumerate, including the leading dot."""

    @property
    @abc.abstractmethod
    def language_name(self) -> str:
        """Language key used by get_parser() and emitted on the graph."""

    @abc.abstractmethod
    def visit_tree(self, tree: Tree, source: str, file_id: str) -> GraphBuilder:
        """Parse one file's syntax tree into a GraphBuilder partial."""

    def _resolve(self, graph: StaticGraph) -> StaticGraph:
        """Post-walk resolver hook. Subclasses override to run their resolver."""
        return graph

    def hints_for_file(self, source: str, file_id: str) -> list[Any]:
        """Return cross-language hint dicts for one source file.

        Subclasses override this to call their ``hints.extract_hints``.
        The default returns an empty list (opt-in).
        """
        return []

    def walk(self) -> StaticGraph:
        nodes: list[Any] = []
        edges: list[Any] = []
        hints: list[Any] = []
        warnings: list[str] = []

        parser = get_parser(self.language_name)

        source_files: list[Path] = []
        for ext in self.file_extensions:
            source_files.extend(self._root.rglob(f"*{ext}"))
        # deduplicate (overlapping globs) and sort for deterministic ordering
        source_files = sorted(set(source_files))

        for src_file in source_files:
            if _is_excluded(src_file, self._root, self._options.exclude_patterns):
                continue

            partial = self._cache.get(src_file)
            if partial is not None:
                nodes.extend(partial.get("nodes", []))
                edges.extend(partial.get("edges", []))
                hints.extend(partial.get("hints", []))
                continue

            try:
                content = src_file.read_bytes()
            except OSError as exc:
                warnings.append(f"{to_posix(src_file, self._root)}: read error: {exc}")
                continue

            content_hash = _hash_bytes(content)

            try:
                tree = parser.parse(content)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"{to_posix(src_file, self._root)}: parse error: {exc}")
                continue

            source = content.decode("utf-8", errors="replace")
            file_id = to_posix(src_file, self._root)
            builder = self.visit_tree(tree, source, file_id)

            file_hints = self.hints_for_file(source, file_id)
            partial_dict = builder.partial()
            partial_dict["hints"] = file_hints
            self._cache.set(src_file, content_hash, partial_dict)
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
            "language": self.language_name,
            "nodes": nodes,
            "edges": edges,
        }
        if metadata:
            graph["metadata"] = metadata
        return self._resolve(graph)
