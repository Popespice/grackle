"""Integration test: parse fixtures/tiny-ts-app and verify golden counts."""

from __future__ import annotations

from pathlib import Path

import pytest

from grackle.adapters.base import ParseOptions, StaticGraph
from grackle.cache import CacheManager
from grackle.typescript_parser.adapter import TypeScriptStaticParser
from grackle.typescript_parser.walker import TSWalker

FIXTURE = Path(__file__).parents[4] / "fixtures" / "tiny-ts-app"


@pytest.fixture(scope="module")
def graph() -> StaticGraph:
    cache = CacheManager(FIXTURE)
    opts = ParseOptions()
    return TSWalker(FIXTURE, opts, cache).walk()


# ---------------------------------------------------------------------------
# Node counts
# ---------------------------------------------------------------------------


def test_at_least_25_nodes(graph: StaticGraph) -> None:
    assert len(graph["nodes"]) >= 25


def test_file_nodes(graph: StaticGraph) -> None:
    file_ids = {n["id"] for n in graph["nodes"] if n["kind"] == "file"}
    assert "src/types.ts" in file_ids
    assert "src/models.ts" in file_ids
    assert "src/utils.ts" in file_ids
    assert "src/services.ts" in file_ids
    assert "src/index.ts" in file_ids


def test_at_least_one_of_each_kind(graph: StaticGraph) -> None:
    kinds = {n["kind"] for n in graph["nodes"]}
    for expected in ("file", "class", "interface", "function", "method", "type_alias", "enum"):
        assert expected in kinds, f"missing kind: {expected!r}"


def test_specific_class_nodes(graph: StaticGraph) -> None:
    class_ids = {n["id"] for n in graph["nodes"] if n["kind"] == "class"}
    assert "src/models.ts:BaseEntity" in class_ids
    assert "src/models.ts:User" in class_ids
    assert "src/services.ts:UserService" in class_ids


def test_specific_interface_nodes(graph: StaticGraph) -> None:
    iface_ids = {n["id"] for n in graph["nodes"] if n["kind"] == "interface"}
    assert "src/types.ts:Serializable" in iface_ids
    assert "src/types.ts:Printable" in iface_ids


def test_specific_type_alias_nodes(graph: StaticGraph) -> None:
    alias_ids = {n["id"] for n in graph["nodes"] if n["kind"] == "type_alias"}
    assert "src/types.ts:UserId" in alias_ids
    assert "src/types.ts:UserRole" in alias_ids


def test_specific_enum_node(graph: StaticGraph) -> None:
    enum_ids = {n["id"] for n in graph["nodes"] if n["kind"] == "enum"}
    assert "src/types.ts:Status" in enum_ids


# ---------------------------------------------------------------------------
# Edge presence
# ---------------------------------------------------------------------------


def test_inherit_edge_user_extends_base(graph: StaticGraph) -> None:
    inherits = [e for e in graph["edges"] if e["kind"] == "inherit"]
    assert any(
        e["source"] == "src/models.ts:User" and e["target"] == "src/models.ts:BaseEntity"
        for e in inherits
    ), "User should extend BaseEntity (resolved locally)"


def test_implements_edges_present(graph: StaticGraph) -> None:
    impls = [e for e in graph["edges"] if e["kind"] == "implements"]
    assert len(impls) >= 2
    sources = {e["source"] for e in impls}
    assert "src/models.ts:User" in sources


def test_implements_resolved_to_interface_nodes(graph: StaticGraph) -> None:
    iface_ids = {n["id"] for n in graph["nodes"] if n["kind"] == "interface"}
    impls = [e for e in graph["edges"] if e["kind"] == "implements"]
    resolved_targets = {
        e["target"] for e in impls if e.get("metadata", {}).get("resolved") is not False
    }
    assert resolved_targets & iface_ids, (
        "at least one implements edge should resolve to an interface node"
    )


def test_import_edges_present(graph: StaticGraph) -> None:
    imports = [e for e in graph["edges"] if e["kind"] == "import"]
    assert len(imports) >= 4


def test_at_least_3_resolved_cross_file_call_edges(graph: StaticGraph) -> None:
    resolved_calls = [
        e
        for e in graph["edges"]
        if e["kind"] == "call"
        and e["target"] in {n["id"] for n in graph["nodes"]}
        and e["source"].split(":")[0] != e["target"].split(":")[0]
    ]
    assert len(resolved_calls) >= 3, (
        f"expected ≥3 resolved cross-file call edges, got {len(resolved_calls)}: "
        + str([(e["source"], e["target"]) for e in resolved_calls])
    )


# ---------------------------------------------------------------------------
# Adapter smoke test
# ---------------------------------------------------------------------------


def test_adapter_parse_returns_same_result() -> None:
    adapter = TypeScriptStaticParser()
    opts = ParseOptions()
    graph = adapter.parse(FIXTURE, opts)
    assert graph["language"] == "typescript"
    assert len(graph["nodes"]) >= 25
