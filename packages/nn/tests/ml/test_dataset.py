"""Tests for grackle_nn.ml.dataset (Phase 12.1, block M3)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from grackle_nn.ml.dataset import (
    Example,
    build_example,
    load_corpus_dir,
    load_pair,
    split_by_graph,
    stack,
)

if TYPE_CHECKING:
    from pathlib import Path

_GRAPH: dict[str, Any] = {
    "version": 1,
    "language": "python",
    "nodes": [
        {"id": "a.py", "kind": "file", "name": "a.py", "path": "a.py"},
        {"id": "a.py:f", "kind": "function", "name": "f", "path": "a.py", "line": 1},
        {"id": "a.py:g", "kind": "function", "name": "g", "path": "a.py", "line": 2},
    ],
    "edges": [
        {"source": "a.py", "target": "a.py:f", "kind": "import"},
        {"source": "a.py:f", "target": "a.py:g", "kind": "call"},
    ],
}


def test_build_example_alignment_drops_trace_only_ids() -> None:
    heat = {"a.py:f": 3, "a.py:g": 1, "<unresolved>": 99}
    ex = build_example("demo", _GRAPH, heat)
    assert ex.name == "demo"
    assert ex.node_ids == ["a.py", "a.py:f", "a.py:g"]
    assert ex.counts.tolist() == [0, 3, 1]
    assert ex.x.shape == (3, 35)
    assert ex.y.shape == (3,)


def test_build_example_missing_nodes_default_to_zero_count() -> None:
    ex = build_example("demo", _GRAPH, {"a.py:f": 5})
    assert ex.counts.tolist() == [0, 5, 0]
    assert ex.y[0] == 0.0
    assert ex.y[2] == 0.0
    assert ex.y[1] == 1.0  # only touched node -> its own max


def _write_pair(tmp_path: Path, stem: str, graph: dict[str, Any], events: list[str]) -> None:
    (tmp_path / f"{stem}.graph.json").write_text(json.dumps(graph), encoding="utf-8")
    (tmp_path / f"{stem}.trace.jsonl").write_text("\n".join(events) + "\n", encoding="utf-8")


def test_load_pair_round_trip_matches_build_example(tmp_path: Path) -> None:
    events = ['{"event": "call", "node_id": "a.py:f", "metadata": {}}']
    _write_pair(tmp_path, "demo", _GRAPH, events)
    loaded = load_pair("demo", tmp_path / "demo.graph.json", tmp_path / "demo.trace.jsonl")
    direct = build_example("demo", _GRAPH, {"a.py:f": 1})
    assert loaded.node_ids == direct.node_ids
    assert loaded.x.tolist() == direct.x.tolist()
    assert loaded.y.tolist() == direct.y.tolist()
    assert loaded.counts.tolist() == direct.counts.tolist()


def test_load_corpus_dir_sorted_order(tmp_path: Path) -> None:
    for stem in ("charlie", "alpha", "bravo"):
        _write_pair(tmp_path, stem, _GRAPH, ['{"event": "call", "node_id": "a.py:f"}'])
    examples = load_corpus_dir(tmp_path)
    assert [e.name for e in examples] == ["alpha", "bravo", "charlie"]


def test_load_corpus_dir_missing_twin_raises(tmp_path: Path) -> None:
    (tmp_path / "orphan.graph.json").write_text(json.dumps(_GRAPH), encoding="utf-8")
    with pytest.raises(ValueError, match="orphan"):
        load_corpus_dir(tmp_path)

    (tmp_path / "orphan.graph.json").unlink()
    (tmp_path / "orphan2.trace.jsonl").write_text(
        '{"event": "call", "node_id": "a.py:f"}\n', encoding="utf-8"
    )
    with pytest.raises(ValueError, match="orphan2"):
        load_corpus_dir(tmp_path)


def _example(name: str, n: int) -> Example:
    return Example(
        name=name,
        node_ids=[f"{name}:{i}" for i in range(n)],
        x=np.zeros((n, 35), dtype=np.float64),
        y=np.zeros(n, dtype=np.float64),
        counts=np.zeros(n, dtype=np.int64),
    )


def test_split_by_graph_determinism() -> None:
    examples = [_example(f"g{i}", 2) for i in range(6)]
    train1, val1 = split_by_graph(examples, val_count=2, rng=np.random.default_rng(0))
    train2, val2 = split_by_graph(examples, val_count=2, rng=np.random.default_rng(0))
    assert [e.name for e in train1] == [e.name for e in train2]
    assert [e.name for e in val1] == [e.name for e in val2]


def test_split_by_graph_disjoint_and_covers_all() -> None:
    examples = [_example(f"g{i}", 2) for i in range(6)]
    train, val = split_by_graph(examples, val_count=2, rng=np.random.default_rng(1))
    assert len(val) == 2
    assert len(train) == 4
    train_names = {e.name for e in train}
    val_names = {e.name for e in val}
    assert train_names.isdisjoint(val_names)
    assert train_names | val_names == {e.name for e in examples}


def test_split_by_graph_never_straddles_a_graph() -> None:
    # A discriminating proof: every node_id from a given graph name is either
    # entirely in train or entirely in val, never split across both.
    examples = [_example(f"g{i}", 5) for i in range(8)]
    train, val = split_by_graph(examples, val_count=3, rng=np.random.default_rng(2))
    train_ids = {nid for e in train for nid in e.node_ids}
    val_ids = {nid for e in val for nid in e.node_ids}
    assert train_ids.isdisjoint(val_ids)
    for e in examples:
        node_set = set(e.node_ids)
        assert node_set <= train_ids or node_set <= val_ids


def test_split_by_graph_val_count_ge_len_raises() -> None:
    examples = [_example(f"g{i}", 2) for i in range(3)]
    with pytest.raises(ValueError, match="val_count"):
        split_by_graph(examples, val_count=3, rng=np.random.default_rng(0))
    with pytest.raises(ValueError, match="val_count"):
        split_by_graph(examples, val_count=5, rng=np.random.default_rng(0))


def test_stack_concatenates_in_example_order() -> None:
    e0 = build_example("g0", _GRAPH, {"a.py:f": 2})
    e1 = build_example("g1", _GRAPH, {"a.py:g": 4})
    x, y = stack([e0, e1])
    assert x.shape == (6, 35)
    assert y.shape == (6,)
    assert x[:3].tolist() == e0.x.tolist()
    assert x[3:].tolist() == e1.x.tolist()
    assert y[:3].tolist() == e0.y.tolist()
    assert y[3:].tolist() == e1.y.tolist()


def test_stack_empty_examples() -> None:
    x, y = stack([])
    assert x.shape == (0, 35)
    assert y.shape == (0,)
