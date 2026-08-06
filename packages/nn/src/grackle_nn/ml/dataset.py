"""Corpus loading and graph-level splitting for the hotspot-prediction model.

D12.5: the corpus on disk is raw ``(graph, trace)`` pairs, never a features
dump — re-featurizing at load time survives ``FEATURE_VERSION`` bumps. This
module never imports ``grackle`` at runtime, not even under ``TYPE_CHECKING``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from grackle_nn.ml.features import FEATURE_NAMES, extract_features
from grackle_nn.ml.labels import heat_from_jsonl, make_targets

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from grackle_nn._types import Array, IntArray

_GRAPH_SUFFIX = ".graph.json"
_TRACE_SUFFIX = ".trace.jsonl"


@dataclass(frozen=True, slots=True)
class Example:
    """One graph's features + heat labels, aligned row-for-row by ``node_ids``."""

    name: str
    node_ids: list[str]
    x: Array  # (N, 35) raw (unstandardized) features
    y: Array  # (N,) targets, D12.2
    counts: IntArray  # (N,) raw heat counts (0 for untouched nodes)


def build_example(name: str, graph: Mapping[str, Any], heat: Mapping[str, int]) -> Example:
    """Build an :class:`Example` from a parsed graph + a ``node_id -> count`` heat map.

    Alignment is by ``node_ids`` from ``extract_features`` — trace-only ids
    with no static-graph counterpart (e.g. ``<unresolved>``) simply never
    match a row and fall out.
    """
    node_ids, x = extract_features(graph)
    counts: IntArray = np.array([heat.get(nid, 0) for nid in node_ids], dtype=np.int64)
    y = make_targets(node_ids, heat)
    return Example(name=name, node_ids=node_ids, x=x, y=y, counts=counts)


def load_pair(name: str, graph_path: Path, trace_path: Path) -> Example:
    """Load one ``(graph.json, trace.jsonl)`` pair from disk into an :class:`Example`."""
    with graph_path.open("rb") as f:
        graph = json.load(f)
    heat = heat_from_jsonl(trace_path)
    return build_example(name, graph, heat)


def load_corpus_dir(corpus_dir: Path) -> list[Example]:
    """Load every ``{name}.graph.json`` + ``{name}.trace.jsonl`` pair in *corpus_dir*.

    Returns examples sorted by stem name (deterministic order). Raises
    ``ValueError`` if a stem has one file but not its twin.
    """
    graph_stems = {p.name[: -len(_GRAPH_SUFFIX)] for p in corpus_dir.glob(f"*{_GRAPH_SUFFIX}")}
    trace_stems = {p.name[: -len(_TRACE_SUFFIX)] for p in corpus_dir.glob(f"*{_TRACE_SUFFIX}")}
    missing_trace = graph_stems - trace_stems
    missing_graph = trace_stems - graph_stems
    if missing_trace or missing_graph:
        orphans = sorted(missing_trace | missing_graph)
        raise ValueError(f"corpus pair(s) missing a twin file in {corpus_dir}: {orphans}")
    return [
        load_pair(
            stem,
            corpus_dir / f"{stem}{_GRAPH_SUFFIX}",
            corpus_dir / f"{stem}{_TRACE_SUFFIX}",
        )
        for stem in sorted(graph_stems)
    ]


def split_by_graph(
    examples: Sequence[Example], *, val_count: int, rng: np.random.Generator
) -> tuple[list[Example], list[Example]]:
    """Split whole :class:`Example` graphs into train/val (never split a graph's rows).

    ``rng.permutation`` shuffles example indices; the first *val_count* go to
    validation. Raises ``ValueError`` if ``val_count >= len(examples)``.
    """
    n = len(examples)
    if val_count >= n:
        raise ValueError(f"val_count ({val_count}) must be less than len(examples) ({n})")
    perm = rng.permutation(n)
    val_examples = [examples[i] for i in perm[:val_count]]
    train_examples = [examples[i] for i in perm[val_count:]]
    return train_examples, val_examples


def stack(examples: Sequence[Example]) -> tuple[Array, Array]:
    """Concatenate every example's feature/target rows into one ``(X, y)`` pair.

    Row order is example order, then within-example node order — used to pool
    multiple graphs into a single training set (never for held-out
    evaluation, which stays per-graph).
    """
    if not examples:
        return np.zeros((0, len(FEATURE_NAMES)), dtype=np.float64), np.zeros(0, dtype=np.float64)
    x = np.concatenate([e.x for e in examples], axis=0)
    y = np.concatenate([e.y for e in examples], axis=0)
    return x, y
