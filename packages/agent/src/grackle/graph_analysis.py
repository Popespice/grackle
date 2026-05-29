"""Agent-side graph analysis: hub-score and cycle detection injected into graph.metadata.

Results are stored in ``graph["metadata"]`` so the frontend's ``Analysis<T>``
registry can read them without re-computing.  Both analyses are cheap enough to
run synchronously at serve time on startup.

Hub-score
---------
``score = in_degree - out_degree`` for each node.  The top 50 nodes by score
are returned sorted descending.

Cycle detection (Tarjan SCC — iterative)
-----------------------------------------
A purely iterative Tarjan SCC is used to avoid Python recursion-depth limits on
large graphs.  Only SCCs with size > 1 are returned as "cycles", plus size-1
components that have a self-loop edge.  The result is capped at 100 entries
(sorted descending by SCC size) to keep the payload bounded on large codebases.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from grackle.adapters.base import StaticGraph

_MAX_HUB_ENTRIES = 50
_MAX_CYCLE_ENTRIES = 100


def enrich_metadata(graph: StaticGraph) -> None:
    """Compute hub-score and cycle detection and inject into ``graph['metadata']``.

    Modifies *graph* in-place.  Safe to call multiple times — subsequent calls
    overwrite the previous results (idempotent with respect to the final state).
    """
    graph.setdefault("metadata", {})
    graph["metadata"]["hub_score"] = _compute_hub_score(graph)
    graph["metadata"]["cycles"] = _compute_cycles(graph)


# ---------------------------------------------------------------------------
# Hub-score
# ---------------------------------------------------------------------------


def _compute_hub_score(graph: StaticGraph) -> list[dict[str, Any]]:
    """Return top-50 nodes by (in_degree - out_degree), sorted descending."""
    in_degree: dict[str, int] = {}
    out_degree: dict[str, int] = {}

    for node in graph["nodes"]:
        nid = node["id"]
        in_degree.setdefault(nid, 0)
        out_degree.setdefault(nid, 0)

    for edge in graph["edges"]:
        src = edge["source"]
        tgt = edge["target"]
        out_degree[src] = out_degree.get(src, 0) + 1
        in_degree[tgt] = in_degree.get(tgt, 0) + 1

    entries: list[dict[str, Any]] = [
        {"node_id": nid, "score": in_degree.get(nid, 0) - out_degree.get(nid, 0)}
        for nid in in_degree
    ]
    entries.sort(key=lambda x: (-x["score"], x["node_id"]))
    return entries[:_MAX_HUB_ENTRIES]


# ---------------------------------------------------------------------------
# Cycle detection (iterative Tarjan SCC)
# ---------------------------------------------------------------------------


def _compute_cycles(graph: StaticGraph) -> list[dict[str, Any]]:
    """Return SCCs with size > 1 or self-loops, capped at 100, sorted by size desc."""
    # Build adjacency list from node IDs
    node_ids = [n["id"] for n in graph["nodes"]]
    id_to_idx: dict[str, int] = {nid: i for i, nid in enumerate(node_ids)}
    n = len(node_ids)

    adj: list[list[int]] = [[] for _ in range(n)]
    # Track self-loop nodes
    self_loop_nodes: set[int] = set()
    # Track edge kinds per (src, tgt) pair for metadata
    edge_kinds: dict[tuple[int, int], set[str]] = {}

    for edge in graph["edges"]:
        src_idx = id_to_idx.get(edge["source"])
        tgt_idx = id_to_idx.get(edge["target"])
        if src_idx is None or tgt_idx is None:
            continue
        if src_idx == tgt_idx:
            self_loop_nodes.add(src_idx)
        else:
            adj[src_idx].append(tgt_idx)
        key = (src_idx, tgt_idx)
        if key not in edge_kinds:
            edge_kinds[key] = set()
        edge_kinds[key].add(edge["kind"])

    # Iterative Tarjan SCC
    index_counter = [0]
    indices: list[int | None] = [None] * n
    lowlinks: list[int] = [0] * n
    on_stack: list[bool] = [False] * n
    stack: list[int] = []
    sccs: list[list[int]] = []

    # Iterative DFS using an explicit call stack
    # Each entry: (node, iterator_over_neighbours, index_assigned)
    call_stack: list[tuple[int, int]]  # (node, neighbour_pos)

    for start in range(n):
        if indices[start] is not None:
            continue

        # Push (node, neighbour_position)
        call_stack = [(start, 0)]
        indices[start] = lowlinks[start] = index_counter[0]
        index_counter[0] += 1
        stack.append(start)
        on_stack[start] = True

        while call_stack:
            v, pos = call_stack[-1]

            if pos < len(adj[v]):
                # Advance to next neighbour
                call_stack[-1] = (v, pos + 1)
                w = adj[v][pos]
                if indices[w] is None:
                    # Tree edge — recurse
                    indices[w] = lowlinks[w] = index_counter[0]
                    index_counter[0] += 1
                    stack.append(w)
                    on_stack[w] = True
                    call_stack.append((w, 0))
                elif on_stack[w]:
                    # Back edge — indices[w] is set because w was visited before v
                    w_index = indices[w]
                    assert w_index is not None
                    lowlinks[v] = min(lowlinks[v], w_index)
            else:
                # All neighbours processed — pop
                call_stack.pop()
                if call_stack:
                    parent = call_stack[-1][0]
                    lowlinks[parent] = min(lowlinks[parent], lowlinks[v])

                # Root of SCC?
                if lowlinks[v] == indices[v]:
                    scc: list[int] = []
                    while True:
                        w = stack.pop()
                        on_stack[w] = False
                        scc.append(w)
                        if w == v:
                            break
                    sccs.append(scc)

    # Convert to output format
    results: list[dict[str, Any]] = []

    for scc in sccs:
        scc_set = set(scc)
        size = len(scc)
        is_self_loop = size == 1 and scc[0] in self_loop_nodes

        if size <= 1 and not is_self_loop:
            continue

        scc_node_ids = [node_ids[i] for i in scc]
        scc_node_ids_sorted = sorted(scc_node_ids)

        # Collect edge kinds for all edges within this SCC
        kinds_in_cycle: set[str] = set()
        for src_idx in scc:
            for tgt_idx in adj[src_idx]:
                if tgt_idx in scc_set:
                    kinds_in_cycle.update(edge_kinds.get((src_idx, tgt_idx), set()))
        # Include self-loop edge kinds
        if is_self_loop:
            node_idx = scc[0]
            kinds_in_cycle.update(edge_kinds.get((node_idx, node_idx), set()))

        results.append(
            {
                "id": "|".join(scc_node_ids_sorted),
                "nodes": scc_node_ids_sorted,
                "size": size,
                "edge_kinds": sorted(kinds_in_cycle),
            }
        )

    # Sort descending by size, then by id for stable output
    results.sort(key=lambda x: (-x["size"], x["id"]))
    return results[:_MAX_CYCLE_ENTRIES]
