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

    Both arguments are ``.resolve()``-d before being compared, so symlinks
    and ``..`` segments are normalized away. The result is always
    forward-slash separated regardless of host OS.

    **Case is NOT reliably normalized on every platform.** ``Path.resolve()``
    canonicalizes to the on-disk name on Windows (via
    ``_getfinalpathname``), but on macOS/Linux it merely preserves whatever
    case the caller supplied — even though the underlying filesystem may be
    case-insensitive (default macOS APFS) — since POSIX ``realpath`` has no
    concept of "the canonical on-disk case" to query. Two callers referring
    to the same physical file with different case therefore produce two
    *different* posix keys on macOS, silently, even though both resolve
    successfully and both pass the case-insensitive ``.exists()`` check
    (empirically verified — an earlier module docstring claiming uniform
    case normalization here was wrong). Callers that dedupe by posix key
    (e.g. ``grackle.watcher``) must not assume this function collapses
    case-variant references to one key on every OS.

    Do not set ``walk_up=True`` on the underlying ``relative_to`` call —
    that would silently emit ``..`` segments into node IDs.

    Raises:
        ValueError: if ``p`` is not under ``root`` after resolution.
        RuntimeError: if resolving ``p`` or ``root`` hits a symlink loop.
    """
    return p.resolve().relative_to(root.resolve()).as_posix()
