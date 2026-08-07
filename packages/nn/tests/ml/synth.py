"""Synthetic (graph, heat) corpus generator for the M6 acceptance test.

Test-only — never shipped under ``src/``. Builds a preferential-attachment
call/import graph with a planted cycle, then derives ground-truth heat from a
formula that deliberately carries non-degree signal (cycle membership,
method-ness, test-ness) so a model using the full 35-column feature vector
can, in principle, beat a raw-in-degree baseline. Ground truth reuses
``extract_features``'s own in-cycle/reachability/kind computation so the
labels are defined consistently with what the model actually sees.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from grackle_nn.ml.features import FEATURE_NAMES, extract_features

_PA_EDGES_PER_NODE = 3


def _build_graph(rng: np.random.Generator) -> dict[str, Any]:
    n_nodes = int(rng.integers(60, 121))
    n_files = int(rng.integers(4, 9))
    files = [f"file{i}.py" for i in range(n_files)]

    nodes: list[dict[str, Any]] = [
        {"id": fpath, "kind": "file", "name": fpath, "path": fpath} for fpath in files
    ]

    is_test_flags = rng.random(n_nodes) < 0.15
    func_ids: list[str] = []
    for i in range(n_nodes):
        fpath = files[int(rng.integers(0, n_files))]
        kind = "method" if rng.random() < 0.4 else "function"
        name = f"{'test_' if is_test_flags[i] else ''}fn{i}"
        node_id = f"{fpath}:{name}"
        nodes.append({"id": node_id, "kind": kind, "name": name, "path": fpath, "line": i + 1})
        func_ids.append(node_id)

    edges: list[dict[str, Any]] = []
    in_deg_count = dict.fromkeys(func_ids, 0)

    # Preferential attachment: each function (after the first) draws M edges
    # to earlier functions, weighted by (in-degree + 1) -- rich-get-richer,
    # so a handful of "hub" functions accumulate most of the call in-degree.
    for idx, nid in enumerate(func_ids):
        if idx == 0:
            continue
        candidates = func_ids[:idx]
        weights = np.array([in_deg_count[c] + 1 for c in candidates], dtype=np.float64)
        weights /= weights.sum()
        m = min(_PA_EDGES_PER_NODE, idx)
        target_idx = rng.choice(len(candidates), size=m, replace=False, p=weights)
        for ti in target_idx:
            target = candidates[int(ti)]
            kind = "import" if rng.random() < 0.2 else "call"
            edges.append({"source": nid, "target": target, "kind": kind})
            in_deg_count[target] += 1

    # Plant one 3-5 node call cycle. Every other edge above points strictly
    # from a later-created node to an earlier one (a DAG by construction), so
    # this ring is the only cycle the graph can contain.
    cycle_size = int(rng.integers(3, 6))
    cycle_idx = rng.choice(len(func_ids), size=cycle_size, replace=False)
    cycle_nodes = [func_ids[int(i)] for i in cycle_idx]
    for i in range(cycle_size):
        src = cycle_nodes[i]
        tgt = cycle_nodes[(i + 1) % cycle_size]
        edges.append({"source": src, "target": tgt, "kind": "call"})

    return {"version": 1, "language": "python", "nodes": nodes, "edges": edges}


def make_synthetic_pair(seed: int) -> tuple[dict[str, Any], dict[str, int]]:
    """Build one synthetic ``(graph, heat)`` pair with planted non-degree signal.

    Ground-truth heat: ``count = round(expm1(1.5*log1p(in_call_deg) +
    1.2*in_cycle + 0.5*is_method - 1.5*is_test + N(0, 0.3)))``, floored at 0,
    and forced to 0 for any node ``extract_features`` marks unreachable.
    """
    rng = np.random.default_rng(seed)
    graph = _build_graph(rng)
    node_ids, x = extract_features(graph)

    in_call_deg = np.expm1(x[:, FEATURE_NAMES.index("log1p_in_call")])
    in_cycle = x[:, FEATURE_NAMES.index("in_cycle")]
    is_method = x[:, FEATURE_NAMES.index("kind_method")]
    is_test = x[:, FEATURE_NAMES.index("is_test")]
    reachable = x[:, FEATURE_NAMES.index("reachable")]

    noise = rng.normal(0.0, 0.3, size=len(node_ids))
    raw = 1.5 * np.log1p(in_call_deg) + 1.2 * in_cycle + 0.5 * is_method - 1.5 * is_test + noise
    count = np.maximum(np.round(np.expm1(raw)), 0.0)
    count = np.where(reachable > 0.0, count, 0.0)

    heat = {nid: int(c) for nid, c in zip(node_ids, count, strict=True) if c > 0}
    return graph, heat
