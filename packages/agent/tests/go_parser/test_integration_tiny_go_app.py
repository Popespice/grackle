"""Integration test: parse fixtures/tiny-go-app and verify golden counts."""

from __future__ import annotations

from pathlib import Path

import pytest

from grackle.adapters.base import ParseOptions, StaticGraph
from grackle.cache import CacheManager
from grackle.go_parser.adapter import GoStaticParser
from grackle.go_parser.walker import GoWalker

FIXTURE = Path(__file__).parents[4] / "fixtures" / "tiny-go-app"


@pytest.fixture(scope="module")
def graph() -> StaticGraph:
    cache = CacheManager(FIXTURE)
    opts = ParseOptions()
    return GoWalker(FIXTURE, opts, cache).walk()


# ---------------------------------------------------------------------------
# Node counts
# ---------------------------------------------------------------------------


def test_at_least_15_nodes(graph: StaticGraph) -> None:
    assert len(graph["nodes"]) >= 15


def test_file_nodes_present(graph: StaticGraph) -> None:
    file_ids = {n["id"] for n in graph["nodes"] if n["kind"] == "file"}
    assert "main.go" in file_ids
    assert "models/base.go" in file_ids
    assert "models/user.go" in file_ids
    assert "services/service.go" in file_ids
    assert "utils/helpers.go" in file_ids


def test_struct_nodes_present(graph: StaticGraph) -> None:
    struct_ids = {n["id"] for n in graph["nodes"] if n["kind"] == "struct"}
    assert "models/base.go:BaseEntity" in struct_ids
    assert "models/user.go:User" in struct_ids
    assert "services/service.go:UserService" in struct_ids


def test_interface_node_present(graph: StaticGraph) -> None:
    iface_ids = {n["id"] for n in graph["nodes"] if n["kind"] == "interface"}
    assert "models/base.go:Printable" in iface_ids


def test_type_alias_nodes_present(graph: StaticGraph) -> None:
    alias_ids = {n["id"] for n in graph["nodes"] if n["kind"] == "type_alias"}
    assert len(alias_ids) >= 1


def test_function_nodes_present(graph: StaticGraph) -> None:
    func_ids = {n["id"] for n in graph["nodes"] if n["kind"] == "function"}
    assert "main.go:main" in func_ids
    assert "models/user.go:NewUser" in func_ids
    assert "services/service.go:NewUserService" in func_ids


def test_method_nodes_present(graph: StaticGraph) -> None:
    method_ids = {n["id"] for n in graph["nodes"] if n["kind"] == "method"}
    assert "models/user.go:User.Print" in method_ids
    assert "models/base.go:BaseEntity.GetID" in method_ids


def test_at_least_one_kind_each(graph: StaticGraph) -> None:
    kinds = {n["kind"] for n in graph["nodes"]}
    for expected in ("file", "struct", "interface", "function", "method", "type_alias"):
        assert expected in kinds, f"missing kind: {expected!r}"


# ---------------------------------------------------------------------------
# Edge presence
# ---------------------------------------------------------------------------


def test_import_edges_present(graph: StaticGraph) -> None:
    imports = [e for e in graph["edges"] if e["kind"] == "import"]
    assert len(imports) >= 3


def test_inherit_edge_user_embeds_base(graph: StaticGraph) -> None:
    inherits = [e for e in graph["edges"] if e["kind"] == "inherit"]
    node_ids = {n["id"] for n in graph["nodes"]}
    resolved = [
        e for e in inherits if e["source"] == "models/user.go:User" and e["target"] in node_ids
    ]
    assert len(resolved) >= 1, "User should embed BaseEntity (resolved)"


def test_implements_edge_user_implements_printable(graph: StaticGraph) -> None:
    impls = [e for e in graph["edges"] if e["kind"] == "implements"]
    assert len(impls) >= 1
    sources = {e["source"] for e in impls}
    assert "models/user.go:User" in sources
    targets = {e["target"] for e in impls}
    assert "models/base.go:Printable" in targets


def test_cross_file_call_edges_present(graph: StaticGraph) -> None:
    node_ids = {n["id"] for n in graph["nodes"]}
    resolved_cross = [
        e
        for e in graph["edges"]
        if e["kind"] == "call"
        and e["target"] in node_ids
        and e["source"].split(":")[0] != e["target"].split(":")[0]
    ]
    assert len(resolved_cross) >= 1, (
        f"expected ≥1 resolved cross-file call edge, got {len(resolved_cross)}: "
        + str([(e["source"], e["target"]) for e in resolved_cross])
    )


# ---------------------------------------------------------------------------
# Adapter smoke test
# ---------------------------------------------------------------------------


def test_adapter_parse_returns_correct_language() -> None:
    adapter = GoStaticParser()
    graph = adapter.parse(FIXTURE, ParseOptions())
    assert graph["language"] == "go"
    assert len(graph["nodes"]) >= 15
