"""Shared base for runtime node-ID resolvers (Phase 8.6 dedup).

Both runtime resolvers map a raw source identifier + position to a static-graph
node ID:

* ``python_runtime.node_resolution.NodeResolver`` — a CPython ``CodeType``'s
  ``co_filename`` + ``co_firstlineno``;
* ``node_runtime.node_resolution.NodeResolver`` — a V8 callFrame's ``url`` +
  ``lineNumber`` + ``functionName``.
* ``go_runtime.resolution.GoResolver`` — a covdata import-path + statement line.

They share the whole index build, cached normalisation, the ``NOT_PROJECT``
sentinel, and the ``(path,line)`` / file / ``UNRESOLVED`` resolution machinery.
They differ in two ways:

1. How a raw identifier normalises to a POSIX-relative project path — the single
   abstract method :meth:`RuntimeResolver._normalize`.
2. Whether a ``(path, name)`` name index is built. Node sets ``_build_name_index
   = True`` because V8 reports a ``functionName`` usable as a fallback; CPython
   resolution keys on line only, so the index is skipped to avoid the per-session
   allocation cost.
"""

from __future__ import annotations

import bisect
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from grackle.adapters.base import StaticGraph

# Sentinel in ``_norm_cache`` meaning "not a project file" — distinguished from a
# missing key so repeated non-project identifiers still get a cache hit.
NOT_PROJECT = ""

# Returned for an in-project file the static graph did not index (kept visible
# rather than silently dropped).
UNRESOLVED = "<unresolved>"


class RuntimeResolver(ABC):
    """Pre-indexed lookup from a source identifier to a static-graph node ID.

    Args:
        root: Project root; subclasses normalise raw identifiers against it.
        graph: Static graph produced by the language's static parser.
    """

    # Subclasses that use name-based resolution (Node only) set this to True.
    _build_name_index: bool = False

    def __init__(self, root: Path, graph: StaticGraph) -> None:
        self._root = root.resolve()
        # (posix_path, line) -> node_id, or None when >1 distinct node shares the
        # start line (ambiguous → by-line resolution declines to guess).
        self._sym_index: dict[tuple[str, int], str | None] = {}
        # posix_path -> node_id  for file nodes (fallback).
        self._file_index: dict[str, str] = {}
        # (posix_path, name) -> node_id, or None when ambiguous.
        # Only populated when _build_name_index is True (Node resolver).
        self._name_index: dict[tuple[str, str], str | None] = {}
        # raw identifier (str) -> posix_path or NOT_PROJECT. Bounded by the number
        # of distinct identifiers touched during one trace session.
        self._norm_cache: dict[str, str] = {}
        # Per-file sorted declaration-line index.
        # posix -> [(decl_line, node_id)] sorted by decl_line.
        # Used by _resolve_by_decl_line for runtimes that report statement lines
        # rather than func-keyword lines (Go covdata).
        _decl_unsorted: dict[str, list[tuple[int, str]]] = {}

        for node in graph["nodes"]:
            node_id: str = node["id"]
            kind: str = node["kind"]
            path: str = node["path"]
            if kind == "file":
                self._file_index[path] = node_id
            elif kind in ("function", "method"):
                line = node.get("line")
                if line is not None:
                    self._index_unique(self._sym_index, (path, line), node_id)
                    _decl_unsorted.setdefault(path, []).append((line, node_id))
                if self._build_name_index:
                    name = node.get("name")
                    if name:
                        self._index_unique(self._name_index, (path, name), node_id)

        # Finalise: sort each per-file list and build parallel key array.
        self._decl_lines: dict[str, list[tuple[int, str]]] = {}
        self._decl_keys: dict[str, list[int]] = {}
        for posix, pairs in _decl_unsorted.items():
            pairs.sort()
            self._decl_lines[posix] = pairs
            self._decl_keys[posix] = [p[0] for p in pairs]

    @staticmethod
    def _index_unique[K](index: dict[K, str | None], key: K, node_id: str) -> None:
        """Insert *node_id* under *key*, marking ambiguous (None) on a 2nd distinct id.

        First writer wins; a later distinct id at the same key flips it to None so
        resolution declines to guess rather than silently dropping one via
        last-write-wins.
        """
        if key not in index:
            index[key] = node_id
        elif index[key] != node_id:
            index[key] = None

    # ------------------------------------------------------------------
    # Normalisation (the one subclass difference)
    # ------------------------------------------------------------------

    @abstractmethod
    def _normalize(self, identifier: str) -> str | None:
        """Normalise a raw source identifier to a POSIX-relative path, or None.

        Subclasses implement this: the Python resolver parses a ``co_filename``;
        the Node resolver parses a ``file://`` URL. ``None`` means "not a project
        file" (sentinel string, outside the root, unreadable scheme, etc.).
        """

    def _cached_normalize(self, identifier: str) -> str:
        cached = self._norm_cache.get(identifier)
        if cached is not None:
            return cached
        result = self._normalize(identifier)
        normalised = NOT_PROJECT if result is None else result
        self._norm_cache[identifier] = normalised
        return normalised

    # ------------------------------------------------------------------
    # Shared resolution helpers
    # ------------------------------------------------------------------

    def _is_project(self, identifier: str) -> bool:
        return self._cached_normalize(identifier) != NOT_PROJECT

    def _resolve_by_decl_line(self, posix: str, line: int) -> str | None:
        """Return the node whose declaration line is the greatest <= *line*, or None.

        Used by runtimes that report statement lines inside a function body rather
        than the func-keyword declaration line (Go covdata). Bisects the sorted
        per-file declaration index to find the enclosing function/method.
        """
        keys = self._decl_keys.get(posix)
        if not keys:
            return None
        idx = bisect.bisect_right(keys, line) - 1
        if idx < 0:
            return None
        return self._decl_lines[posix][idx][1]

    def _resolve_by_name(self, posix: str, name: str | None) -> str | None:
        """Resolve a (file, function-name) pair to a node ID, or None.

        Returns None for an empty name or an ambiguous/absent one. V8 may qualify
        methods as ``"Class.method"``; the tail is retried so the bare method name
        still matches.
        """
        if not name:
            return None
        candidate = self._name_index.get((posix, name))
        if candidate is not None:
            return candidate
        if "." in name:
            tail = name.rsplit(".", 1)[-1]
            return self._name_index.get((posix, tail))
        return None
