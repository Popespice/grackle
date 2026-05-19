"""Node-ID resolution: map (co_filename, co_firstlineno) → static-graph node ID.

A ``NodeResolver`` is built once per tracer session from the static graph
emitted by the Python static parser. For each runtime event we receive a
``CodeType.co_filename`` (absolute path) and ``CodeType.co_firstlineno``
(the first line of the enclosing function/method definition — or the first
decorator's line, if any). The resolver normalises the filename to a
POSIX-relative path and does an O(1) lookup in a precomputed
``(posix_path, lineno)`` index.

Fallback chain (first match wins):
1. Function / method node whose ``path == posix_path`` and ``line == lineno``.
2. File node whose ``path == posix_path`` (covers lambdas, class bodies, etc.).
3. Literal string ``"<unresolved>"`` (should never happen for project files).

Performance note: each callback used to call ``is_project_file`` and then
``resolve``, which normalised the filename twice. The resolver now caches
``_normalize_filename`` results in a per-instance dict so the second call is
a dict lookup. The cache is bounded by the number of distinct code-object
filenames in the project (a small constant), so growth is not a concern.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from grackle.paths import to_posix

if TYPE_CHECKING:
    from grackle.adapters.base import StaticGraph


# Sentinel used in ``_norm_cache`` to mean "this filename is not a project
# file" — distinguished from a missing key so we still get a cache hit.
_NOT_PROJECT = ""


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
        # Per-instance cache: co_filename (str) → posix_path or _NOT_PROJECT.
        # Bounded by the number of unique code-object filenames touched
        # during a single tracer session.
        self._norm_cache: dict[str, str] = {}

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

    def resolve(
        self,
        co_filename: str,
        co_firstlineno: int,
        co_name: str | None = None,
    ) -> str:
        """Return the node ID that best matches *co_filename* + *co_firstlineno*.

        The *co_filename* may be an absolute path, a ``<string>`` sentinel, or
        anything else Python sets on code objects. Non-project paths (those
        outside the project root) return ``"<unresolved>"`` immediately so the
        caller can decide to skip or disable that code object.

        When *co_name* is ``"<module>"``, the lookup skips the function/method
        index and goes straight to the file index. Module-level code has
        ``co_firstlineno = 1``, which collides with any function defined on
        line 1 — without this special case those module frames would be
        misresolved to that function's node.
        """
        posix = self._cached_normalize(co_filename)
        if posix == _NOT_PROJECT:
            return "<unresolved>"

        # Module-level frames never match a function node — go straight to
        # the file fallback to avoid the line-1 collision described above.
        if co_name == "<module>":
            file_id = self._file_index.get(posix)
            return file_id if file_id is not None else "<unresolved>"

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
        return self._cached_normalize(co_filename) != _NOT_PROJECT

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _cached_normalize(self, co_filename: str) -> str:
        """Return cached POSIX-relative path or ``_NOT_PROJECT`` sentinel.

        Avoids re-running ``Path.resolve()`` and ``relative_to()`` on every
        callback for the same code object.
        """
        cached = self._norm_cache.get(co_filename)
        if cached is not None:
            return cached
        result = self._normalize_filename(co_filename)
        normalised = _NOT_PROJECT if result is None else result
        self._norm_cache[co_filename] = normalised
        return normalised

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
