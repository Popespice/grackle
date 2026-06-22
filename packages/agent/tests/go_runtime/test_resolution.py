"""Tests for GoResolver — no Go toolchain required (synthetic StaticGraph)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from grackle.adapters.runtime_resolution import UNRESOLVED
from grackle.go_runtime.resolution import GoResolver

# ---------------------------------------------------------------------------
# Synthetic graph mirroring fixtures/tiny-go-app
# ---------------------------------------------------------------------------
#
#   models/user.go:
#     line 14 → func NewUser (function)
#     line 19 → func (u *User) Print (method, receiver User)
#     line 23 → func (u *User) Describe (method, receiver User)
#
#   utils/helpers.go:
#     line 4  → func Contains (function) — will be "cold" in e2e

_GRAPH = {
    "version": 1,
    "language": "go",
    "nodes": [
        # file nodes
        {"id": "models/user.go", "kind": "file", "name": "user.go", "path": "models/user.go"},
        {
            "id": "utils/helpers.go",
            "kind": "file",
            "name": "helpers.go",
            "path": "utils/helpers.go",
        },
        # function + method nodes
        {
            "id": "models/user.go:NewUser",
            "kind": "function",
            "name": "NewUser",
            "path": "models/user.go",
            "line": 14,
        },
        {
            "id": "models/user.go:User.Print",
            "kind": "method",
            "name": "Print",
            "path": "models/user.go",
            "line": 19,
            "metadata": {"receiver": "User"},
        },
        {
            "id": "models/user.go:User.Describe",
            "kind": "method",
            "name": "Describe",
            "path": "models/user.go",
            "line": 23,
            "metadata": {"receiver": "User"},
        },
        {
            "id": "utils/helpers.go:Contains",
            "kind": "function",
            "name": "Contains",
            "path": "utils/helpers.go",
            "line": 4,
        },
    ],
    "edges": [],
}

_MODULE = "example.com/tinyapp"


@pytest.fixture()
def resolver(tmp_path: Path) -> GoResolver:
    """GoResolver with a synthetic graph and a fake go.mod."""
    gomod = tmp_path / "go.mod"
    gomod.write_text(f"module {_MODULE}\n\ngo 1.21\n")
    (tmp_path / "models").mkdir()
    (tmp_path / "utils").mkdir()
    return GoResolver(tmp_path, _GRAPH)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _normalize
# ---------------------------------------------------------------------------


def test_normalize_strips_module_prefix(resolver: GoResolver) -> None:
    posix = resolver._normalize("example.com/tinyapp/models/user.go")
    assert posix == "models/user.go"


def test_normalize_external_returns_none(resolver: GoResolver) -> None:
    assert resolver._normalize("fmt") is None
    assert resolver._normalize("github.com/other/pkg/file.go") is None


def test_normalize_no_module_returns_none(tmp_path: Path) -> None:
    r = GoResolver(tmp_path, _GRAPH)  # type: ignore[arg-type]
    assert r._normalize("example.com/tinyapp/models/user.go") is None


# ---------------------------------------------------------------------------
# resolve_block — decl-line bisect (the critical trap-#2 test)
# ---------------------------------------------------------------------------


def test_resolve_block_exact_decl_line(resolver: GoResolver) -> None:
    # Block starting at exactly the decl line resolves via _sym_index.
    nid = resolver.resolve_block("example.com/tinyapp/models/user.go", 14)
    assert nid == "models/user.go:NewUser"


def test_resolve_block_inside_first_func(resolver: GoResolver) -> None:
    # Statement line 16 is inside NewUser (decl 14), before Print (decl 19).
    nid = resolver.resolve_block("example.com/tinyapp/models/user.go", 16)
    assert nid == "models/user.go:NewUser"


def test_resolve_block_exact_method_decl(resolver: GoResolver) -> None:
    nid = resolver.resolve_block("example.com/tinyapp/models/user.go", 19)
    assert nid == "models/user.go:User.Print"


def test_resolve_block_inside_method(resolver: GoResolver) -> None:
    # Line 21 is inside Print (decl 19), before Describe (decl 23).
    nid = resolver.resolve_block("example.com/tinyapp/models/user.go", 21)
    assert nid == "models/user.go:User.Print"


def test_resolve_block_inside_second_method(resolver: GoResolver) -> None:
    # Line 25 is inside Describe (decl 23).
    nid = resolver.resolve_block("example.com/tinyapp/models/user.go", 25)
    assert nid == "models/user.go:User.Describe"


def test_resolve_block_before_all_decls_falls_to_file(resolver: GoResolver) -> None:
    # Line 2 is before Contains (decl 4) — no enclosing function → file node.
    nid = resolver.resolve_block("example.com/tinyapp/utils/helpers.go", 2)
    assert nid == "utils/helpers.go"


# ---------------------------------------------------------------------------
# resolve_block — other fallbacks
# ---------------------------------------------------------------------------


def test_resolve_block_external_returns_none(resolver: GoResolver) -> None:
    assert resolver.resolve_block("fmt", 1) is None
    assert resolver.resolve_block("github.com/other/pkg/file.go", 5) is None


def test_resolve_block_unindexed_inproject(resolver: GoResolver, tmp_path: Path) -> None:
    # A file under the module that the static graph did not index → UNRESOLVED.
    (tmp_path / "internal").mkdir()
    nid = resolver.resolve_block("example.com/tinyapp/internal/secret.go", 10)
    assert nid == UNRESOLVED
