# ADR-0011: Cycle Detection — Tarjan's SCC, All-Edge-Kinds Default, Frontend Implementation

**Status:** Accepted  
**Date:** 2026-05-18  
**Deciders:** solo project  
**Cross-refs:** ADR-0008 (Analysis registry, deferred migration path), ADR-0004 (open-string extension surface)

---

## Context

Phase 5.2 adds the fifth `Analysis<T>` to the `AnalysisRegistry` (ADR-0008). Cycle detection is the first analysis that returns a *list of subgraphs* rather than a ranked entry list, and the first to drive interactive renderer highlighting (node ring colouring on click).

Two architectural questions needed settling before implementation:

1. Which SCC algorithm?
2. Which edge kinds should participate?

---

## Decision 1: Tarjan's SCC algorithm

**Chosen:** Tarjan's iterative SCC.

**Alternatives considered:**

| Algorithm | DFS passes | Space | Notes |
|---|---|---|---|
| Tarjan (iterative) | 1 | O(V) stack | Chosen |
| Kosaraju | 2 | O(V+E) for transpose | Two passes, needs transpose graph |
| Johnson's | V+1 | O(V+E) per call | Enumerates all *simple* cycles, exponential output |

Tarjan is preferred over Kosaraju because it is a single-DFS-pass, O(V+E), and doesn't require building a transpose graph. Johnson's algorithm is ruled out: it enumerates simple cycles (exponential in the number of back-edges), while we want SCCs — the structural units of mutual reachability.

The implementation is iterative (explicit call-stack) rather than recursive to avoid JavaScript's call-stack limit on graphs with long chains (e.g. stress-2k has chains of ~300 nodes). The iterative form is O(V+E) in both time and space, identical to the recursive form.

**Cycle identity:** the `id` field is the sorted member node IDs joined with `"|"`. This is deterministic regardless of SCC traversal order, requires no crypto primitives, and is human-readable in debug output.

---

## Decision 2: All-edge-kinds default

**Chosen:** detect cycles across all edge kinds (import, call, inherit, implements, and future kinds).

**Rationale:** The most common cause of unexpected runtime behaviour is a cycle between *any* two nodes — whether `call`→`call` mutual recursion or `import`→`import` circular dependency. Filtering to a single edge kind would silently miss mixed-kind cycles (e.g. a file that imports a class that calls back into the file).

**Known trade-off:** real codebases have many benign `inherit`/`implements` chains (e.g. a trait hierarchy) that form legitimate cycles. The `size` and `edge_kinds` fields let users interpret cycles in context. The renderer's existing legend toggles already control edge *visibility*, which provides a complementary filtering surface without requiring the analysis to duplicate that logic.

If the cycle count for a given codebase is too noisy, a future option (`cycleDetection({ edgeKinds })`) can add filtering at the analysis call site — the `Analysis<T>` interface (ADR-0008) supports parameterisation via a new registration.

---

## Decision 3: Frontend implementation

**Chosen:** implement in the frontend (`cycleDetection.ts`), following ADR-0008's deferred-migration path.

The analysis runs on the already-loaded graph object. For the current target scale (stress-2k: ~2 000 nodes, ~6 000 edges) Tarjan is O(V+E) ≈ 8 000 operations — well within a 16 ms frame budget.

ADR-0008 explicitly reserved the migration path to agent-side computation if frontend profiling shows >100 ms on a large graph. That path remains open: move `cycleDetection` into `packages/agent/src/grackle/analyses/` and emit `CycleEntry[]` alongside the graph payload. No protocol change is needed because `StaticGraphMessage.payload` extends `Graph` which already allows `metadata` extensions.

---

## Consequences

- `AnalysisRegistry` now holds 5 analyses; `index.test.ts` asserts this.
- `useGraphStore` gains `highlightedNodeIds: Set<string> | null` + `setHighlightedNodes` — a new interaction primitive reusable by future analyses or panels.
- `GraphCanvas.makeNodeReducer` reads `highlightedNodeIds`; highlighted nodes render with `--color-highlight-cycle` (oklch warm amber); non-highlighted nodes dim identically to the existing selection dimming.
- `CyclesPanel` is registered in `right-sidebar` at `order: 30` (below the graph legend). It returns `null` when no cycles are present, so it does not occupy space on acyclic graphs.
- `StatsPanel` shows "Cycles: N" — a one-glance indicator that prompts the user to open the Cycles panel when N > 0.
