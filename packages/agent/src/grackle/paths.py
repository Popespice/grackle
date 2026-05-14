"""POSIX-relative path canonicalization.

This module is the single sanctioned location for ``Path.relative_to`` calls
in the agent codebase. ADR-0003 (adapter design) and docs/cross-platform.md
require that any path emitted into a graph node ID, annotation key, cache
entry, or wire-format payload be POSIX-relative to the project root, so the
same project on macOS, Windows, and Linux yields identical IDs.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def to_posix(p: Path, root: Path) -> str:
    """Canonicalize ``p`` to a POSIX-relative path anchored at ``root``.

    Both arguments are ``.resolve()``-d before being compared, so symlinks,
    ``..`` segments, and case differences (on case-insensitive filesystems)
    are normalized away. The result is always forward-slash separated
    regardless of host OS.

    Do not set ``walk_up=True`` on the underlying ``relative_to`` call —
    that would silently emit ``..`` segments into node IDs.

    Raises:
        ValueError: if ``p`` is not under ``root`` after resolution.
    """
    return p.resolve().relative_to(root.resolve()).as_posix()
