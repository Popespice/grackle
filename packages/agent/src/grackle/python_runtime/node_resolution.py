"""Node-ID resolution: map (co_filename, co_firstlineno) → static-graph node ID.

A ``NodeResolver`` is built once per tracer session from the static graph
emitted by the Python static parser. For each runtime event we receive a
``CodeType.co_filename`` (absolute path) and ``CodeType.co_firstlineno``
(the first line of the enclosing function/method definition — or the first
decorator's line, if any). The resolver normalises the filename to a
POSIX-relative path and does an O(1) lookup in a precomputed
``(posix_path, lineno)`` index.

The index build, cached normalisation, and ``(path,line)`` / file / ``UNRESOLVED``
resolution machinery live in :class:`grackle.adapters.runtime_resolution.RuntimeResolver`;
this subclass supplies only :meth:`_normalize` (a CPython ``co_filename``) and the
``resolve`` contract.

Fallback chain (first match wins):
1. Function / method node whose ``path == posix_path`` and ``line == lineno``.
2. File node whose ``path == posix_path`` (covers lambdas, class bodies, etc.).
3. Literal ``UNRESOLVED`` (should never happen for project files).
"""

from __future__ import annotations

from pathlib import Path

from grackle.adapters.runtime_resolution import NOT_PROJECT, UNRESOLVED, RuntimeResolver
from grackle.paths import to_posix


class NodeResolver(RuntimeResolver):
    """Pre-indexed lookup from (POSIX path, definition line) to node ID."""

    def resolve(
        self,
        co_filename: str,
        co_firstlineno: int,
        co_name: str | None = None,
    ) -> str:
        """Return the node ID that best matches *co_filename* + *co_firstlineno*.

        The *co_filename* may be an absolute path, a ``<string>`` sentinel, or
        anything else Python sets on code objects. Non-project paths (those
        outside the project root) return ``UNRESOLVED`` immediately so the caller
        can decide to skip or disable that code object.

        When *co_name* is ``"<module>"``, the lookup skips the function/method
        index and goes straight to the file index. Module-level code has
        ``co_firstlineno = 1``, which collides with any function defined on
        line 1 — without this special case those module frames would be
        misresolved to that function's node.
        """
        posix = self._cached_normalize(co_filename)
        if posix == NOT_PROJECT:
            return UNRESOLVED

        # Module-level frames never match a function node — go straight to
        # the file fallback to avoid the line-1 collision described above.
        if co_name == "<module>":
            file_id = self._file_index.get(posix)
            return file_id if file_id is not None else UNRESOLVED

        # Prefer the most-specific node (function/method at this exact line).
        sym_id = self._sym_index.get((posix, co_firstlineno))
        if sym_id is not None:
            return sym_id

        # Fall back to the file node.
        file_id = self._file_index.get(posix)
        if file_id is not None:
            return file_id

        return UNRESOLVED

    def is_project_file(self, co_filename: str) -> bool:
        """Return True if *co_filename* falls inside the project root."""
        return self._is_project(co_filename)

    def _normalize(self, identifier: str) -> str | None:
        """Normalise *co_filename* to a POSIX-relative path or return None.

        Returns None for:
        - Sentinel strings like ``<frozen importlib._bootstrap>`` or ``<stdin>``.
        - Absolute paths outside the project root.
        - Calls made during interpreter shutdown (``sys.meta_path is None``).
        """
        if not identifier or identifier.startswith("<"):
            return None
        try:
            abs_path = Path(identifier).resolve()
            # Ensure it is inside the project root before converting.
            abs_path.relative_to(self._root)
            return to_posix(abs_path, self._root)
        except (ValueError, OSError, ImportError):
            # ImportError can be raised by Path() during interpreter shutdown
            # (sys.meta_path is None). Treat as "not a project file."
            return None
