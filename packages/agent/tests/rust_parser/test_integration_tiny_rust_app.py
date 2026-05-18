"""Integration test: parse fixtures/tiny-rust-app and verify golden counts."""

from __future__ import annotations

from pathlib import Path

import pytest

from grackle.adapters.base import ParseOptions, StaticGraph
from grackle.cache import CacheManager
from grackle.rust_parser.adapter import RustStaticParser
from grackle.rust_parser.walker import RustWalker

FIXTURE = Path(__file__).parents[4] / "fixtures" / "tiny-rust-app"


@pytest.fixture(scope="module")
def graph() -> StaticGraph:
    cache = CacheManager(FIXTURE)
    opts = ParseOptions()
    return RustWalker(FIXTURE, opts, cache).walk()


# ---------------------------------------------------------------------------
# Node counts
# ---------------------------------------------------------------------------


def test_at_least_20_nodes(graph: StaticGraph) -> None:
    assert len(graph["nodes"]) >= 20


def test_file_nodes_present(graph: StaticGraph) -> None:
    file_ids = {n["id"] for n in graph["nodes"] if n["kind"] == "file"}
    assert "crates/models/src/lib.rs" in file_ids
    assert "crates/models/src/utils.rs" in file_ids
    assert "crates/api/src/lib.rs" in file_ids
    assert "crates/api/src/handlers.rs" in file_ids


def test_struct_nodes_present(graph: StaticGraph) -> None:
    struct_ids = {n["id"] for n in graph["nodes"] if n["kind"] == "struct"}
    assert "crates/models/src/lib.rs:User" in struct_ids
    assert "crates/api/src/lib.rs:UserRepository" in struct_ids


def test_trait_nodes_as_interface(graph: StaticGraph) -> None:
    iface_ids = {n["id"] for n in graph["nodes"] if n["kind"] == "interface"}
    assert "crates/models/src/lib.rs:Store" in iface_ids
    assert "crates/models/src/lib.rs:UserStore" in iface_ids


def test_traits_have_subkind_trait(graph: StaticGraph) -> None:
    for n in graph["nodes"]:
        if n["kind"] == "interface":
            assert n.get("metadata", {}).get("subkind") == "trait"


def test_type_alias_present(graph: StaticGraph) -> None:
    alias_ids = {n["id"] for n in graph["nodes"] if n["kind"] == "type_alias"}
    assert "crates/models/src/lib.rs:UserId" in alias_ids


def test_enum_present(graph: StaticGraph) -> None:
    enum_ids = {n["id"] for n in graph["nodes"] if n["kind"] == "enum"}
    assert "crates/models/src/lib.rs:UserStatus" in enum_ids


def test_method_nodes_present(graph: StaticGraph) -> None:
    method_ids = {n["id"] for n in graph["nodes"] if n["kind"] == "method"}
    assert "crates/models/src/lib.rs:User.new" in method_ids
    assert "crates/models/src/lib.rs:User.is_active" in method_ids
    assert "crates/api/src/lib.rs:UserRepository.new" in method_ids


def test_function_nodes_present(graph: StaticGraph) -> None:
    func_ids = {n["id"] for n in graph["nodes"] if n["kind"] == "function"}
    assert "crates/models/src/utils.rs:format_user" in func_ids
    assert "crates/api/src/handlers.rs:handle_get_users" in func_ids


def test_at_least_one_kind_each(graph: StaticGraph) -> None:
    kinds = {n["kind"] for n in graph["nodes"]}
    for expected in ("file", "struct", "interface", "function", "method", "type_alias", "enum"):
        assert expected in kinds, f"missing kind: {expected!r}"


# ---------------------------------------------------------------------------
# Edge presence
# ---------------------------------------------------------------------------


def test_import_edges_present(graph: StaticGraph) -> None:
    imports = [e for e in graph["edges"] if e["kind"] == "import"]
    assert len(imports) >= 3


def test_implements_edges_present(graph: StaticGraph) -> None:
    impls = [e for e in graph["edges"] if e["kind"] == "implements"]
    assert len(impls) >= 2


def test_inherit_edge_supertrait(graph: StaticGraph) -> None:
    node_ids = {n["id"] for n in graph["nodes"]}
    resolved_inherits = [
        e for e in graph["edges"] if e["kind"] == "inherit" and e["target"] in node_ids
    ]
    assert len(resolved_inherits) >= 1
    assert any(
        e["source"] == "crates/models/src/lib.rs:UserStore"
        and e["target"] == "crates/models/src/lib.rs:Store"
        for e in resolved_inherits
    )


def test_cross_crate_implements_resolved(graph: StaticGraph) -> None:
    node_ids = {n["id"] for n in graph["nodes"]}
    cross = [
        e
        for e in graph["edges"]
        if e["kind"] == "implements"
        and e["target"] in node_ids
        and e["source"].split(":")[0] != e["target"].split(":")[0]
    ]
    assert len(cross) >= 1


def test_cross_crate_call_edges_present(graph: StaticGraph) -> None:
    node_ids = {n["id"] for n in graph["nodes"]}
    resolved_cross = [
        e
        for e in graph["edges"]
        if e["kind"] == "call"
        and e["target"] in node_ids
        and e["source"].split(":")[0] != e["target"].split(":")[0]
    ]
    assert len(resolved_cross) >= 1, (
        f"expected ≥1 resolved cross-crate call, got {len(resolved_cross)}: "
        + str([(e["source"], e["target"]) for e in resolved_cross])
    )


# ---------------------------------------------------------------------------
# Adapter smoke test
# ---------------------------------------------------------------------------


def test_adapter_parse_returns_correct_language() -> None:
    adapter = RustStaticParser()
    graph = adapter.parse(FIXTURE, ParseOptions())
    assert graph["language"] == "rust"
    assert len(graph["nodes"]) >= 20
