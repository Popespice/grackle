"""Node-ID resolution for the Go runtime adapter (ADR-0023).

Resolves ``go tool covdata textfmt`` coverage blocks — identified by an
import-path-prefixed file path + statement line — to static-graph node IDs.

Resolution contract:

- ``None``  → the block is not a project frame; the caller filters it out.
  Covers standard-library and external-module paths not under this module.
- ``str``   → a project node ID. May be a function/method node (typical),
  a file node (fallback), or ``UNRESOLVED`` for an in-project file the
  static graph did not index.

Fallback chain (first match wins):

1. Exact ``(posix, line)`` match in ``_sym_index`` (rare: func-keyword on the
   exact statement line, e.g. one-liner functions).
2. Greatest decl-line ≤ statement-line via ``_resolve_by_decl_line`` — the
   primary Go path, since covdata reports block-start statement lines, not
   func-keyword declaration lines.
3. File node for ``posix`` (the file is in the project but no function matched).
4. ``UNRESOLVED`` (in-project file not indexed by the static parser).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from grackle.adapters.runtime_resolution import NOT_PROJECT, UNRESOLVED, RuntimeResolver
from grackle.go_parser.resolver import _read_go_mod
from grackle.paths import to_posix

if TYPE_CHECKING:
    from pathlib import Path

    from grackle.adapters.base import StaticGraph

__all__ = ["UNRESOLVED", "GoResolver"]


class GoResolver(RuntimeResolver):
    """Pre-indexed lookup from a covdata (import_path, line) pair to a node ID."""

    _build_name_index: bool = False
    _build_decl_index: bool = True

    def __init__(self, root: Path, graph: StaticGraph) -> None:
        super().__init__(root, graph)
        self._module: str = _read_go_mod(self._root) or ""

    def resolve_block(self, import_path: str, line: int) -> str | None:
        """Resolve a covdata block to a node ID, or ``None`` to filter it.

        Args:
            import_path: The import-path-prefixed file path from covdata
                (e.g. ``"example.com/tinyapp/models/user.go"``).
            line:        1-based statement line (block start).

        Returns:
            ``None`` for external/std-lib frames; a node ID string otherwise.
        """
        posix = self._cached_normalize(import_path)
        if posix == NOT_PROJECT:
            return None

        # 1. Exact line match (one-liner functions, func-keyword on block line).
        sym = self._sym_index.get((posix, line))
        if sym is not None:
            return sym

        # 2. Decl-line bisect — the normal Go path.
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
        """Strip the module prefix from a covdata import path, then to_posix.

        Returns ``None`` for paths not under this module (std-lib, external
        dependencies, vendored code).
        """
        if not self._module:
            return None
        prefix = self._module + "/"
        if not identifier.startswith(prefix):
            return None
        rel = identifier[len(prefix) :]
        try:
            return to_posix(self._root / rel, self._root)
        except (ValueError, OSError):
            return None
