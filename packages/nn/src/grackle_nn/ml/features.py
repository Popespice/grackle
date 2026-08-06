"""Structural feature extraction for the self-supervised hotspot-prediction model.

Computes a fixed-width feature matrix from a **raw** static graph dict — the
bare ``grackle parse`` output, never the server-side ``enrich_metadata``
result (``hub_score``/``cycles`` are truncated top-N and only exist
server-side; see ADR-0028/D12.1). Graph params are ``Mapping[str, Any]`` —
this module never imports ``grackle`` at runtime, not even under
``TYPE_CHECKING``.

Column layout (``FEATURE_NAMES``, 35 wide) is a versioned contract:
reordering, inserting, or removing a column requires a ``FEATURE_VERSION``
bump, since a trained model's standardization stats are keyed to a fixed
column order.
"""

from __future__ import annotations

import math
from collections import deque
from typing import TYPE_CHECKING, Any

import numpy as np

from grackle_nn.ml._rank_utils import tie_averaged_ranks_0indexed

if TYPE_CHECKING:
    from collections.abc import Mapping

    from grackle_nn._types import Array

FEATURE_VERSION = 1

# Edge-kind buckets: an edge kind buckets by exact match, except any kind
# starting with "cross_language" (cross_language_call, cross_language_spawn,
# cross_language_http_route, ...) which collapses into bucket 4. Anything
# else (interface/type_alias/enum/struct edges, future open-string kinds)
# falls into "other" — see ADR-0004 (open strings, not enums).
_EDGE_BUCKETS = ("import", "call", "inherit", "implements", "cross_language", "other")
_KIND_BUCKETS = ("function", "method", "class", "file", "other")

FEATURE_NAMES: tuple[str, ...] = (
    "log1p_in_degree",
    "log1p_out_degree",
    *(f"log1p_in_{b}" for b in _EDGE_BUCKETS),
    *(f"log1p_out_{b}" for b in _EDGE_BUCKETS),
    *(f"kind_{k}" for k in _KIND_BUCKETS),
    "in_cycle",
    "log1p_scc_size",
    "in_degree_percentile",
    "out_degree_percentile",
    "log1p_file_fan_in",
    "log1p_file_fan_out",
    "log1p_bfs_depth",
    "reachable",
    "log1p_name_len",
    "is_dunder",
    "is_private",
    "is_test",
    "is_async",
    "has_decorators",
    "log1p_path_depth",
    "log1p_line",
)
assert len(FEATURE_NAMES) == 35  # noqa: S101 — a broken column count breaks every consumer


def _edge_bucket(kind: str) -> int:
    if kind in ("import", "call", "inherit", "implements"):
        return _EDGE_BUCKETS.index(kind)
    if kind.startswith("cross_language"):
        return _EDGE_BUCKETS.index("cross_language")
    return _EDGE_BUCKETS.index("other")


def _kind_bucket(kind: str) -> int:
    if kind in ("function", "method", "class", "file"):
        return _KIND_BUCKETS.index(kind)
    return _KIND_BUCKETS.index("other")


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__") and len(name) >= 5


def _is_test(name: str, path: str) -> bool:
    if name.startswith("test_"):
        return True
    parts = path.split("/")
    if "tests" in parts:
        return True
    return bool(parts) and parts[-1].startswith("test_")


def _path_depth(path: str) -> int:
    return len(path.split("/")) if path else 0


def _percentile(values: Array) -> Array:
    """Average-rank percentile in [0, 1], ties share the mean rank of their group.

    A single-node graph (n<=1) has no meaningful spread and returns all zeros.
    """
    n = values.shape[0]
    if n <= 1:
        return np.zeros(n, dtype=np.float64)
    result: Array = tie_averaged_ranks_0indexed(values) / (n - 1)
    return result


def _tarjan_scc(adj: list[list[int]], n: int) -> tuple[list[int], list[int]]:
    """Iterative Tarjan SCC (explicit-stack DFS — no Python recursion limit).

    Reimplementation of graph_analysis.py's ``_compute_cycles`` algorithm,
    independent of the agent package (this module never imports it). Returns
    ``(scc_id, scc_sizes)``: ``scc_id[i]`` is node *i*'s component index into
    ``scc_sizes``. Self-loops are not represented in *adj* — callers track
    them separately, matching the agent's algorithm exactly.
    """
    index_counter = 0
    indices: list[int | None] = [None] * n
    lowlinks: list[int] = [0] * n
    on_stack: list[bool] = [False] * n
    stack: list[int] = []
    scc_id: list[int] = [-1] * n
    scc_sizes: list[int] = []

    for start in range(n):
        if indices[start] is not None:
            continue
        call_stack: list[tuple[int, int]] = [(start, 0)]
        indices[start] = lowlinks[start] = index_counter
        index_counter += 1
        stack.append(start)
        on_stack[start] = True

        while call_stack:
            v, pos = call_stack[-1]
            if pos < len(adj[v]):
                call_stack[-1] = (v, pos + 1)
                w = adj[v][pos]
                if indices[w] is None:
                    indices[w] = lowlinks[w] = index_counter
                    index_counter += 1
                    stack.append(w)
                    on_stack[w] = True
                    call_stack.append((w, 0))
                elif on_stack[w]:
                    w_index = indices[w]
                    assert w_index is not None
                    lowlinks[v] = min(lowlinks[v], w_index)
            else:
                call_stack.pop()
                if call_stack:
                    parent = call_stack[-1][0]
                    lowlinks[parent] = min(lowlinks[parent], lowlinks[v])
                if lowlinks[v] == indices[v]:
                    scc_index = len(scc_sizes)
                    size = 0
                    while True:
                        w = stack.pop()
                        on_stack[w] = False
                        scc_id[w] = scc_index
                        size += 1
                        if w == v:
                            break
                    scc_sizes.append(size)

    return scc_id, scc_sizes


def extract_features(graph: Mapping[str, Any]) -> tuple[list[str], Array]:
    """Extract the 35-column structural feature matrix from a raw static graph.

    Returns ``(node_ids, X)`` where ``X.shape == (len(node_ids), 35)`` and
    ``node_ids[i]`` labels row ``i``, in the graph's own node order. Edges
    whose source or target is not present in ``graph["nodes"]`` are dropped
    (unresolved external references — the same tolerance
    ``graph_analysis.py``'s cycle detection uses).
    """
    nodes = list(graph.get("nodes") or [])
    edges = list(graph.get("edges") or [])
    node_ids = [n["id"] for n in nodes]
    n = len(node_ids)
    if n == 0:
        return [], np.zeros((0, len(FEATURE_NAMES)), dtype=np.float64)

    id_to_idx: dict[str, int] = {nid: i for i, nid in enumerate(node_ids)}
    paths = [n.get("path") or "" for n in nodes]

    in_bucket = np.zeros((n, len(_EDGE_BUCKETS)), dtype=np.float64)
    out_bucket = np.zeros((n, len(_EDGE_BUCKETS)), dtype=np.float64)
    adj: list[list[int]] = [[] for _ in range(n)]
    call_import_adj: list[list[int]] = [[] for _ in range(n)]
    call_import_in_degree = np.zeros(n, dtype=np.int64)
    self_loop = np.zeros(n, dtype=bool)
    file_fan_in: dict[str, int] = {}
    file_fan_out: dict[str, int] = {}

    # One edge pass: degree buckets, adjacency (for SCC), call+import
    # adjacency/in-degree (for BFS), and cross-file fan counts.
    for edge in edges:
        src_idx = id_to_idx.get(edge.get("source"))
        tgt_idx = id_to_idx.get(edge.get("target"))
        if src_idx is None or tgt_idx is None:
            continue
        kind = edge.get("kind", "")
        bucket = _edge_bucket(kind)
        out_bucket[src_idx, bucket] += 1.0
        in_bucket[tgt_idx, bucket] += 1.0
        if src_idx == tgt_idx:
            self_loop[src_idx] = True
        else:
            adj[src_idx].append(tgt_idx)
        if kind in ("call", "import") and src_idx != tgt_idx:
            # Excludes self-loops, matching call_import_adj below (the graph
            # BFS actually walks) and adj/self_loop above (the SCC graph) --
            # a self-loop must never be able to make the entry-set criterion
            # (call_import_in_degree == 0) disagree with what the traversal
            # itself can reach.
            call_import_in_degree[tgt_idx] += 1
            call_import_adj[src_idx].append(tgt_idx)
        src_file = paths[src_idx]
        tgt_file = paths[tgt_idx]
        if src_file != tgt_file:
            file_fan_out[src_file] = file_fan_out.get(src_file, 0) + 1
            file_fan_in[tgt_file] = file_fan_in.get(tgt_file, 0) + 1

    in_degree = in_bucket.sum(axis=1)
    out_degree = out_bucket.sum(axis=1)

    scc_id, scc_sizes = _tarjan_scc(adj, n)
    scc_size_of_node = np.array([scc_sizes[scc_id[i]] for i in range(n)], dtype=np.float64)
    in_cycle = np.array(
        [scc_sizes[scc_id[i]] > 1 or bool(self_loop[i]) for i in range(n)], dtype=np.float64
    )

    # BFS from the entry set (file nodes, or any node with zero call/import
    # in-degree) over call+import edges only.
    entry = [i for i in range(n) if nodes[i].get("kind") == "file" or call_import_in_degree[i] == 0]
    bfs_depth = [-1] * n
    dq: deque[int] = deque()
    for i in entry:
        if bfs_depth[i] == -1:
            bfs_depth[i] = 0
            dq.append(i)
    while dq:
        u = dq.popleft()
        for v in call_import_adj[u]:
            if bfs_depth[v] == -1:
                bfs_depth[v] = bfs_depth[u] + 1
                dq.append(v)
    reachable = np.array([1.0 if d >= 0 else 0.0 for d in bfs_depth], dtype=np.float64)
    bfs_depth_safe = np.array([max(d, 0) for d in bfs_depth], dtype=np.float64)

    in_pct = _percentile(in_degree)
    out_pct = _percentile(out_degree)

    rows = np.zeros((n, len(FEATURE_NAMES)), dtype=np.float64)
    for i, node in enumerate(nodes):
        metadata = node.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        name = node.get("name", "")
        path = paths[i]
        rows[i, 0] = math.log1p(in_degree[i])
        rows[i, 1] = math.log1p(out_degree[i])
        rows[i, 2:8] = np.log1p(in_bucket[i])
        rows[i, 8:14] = np.log1p(out_bucket[i])
        rows[i, 14 + _kind_bucket(node.get("kind", ""))] = 1.0
        rows[i, 19] = in_cycle[i]
        rows[i, 20] = math.log1p(scc_size_of_node[i])
        rows[i, 21] = in_pct[i]
        rows[i, 22] = out_pct[i]
        rows[i, 23] = math.log1p(file_fan_in.get(path, 0))
        rows[i, 24] = math.log1p(file_fan_out.get(path, 0))
        rows[i, 25] = math.log1p(bfs_depth_safe[i])
        rows[i, 26] = reachable[i]
        rows[i, 27] = math.log1p(len(name))
        dunder = _is_dunder(name)
        rows[i, 28] = 1.0 if dunder else 0.0
        rows[i, 29] = 1.0 if (name.startswith("_") and not dunder) else 0.0
        rows[i, 30] = 1.0 if _is_test(name, path) else 0.0
        rows[i, 31] = 1.0 if metadata.get("is_async") else 0.0
        rows[i, 32] = 1.0 if metadata.get("decorators") else 0.0
        rows[i, 33] = math.log1p(_path_depth(path))
        rows[i, 34] = math.log1p(node.get("line") or 0)

    return node_ids, rows
