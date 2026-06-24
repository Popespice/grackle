"""Tests for RustResolver — no Rust toolchain required (synthetic StaticGraph)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from grackle.adapters.runtime_resolution import UNRESOLVED
from grackle.rust_runtime.resolution import RustResolver

# ---------------------------------------------------------------------------
# Synthetic graph mirroring fixtures/tiny-rust-bin and tiny-rust-app layout.
# ---------------------------------------------------------------------------
#
#   src/main.rs:
#     line 4  → fn main (function)
#     line 10 → fn greet (function)
#
#   src/calc.rs:
#     line 1  → fn add (function)
#     line 5  → fn sub (function)

_GRAPH: dict[str, Any] = {
    "version": 1,
    "language": "rust",
    "nodes": [
        # file nodes
        {"id": "src/main.rs", "kind": "file", "name": "main.rs", "path": "src/main.rs"},
        {"id": "src/calc.rs", "kind": "file", "name": "calc.rs", "path": "src/calc.rs"},
        # function nodes
        {
            "id": "src/main.rs:main",
            "kind": "function",
            "name": "main",
            "path": "src/main.rs",
            "line": 4,
        },
        {
            "id": "src/main.rs:greet",
            "kind": "function",
            "name": "greet",
            "path": "src/main.rs",
            "line": 10,
        },
        {
            "id": "src/calc.rs:add",
            "kind": "function",
            "name": "add",
            "path": "src/calc.rs",
            "line": 1,
        },
        {
            "id": "src/calc.rs:sub",
            "kind": "function",
            "name": "sub",
            "path": "src/calc.rs",
            "line": 5,
        },
    ],
    "edges": [],
}


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    """Fake project root with the expected source files."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.rs").touch()
    (tmp_path / "src" / "calc.rs").touch()
    return tmp_path


@pytest.fixture()
def resolver(root: Path) -> RustResolver:
    return RustResolver(root, _GRAPH)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _normalize — absolute paths
# ---------------------------------------------------------------------------


def test_normalize_in_project(resolver: RustResolver, root: Path) -> None:
    abs_path = str(root / "src" / "main.rs")
    posix = resolver._normalize(abs_path)
    assert posix == "src/main.rs"


def test_normalize_outside_project(resolver: RustResolver, tmp_path: Path) -> None:
    # A file NOT under the project root → None.
    other = tmp_path.parent / "other_project" / "lib.rs"
    assert resolver._normalize(str(other)) is None


def test_normalize_stdlib_path(resolver: RustResolver) -> None:
    # Standard library paths on macOS/Linux are typically under /.rustup or /usr.
    assert resolver._normalize("/usr/lib/rust/std/src/lib.rs") is None


# ---------------------------------------------------------------------------
# resolve_function — decl-line bisect (the critical trap test)
# ---------------------------------------------------------------------------


def test_resolve_exact_decl_line(resolver: RustResolver, root: Path) -> None:
    # Region starts exactly at the fn-keyword line → resolves via _sym_index.
    nid = resolver.resolve_function(str(root / "src" / "calc.rs"), 1)
    assert nid == "src/calc.rs:add"


def test_resolve_inside_first_func(resolver: RustResolver, root: Path) -> None:
    # Region line 3 is inside add (decl 1), before sub (decl 5).
    nid = resolver.resolve_function(str(root / "src" / "calc.rs"), 3)
    assert nid == "src/calc.rs:add"


def test_resolve_exact_second_func(resolver: RustResolver, root: Path) -> None:
    nid = resolver.resolve_function(str(root / "src" / "calc.rs"), 5)
    assert nid == "src/calc.rs:sub"


def test_resolve_inside_second_func(resolver: RustResolver, root: Path) -> None:
    nid = resolver.resolve_function(str(root / "src" / "calc.rs"), 7)
    assert nid == "src/calc.rs:sub"


def test_resolve_before_all_decls_falls_to_file(resolver: RustResolver, root: Path) -> None:
    # Line before the first fn (e.g. a module attribute) → file node fallback.
    # In _GRAPH, calc.rs:add is at line 1, so there's nothing before it.
    # Use a file with no functions to test the file-fallback path.
    empty_graph: dict[str, Any] = {
        "version": 1,
        "language": "rust",
        "nodes": [
            {"id": "src/calc.rs", "kind": "file", "name": "calc.rs", "path": "src/calc.rs"},
        ],
        "edges": [],
    }
    r = RustResolver(root, empty_graph)  # type: ignore[arg-type]
    nid = r.resolve_function(str(root / "src" / "calc.rs"), 10)
    assert nid == "src/calc.rs"


def test_resolve_in_project_but_not_indexed(resolver: RustResolver, root: Path) -> None:
    # A file under root that the static graph did not index → UNRESOLVED.
    unindexed = root / "src" / "helpers.rs"
    unindexed.touch()
    nid = resolver.resolve_function(str(unindexed), 3)
    assert nid == UNRESOLVED


def test_resolve_outside_project_returns_none(resolver: RustResolver, tmp_path: Path) -> None:
    other = "/usr/lib/rustlib/std/src/lib.rs"
    assert resolver.resolve_function(other, 5) is None
