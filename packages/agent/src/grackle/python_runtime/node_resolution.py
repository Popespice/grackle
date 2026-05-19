"""Node-ID resolution: map (co_filename, co_firstlineno) → static-graph node ID.

A ``NodeResolver`` is built once per tracer session from the static graph
emitted by the Python static parser. For each runtime event we receive a
``CodeType.co_filename`` (absolute path) and ``CodeType.co_firstlineno``
(the first line of the enclosing function/method definition). The resolver
normalises the filename to a POSIX-relative path and does an O(1) lookup in
a precomputed ``(posix_path, lineno)`` index.

Fallback chain (first match wins):
1. Function / method node whose ``path == posix_path`` and ``line == lineno``.
2. File node whose ``path == posix_path`` (covers lambdas, class bodies, etc.).
3. Literal string ``"<unresolved>"`` (should never happen for project files).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from grackle.paths import to_posix

if TYPE_CHECKING:
    from grackle.adapters.base import StaticGraph


class NodeResolver:
    """Pre-indexed lookup from (POSIX path, definition line) to node ID.

    Args:
        root: Project root used to normalise ``co_filename`` values.
        graph: Static graph produced by the Python adapter for the project.
    """

    def __init__(self, root: Path, graph: StaticGraph) -> None:
        self._root = root.resolve()
        # Index 1: (posix_path, lineno) → node_id  for function/method nodes
        self._sym_index: dict[tuple[str, int], str] = {}
        # Index 2: posix_path → node_id  for file nodes (fallback)
        self._file_index: dict[str, str] = {}

        for node in graph["nodes"]:
            node_id: str = node["id"]
            kind: str = node["kind"]
            posix_path: str = node["path"]
            if kind == "file":
                self._file_index[posix_path] = node_id
            elif kind in ("function", "method"):
                line: int | None = node.get("line")
                if line is not None:
                    self._sym_index[(posix_path, line)] = node_id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, co_filename: str, co_firstlineno: int) -> str:
        """Return the node ID that best matches *co_filename* + *co_firstlineno*.

        The *co_filename* may be an absolute path, a ``<string>`` sentinel, or
        anything else Python sets on code objects. Non-project paths (those
        outside the project root) return ``"<unresolved>"`` immediately so the
        caller can decide to skip or disable that code object.
        """
        posix = self._normalize_filename(co_filename)
        if posix is None:
            return "<unresolved>"

        # Prefer the most-specific node (function/method at this exact line).
        sym_id = self._sym_index.get((posix, co_firstlineno))
        if sym_id is not None:
            return sym_id

        # Fall back to the file node.
        file_id = self._file_index.get(posix)
        if file_id is not None:
            return file_id

        return "<unresolved>"

    def is_project_file(self, co_filename: str) -> bool:
        """Return True if *co_filename* falls inside the project root."""
        return self._normalize_filename(co_filename) is not None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _normalize_filename(self, co_filename: str) -> str | None:
        """Normalise *co_filename* to a POSIX-relative path or return None.

        Returns None for:
        - Sentinel strings like ``<frozen importlib._bootstrap>`` or ``<stdin>``.
        - Absolute paths outside the project root.
        - Calls made during interpreter shutdown (``sys.meta_path is None``).
        """
        if not co_filename or co_filename.startswith("<"):
            return None
        try:
            abs_path = Path(co_filename).resolve()
            # Ensure it is inside the project root.
            abs_path.relative_to(self._root)
            return to_posix(abs_path, self._root)
        except (ValueError, OSError, ImportError):
            # ImportError can be raised by Path() during interpreter shutdown
            # (sys.meta_path is None). Treat as "not a project file."
            return None
