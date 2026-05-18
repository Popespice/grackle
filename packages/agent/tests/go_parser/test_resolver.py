"""Tests for the Go symbol resolver."""

from __future__ import annotations

from typing import TYPE_CHECKING

from grackle.go_parser.resolver import (
    FileScope,
    ProjectScope,
    SymbolResolver,
    _detect_implements,
    _dir,
    _import_to_dir,
    _read_go_mod,
    build_file_scope,
    build_project_scope,
)

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_dir_root_level() -> None:
    assert _dir("main.go") == ""


def test_dir_one_level() -> None:
    assert _dir("models/user.go") == "models"


def test_dir_two_levels() -> None:
    assert _dir("pkg/sub/file.go") == "pkg/sub"


def test_import_to_dir_local() -> None:
    result = _import_to_dir("example.com/app/models", "example.com/app")
    assert result == "models"


def test_import_to_dir_external() -> None:
    result = _import_to_dir("fmt", "example.com/app")
    assert result is None


def test_import_to_dir_no_module() -> None:
    result = _import_to_dir("fmt", "")
    assert result is None


def test_read_go_mod(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/app\n\ngo 1.21\n")
    assert _read_go_mod(tmp_path) == "example.com/app"


def test_read_go_mod_missing(tmp_path: Path) -> None:
    assert _read_go_mod(tmp_path) is None


# ---------------------------------------------------------------------------
# ProjectScope
# ---------------------------------------------------------------------------


def test_build_project_scope_functions() -> None:
    nodes = [
        {
            "id": "models/user.go:NewUser",
            "kind": "function",
            "name": "NewUser",
            "path": "models/user.go",
        },
        {"id": "models/user.go", "kind": "file", "name": "user.go", "path": "models/user.go"},
    ]
    scope = build_project_scope(nodes)  # type: ignore[arg-type]
    assert scope.exports[("models", "NewUser")] == "models/user.go:NewUser"


def test_build_project_scope_struct() -> None:
    nodes = [
        {"id": "models/user.go:User", "kind": "struct", "name": "User", "path": "models/user.go"},
    ]
    scope = build_project_scope(nodes)  # type: ignore[arg-type]
    assert scope.exports[("models", "User")] == "models/user.go:User"


# ---------------------------------------------------------------------------
# FileScope
# ---------------------------------------------------------------------------


def test_build_file_scope_same_package() -> None:
    nodes = [
        {
            "id": "models/base.go:BaseEntity",
            "kind": "struct",
            "name": "BaseEntity",
            "path": "models/base.go",
        },
        {"id": "models/user.go:User", "kind": "struct", "name": "User", "path": "models/user.go"},
    ]
    scope = build_file_scope("models/user.go", nodes, [], "example.com/app")  # type: ignore[arg-type]
    assert "BaseEntity" in scope.local_defs
    assert "User" in scope.local_defs


def test_build_file_scope_import_map() -> None:
    nodes: list[object] = []
    import_edges = [
        {
            "source": "main.go",
            "target": "example.com/app/models",
            "kind": "import",
            "metadata": {},
        }
    ]
    scope = build_file_scope("main.go", nodes, import_edges, "example.com/app")  # type: ignore[arg-type]
    assert scope.import_map["models"] == "models"


def test_build_file_scope_import_alias() -> None:
    nodes: list[object] = []
    import_edges = [
        {
            "source": "main.go",
            "target": "example.com/app/models",
            "kind": "import",
            "metadata": {"alias": "m"},
        }
    ]
    scope = build_file_scope("main.go", nodes, import_edges, "example.com/app")  # type: ignore[arg-type]
    assert scope.import_map["m"] == "models"


def test_build_file_scope_external_import_excluded() -> None:
    nodes: list[object] = []
    import_edges = [{"source": "main.go", "target": "fmt", "kind": "import", "metadata": {}}]
    scope = build_file_scope("main.go", nodes, import_edges, "example.com/app")  # type: ignore[arg-type]
    assert "fmt" not in scope.import_map


# ---------------------------------------------------------------------------
# SymbolResolver
# ---------------------------------------------------------------------------


def test_resolve_local_name() -> None:
    fs = FileScope(
        file_id="models/user.go",
        local_defs={"User": "models/user.go:User"},
    )
    ps = ProjectScope()
    r = SymbolResolver(fs, ps)
    assert r.resolve("User") == "models/user.go:User"


def test_resolve_cross_package() -> None:
    fs = FileScope(
        file_id="main.go",
        import_map={"models": "models"},
    )
    ps = ProjectScope(exports={("models", "NewUser"): "models/user.go:NewUser"})
    r = SymbolResolver(fs, ps)
    assert r.resolve("models.NewUser") == "models/user.go:NewUser"


def test_resolve_unknown_package() -> None:
    fs = FileScope(file_id="main.go")
    ps = ProjectScope()
    r = SymbolResolver(fs, ps)
    assert r.resolve("external.Func") is None


def test_resolve_unknown_local() -> None:
    fs = FileScope(file_id="main.go")
    ps = ProjectScope()
    r = SymbolResolver(fs, ps)
    assert r.resolve("Unknown") is None


# ---------------------------------------------------------------------------
# Implements detection
# ---------------------------------------------------------------------------


def test_detect_implements_basic() -> None:
    nodes = [
        {
            "id": "models/base.go:Printable",
            "kind": "interface",
            "name": "Printable",
            "path": "models/base.go",
            "metadata": {"methods": ["Print"]},
        },
        {
            "id": "models/user.go:User",
            "kind": "struct",
            "name": "User",
            "path": "models/user.go",
        },
        {
            "id": "models/user.go:User.Print",
            "kind": "method",
            "name": "Print",
            "path": "models/user.go",
            "metadata": {"receiver": "User"},
        },
    ]
    edges = _detect_implements(nodes)  # type: ignore[arg-type]
    assert len(edges) == 1
    assert edges[0]["source"] == "models/user.go:User"
    assert edges[0]["target"] == "models/base.go:Printable"
    assert edges[0]["kind"] == "implements"


def test_detect_implements_partial_match_none() -> None:
    nodes = [
        {
            "id": "pkg/iface.go:Full",
            "kind": "interface",
            "name": "Full",
            "path": "pkg/iface.go",
            "metadata": {"methods": ["A", "B"]},
        },
        {
            "id": "pkg/impl.go:Partial",
            "kind": "struct",
            "name": "Partial",
            "path": "pkg/impl.go",
        },
        {
            "id": "pkg/impl.go:Partial.A",
            "kind": "method",
            "name": "A",
            "path": "pkg/impl.go",
            "metadata": {"receiver": "Partial"},
        },
    ]
    edges = _detect_implements(nodes)  # type: ignore[arg-type]
    assert edges == []


def test_detect_implements_empty_interface_skipped() -> None:
    nodes = [
        {
            "id": "pkg/iface.go:Empty",
            "kind": "interface",
            "name": "Empty",
            "path": "pkg/iface.go",
            "metadata": {"methods": []},
        },
        {
            "id": "pkg/impl.go:MyStruct",
            "kind": "struct",
            "name": "MyStruct",
            "path": "pkg/impl.go",
        },
    ]
    edges = _detect_implements(nodes)  # type: ignore[arg-type]
    assert edges == []
