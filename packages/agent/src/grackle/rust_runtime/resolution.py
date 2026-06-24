"""Node-ID resolution for the Rust runtime adapter (ADR-0024).

Resolves ``llvm-cov export`` function entries — identified by an absolute
filesystem path + region start line — to static-graph node IDs.

Simpler than :mod:`grackle.go_runtime.resolution` because ``llvm-cov`` emits
real filesystem absolute paths (no module-prefix stripping needed). The
``to_posix`` call handles macOS ``/var``→``/private/var`` and Windows
short-path canonicalization via ``.resolve()`` on both sides.

Resolution contract:

- ``None``  → not a project file; caller filters it out.
- ``str``   → a project node ID. May be a function/method node (typical),
  a file node (fallback), or ``UNRESOLVED`` for an in-project file the
  static graph did not index.

Fallback chain (first match wins):

1. Exact ``(posix, line)`` match in ``_sym_index`` (rare: ``fn``-keyword is
   on the exact region start line).
2. Greatest decl-line ≤ region-start-line via ``_resolve_by_decl_line`` —
   the primary Rust path, since ``llvm-cov`` reports body region lines, not
   ``fn``-keyword declaration lines.
3. File node for ``posix`` (the file is in the project but no function matched).
4. ``UNRESOLVED`` (in-project file not indexed by the static parser).
"""

from __future__ import annotations

from grackle.adapters.runtime_resolution import NOT_PROJECT, UNRESOLVED, RuntimeResolver
from grackle.paths import to_posix

__all__ = ["UNRESOLVED", "RustResolver"]


class RustResolver(RuntimeResolver):
    """Pre-indexed lookup from an (absolute-path, line) pair to a node ID."""

    _build_name_index: bool = False
    _build_decl_index: bool = True  # llvm-cov reports body lines, not fn-keyword lines

    def resolve_function(self, abs_path: str, line: int) -> str | None:
        """Resolve an llvm-cov function entry to a node ID, or ``None`` to filter it.

        Args:
            abs_path: Absolute filesystem path from llvm-cov (``filenames[0]``).
            line:     Minimum region start line (1-based).

        Returns:
            ``None`` for external/stdlib frames; a node ID string otherwise.
        """
        posix = self._cached_normalize(abs_path)
        if posix == NOT_PROJECT:
            return None

        # 1. Exact line match (fn-keyword on the exact region start line).
        sym = self._sym_index.get((posix, line))
        if sym is not None:
            return sym

        # 2. Decl-line bisect — the normal Rust path.
        decl = self._resolve_by_decl_line(posix, line)
        if decl is not None:
            return decl

        # 3. File node fallback.
        fid = self._file_index.get(posix)
        if fid is not None:
            return fid

        # 4. In-project but not indexed.
        return UNRESOLVED

    def _normalize(self, identifier: str) -> str | None:
        """Normalise an absolute filesystem path to a POSIX-relative project path.

        ``llvm-cov`` emits real compile-time absolute paths, so no prefix-stripping
        is needed. ``to_posix`` resolves both sides (handles macOS /var→/private/var
        and Windows short paths) and raises ``ValueError`` if the path is not under
        the project root.
        """
        from pathlib import Path

        try:
            return to_posix(Path(identifier), self._root)
        except (ValueError, OSError):
            return None
