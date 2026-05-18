"""Tests for the Rust symbol resolver."""

from __future__ import annotations

from pathlib import Path

import pytest

from grackle.adapters.base import ParseOptions, StaticGraph
from grackle.cache import CacheManager
from grackle.rust_parser.walker import RustWalker

FIXTURE = Path(__file__).parents[4] / "fixtures" / "tiny-rust-app"


@pytest.fixture(scope="module")
def graph() -> StaticGraph:
    cache = CacheManager(FIXTURE)
    return RustWalker(FIXTURE, ParseOptions(), cache).walk()


def test_implements_edges_resolved(graph: StaticGraph) -> None:
    node_ids = {n["id"] for n in graph["nodes"]}
    resolved_impls = [
        e for e in graph["edges"] if e["kind"] == "implements" and e["target"] in node_ids
    ]
    assert len(resolved_impls) >= 2, (
        f"expected ≥2 resolved implements edges, got {len(resolved_impls)}"
    )


def test_inherit_edge_resolved(graph: StaticGraph) -> None:
    node_ids = {n["id"] for n in graph["nodes"]}
    resolved_inherits = [
        e for e in graph["edges"] if e["kind"] == "inherit" and e["target"] in node_ids
    ]
    assert len(resolved_inherits) >= 1


def test_cross_crate_implements(graph: StaticGraph) -> None:
    node_ids = {n["id"] for n in graph["nodes"]}
    cross_crate = [
        e
        for e in graph["edges"]
        if e["kind"] == "implements"
        and e["target"] in node_ids
        and e["source"].split(":")[0] != e["target"].split(":")[0]
    ]
    assert len(cross_crate) >= 1, "expected ≥1 cross-crate implements edge"


def test_cross_crate_call_resolved(graph: StaticGraph) -> None:
    node_ids = {n["id"] for n in graph["nodes"]}
    cross_crate_calls = [
        e
        for e in graph["edges"]
        if e["kind"] == "call"
        and e["target"] in node_ids
        and e["source"].split(":")[0] != e["target"].split(":")[0]
    ]
    assert len(cross_crate_calls) >= 1, (
        f"expected ≥1 resolved cross-crate call, got {len(cross_crate_calls)}"
    )
