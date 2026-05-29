"""Tests for grackle.graph_analysis (Phase 8.3).

Design notes:
- StaticGraph dicts are constructed inline; no file I/O.
- Hub-score and cycle detection are tested against graphs with known topology.
- Idempotency: calling enrich_metadata twice must not double entries.
"""

from __future__ import annotations

from typing import Any

from grackle.graph_analysis import enrich_metadata  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _graph(
    nodes: list[str],
    edges: list[tuple[str, str, str]],  # (source, target, kind)
) -> dict[str, Any]:
    """Build a minimal StaticGraph dict for testing."""
    return {
        "version": 1,
        "language": "python",
        "nodes": [{"id": n, "kind": "function", "name": n, "path": "x.py"} for n in nodes],
        "edges": [{"source": s, "target": t, "kind": k} for s, t, k in edges],
    }


# ---------------------------------------------------------------------------
# test_enrich_adds_hub_score
# ---------------------------------------------------------------------------


def test_enrich_adds_hub_score() -> None:
    """Graph with known in/out degrees: top entry should be the highest-score node."""
    # A → B, A → C, D → B
    # in_degree:  A=0, B=2, C=1, D=0
    # out_degree: A=2, B=0, C=0, D=1
    # score:      A=-2, B=2, C=1, D=-1
    g = _graph(
        ["A", "B", "C", "D"],
        [("A", "B", "call"), ("A", "C", "call"), ("D", "B", "call")],
    )
    enrich_metadata(g)  # type: ignore[arg-type]

    hub = g["metadata"]["hub_score"]
    assert isinstance(hub, list)
    assert len(hub) >= 1
    # Top entry must be B (score=2)
    assert hub[0]["node_id"] == "B"
    assert hub[0]["score"] == 2


def test_enrich_hub_score_no_edges() -> None:
    """Graph with no edges: all scores are 0."""
    g = _graph(["X", "Y"], [])
    enrich_metadata(g)  # type: ignore[arg-type]

    hub = g["metadata"]["hub_score"]
    assert all(entry["score"] == 0 for entry in hub)


# ---------------------------------------------------------------------------
# test_enrich_adds_cycles
# ---------------------------------------------------------------------------


def test_enrich_adds_cycles() -> None:
    """Graph with a known cycle: A→B→C→A should appear in cycles."""
    g = _graph(
        ["A", "B", "C"],
        [("A", "B", "call"), ("B", "C", "call"), ("C", "A", "call")],
    )
    enrich_metadata(g)  # type: ignore[arg-type]

    cycles = g["metadata"]["cycles"]
    assert isinstance(cycles, list)
    assert len(cycles) >= 1
    # The cycle must include all three nodes
    cycle = cycles[0]
    assert set(cycle["nodes"]) == {"A", "B", "C"}
    assert cycle["size"] == 3


def test_enrich_adds_self_loop() -> None:
    """Self-loop (A→A) is reported as a size-1 cycle."""
    g = _graph(["A", "B"], [("A", "A", "call"), ("A", "B", "call")])
    enrich_metadata(g)  # type: ignore[arg-type]

    cycles = g["metadata"]["cycles"]
    assert any(c["size"] == 1 and "A" in c["nodes"] for c in cycles)


# ---------------------------------------------------------------------------
# test_enrich_no_cycles
# ---------------------------------------------------------------------------


def test_enrich_no_cycles() -> None:
    """DAG: cycles list should be empty."""
    # A → B → C (simple chain, no back-edges)
    g = _graph(
        ["A", "B", "C"],
        [("A", "B", "call"), ("B", "C", "call")],
    )
    enrich_metadata(g)  # type: ignore[arg-type]

    cycles = g["metadata"]["cycles"]
    assert cycles == []


# ---------------------------------------------------------------------------
# test_enrich_idempotent
# ---------------------------------------------------------------------------


def test_enrich_idempotent() -> None:
    """Calling enrich_metadata twice does not double entries."""
    g = _graph(
        ["A", "B"],
        [("A", "B", "call"), ("B", "A", "call")],
    )
    enrich_metadata(g)  # type: ignore[arg-type]
    hub_after_first = list(g["metadata"]["hub_score"])
    cycles_after_first = list(g["metadata"]["cycles"])

    enrich_metadata(g)  # type: ignore[arg-type]

    assert g["metadata"]["hub_score"] == hub_after_first
    assert g["metadata"]["cycles"] == cycles_after_first


# ---------------------------------------------------------------------------
# test_enrich_empty_graph
# ---------------------------------------------------------------------------


def test_enrich_empty_graph() -> None:
    """Empty graph (no nodes/edges) produces empty hub_score and cycles."""
    g = _graph([], [])
    enrich_metadata(g)  # type: ignore[arg-type]

    assert g["metadata"]["hub_score"] == []
    assert g["metadata"]["cycles"] == []
