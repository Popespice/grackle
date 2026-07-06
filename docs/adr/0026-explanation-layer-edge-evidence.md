# ADR-0026 — Explanation Layer: Edge Evidence (and the Causal Path)

**Status:** Accepted (implemented in Phase 10.4, 2026-07-05; extended in Phase 10.5, 2026-07-08)
**Date:** 2026-07-05
**Phase:** 10.4, 10.5

---

## Context

grackle's north star is a view that **explains how files are hooked up, why, and why
they fire** (see the north-star vision and `~/.claude/plans/plan-out-phase-10-mighty-lovelace.md`).
Phase 10 splits that into two halves. Chunks 10.1–10.3 shipped the *"why they fire"*
half: sampled value capture (ADR-0025) plus a time-travel inspector that scrubs a run
and shows a function's args, return, and live call stack.

This ADR governs the *"why they connect"* half — the **explanation layer** — as one
"show the user why" thesis with two chunks:

- **10.4: edge evidence.** Every graph edge already records *that* B
  imports / calls / inherits / implements A (or reaches it across a language boundary
  via an HTTP route / subprocess spawn). What it lacked was the **justifying source
  line**: the exact `from a import foo` (a.py:3) that makes the connection real and
  clickable. 10.4 records that line and builds the UI to surface it.
- **10.5 (this extension): the causal "why did this fire" path.** From a selected
  node's firing, render the ancestor call-path chain annotated with the values
  (ADR-0025) that drove each hop. This extension amends this document's Decision and
  Future-work sections rather than opening a new ADR — it is the same thesis, one
  design (§8).

Unlike 10.2 (which deliberately ended the no-wire-schema-change streak with a typed
`values` field), 10.4 is a **no-wire-schema-change chunk**: the evidence rides on the
edge's already-open `metadata` bag.

## Decision

### 1. Edge evidence rides on open `metadata.line` — no wire-schema change

`GraphEdge.metadata` is an open `additionalProperties: {}` object on both
`packages/shared-types/schema/graph.schema.json` (`GraphEdge`) and the Python
`GraphEdge` TypedDict (`adapters/base.py`), per the open-strings/open-surfaces posture
of ADR-0004. Edges gain a single optional integer `metadata.line` — the 1-based source
line of the justifying construct. **No schema field is added, no codegen changes, and
`pnpm check-parity` stays a no-op** (it diffs generated-artifact bytes and message-
`type` discriminator consts, neither of which an open-metadata key touches).

This is the *opposite* choice from 10.2's typed `values` field, and deliberately so.
`values` is a rich, multi-field payload whose `args`/`ret` shapes benefit from a
structural guarantee across the TS/Python canonical types; `metadata.line` is a lone
integer on a bag that already carries per-kind keys (`relative`, `type_checking`,
`alias`, `resolved`, …). A typed wire field would have re-opened the three-way
hand-sync burden (schema ↔ `messages.ts` ↔ TypedDict) for no structural benefit.

### 2. Line is captured at emission, in the source node's file

The AST/tree-sitter position is in scope at every static edge-emission site, so the
line is recorded where the edge is built:

- **Python** (`python_parser/visitors.py`): `node.lineno` for import / call /
  inherit edges (already 1-based).
- **TS / Go / Rust** (`{typescript,go,rust}_parser/visitors.py`): `node.start_point[0]
  + 1` (tree-sitter rows are 0-based). Call edges route through a `_CallCollector` that
  previously discarded position — it now carries `(name, line)` pairs. The
  `_emit_inherit` / `_emit_implements` name-string helpers gained a threaded `line`
  parameter from their positioned caller node.

The edge carries **no path** — the edge's `source` node ID already encodes the
POSIX-relative file, and the evidence line lives in that source file. No new
path-bearing field means no POSIX-discipline surface (ADR-0001) on the edge.

### 3. Line-only on the wire; the frontend derives the snippet

The adapters emit only the integer line, **not** a source snippet. The frontend
already fetches file source (Shiki source viewer, `useSource`), so the
EdgeEvidencePanel slices the evidence line out of the source it loads to render
"`from a import foo`". This keeps the parser change minimal and mechanical, adds no
source-text reading to the parse hot path, and avoids snippet-string bloat / escaping
/ truncation in the graph JSON.

### 4. Evidence survives resolution

The per-language `resolve_graph` passes rebuild inherit/call edges when they resolve an
unresolved target to a concrete node. The pre-existing resolved branch **replaced** the
edge metadata (`{}` in TS/Go/Rust; `resolution.metadata` in Python), which would have
dropped `line` for every successfully-resolved edge. Each resolved branch now carries
the original evidence forward while **dropping only the `resolved` marker** (so the
now-resolved edge reflects its state and stays idempotent under a re-resolve). The
unresolved branches already merged the original metadata and are unchanged.

### 5. Cross-language edge evidence is static and degrades gracefully

Cross-language hints are produced at **static parse time** by regex over source text
(`{python,typescript,go,rust}_parser/hints.py`, `extract_hints`), *not* by the runtime
adapters. Each hint's `payload` now carries the 1-based line derived from the regex
match offset (`source.count("\n", 0, m.start()) + 1`), and `cross_language.py` threads
that line onto the `cross_language_call` / `cross_language_spawn` edge's metadata **only
when present**. An absent line (see Known limitations) degrades cleanly: the frontend
omits the snippet and disables the jump for that row rather than erroring.

### 6. Frontend: two triggers, one precise jump

- **Pick an edge** (a new Sigma `clickEdge` handler) → an `EdgeEvidencePanel`
  (right-sidebar, ErrorBoundary-wrapped) shows that edge's evidence. Because the
  graphology instance is a `MultiDirectedGraph`, parallel edges (two calls to the same
  target on different lines) are preserved; `line` is carried onto the graphology edge
  attribute so the clicked edge's exact line is read back on click.
- **Select a node** → the panel lists the node's incoming + outgoing edges, each with
  its evidence line (and, for out-edges that share the node's file, an inline snippet).
- **Precise jump.** A new store action `jumpToSourceLine(path, line)` sets a
  `sourceViewerTarget` that the SourceViewer prefers over its node-derived path/line.
  This is necessary because a call edge's evidence line is deep in a function body — not
  the source node's definition line — and an incoming edge's evidence line is in a
  *different* file entirely. **10.5 reuses this action** for causal-path hop navigation.

### 7. The causal path (10.5): a selection-driven complement to the time-travel inspector

10.3's `ValueInspectorPanel` is **playhead-driven**: scrub to a moment and see the call
stack open at that instant. To ask "why did *this node* fire?" you first had to already
know when it fired. 10.5 adds a **selection-driven** `CausalPathPanel` (right-sidebar,
order 27, adjacent to `EdgeEvidencePanel`): pick a node, pick which firing (when it fired
more than once), and read the ancestor chain **root → … → THIS** with the argument values
that drove each hop — fusing 10.2's captured values, 10.3's stack reconstruction
(`ancestorStackAt`), and 10.4's edge evidence.

**The causal path is already computable from shipped infrastructure.** A new pure module,
`graph/causalPath.ts`, adds only what `ancestorStackAt` doesn't provide:

- `firingsOf(events, nodeId)` enumerates every `call` event for a node, capped at 200
  enumerated firings (`MAX_FIRINGS`) so a hot helper called thousands of times doesn't
  materialize thousands of stepper states — the scan stops as soon as the cap is hit
  (cheaper than a full-prefix scan), and the panel notes when the list is capped.
- `nearestFiring(firings, playhead)` picks a sensible default invocation (the latest
  *collected* firing at or before the current playhead, falling back to the earliest if
  the playhead precedes all of them) — but **only as the initial default**. When the list
  is capped and the playhead sits at or past the last collected firing, a truer "nearest"
  firing may exist beyond the cap; `CausalPathPanel` surfaces this explicitly rather than
  silently presenting the last collected firing as if it were confirmed nearest. Once shown, the firing
  index is a user-controlled stepper immune to playhead moves triggered by a hop's own
  "time-travel" action, so clicking that action never silently swaps which firing the
  panel displays.
- `causalPathAt(events, callIndex, threadId)` is a thin, drift-guarded wrapper:
  `ancestorStackAt(events, callIndex).byThread.get(threadId)` **is** the causal path for
  that firing — at the instant a `call` event opens, the open stack on its thread is
  exactly `[root, …, THIS]`, each frame already carrying its opening call's captured
  `values.args`. A cross-check test asserts the wrapper never diverges from calling
  `ancestorStackAt` directly, the same drift-guard discipline `ancestorStackAt` itself
  uses against `callTree.ts`.

**Firings are disambiguated by `ts_ns` and `callIndex`, never by argument values** — a
run captured without `--capture-values`, or one where every invocation happens to share
identical arguments, would otherwise render every firing indistinguishable.

**Truncation posture: a completeness banner, not a hard gate — the one place 10.5
deliberately diverges from 10.3.** `ValueInspectorPanel` hard-gates past `useFullTrace`'s
50k-event paging cap because its *playhead* can point past the paged prefix, yielding a
plausible-but-wrong stack. 10.5 never creates that situation: it only ever enumerates
firings *from* the already-paged prefix and reconstructs `causalPathAt(events, callIndex)`
for a `callIndex` that is, by construction, within that prefix. Because the prefix is a
**true prefix rooted at absolute index 0**, replaying `[0, callIndex]` is byte-identical
whether or not events exist past the 50k cliff — truncation only removes events *after*
`callIndex`, which never participate in that replay. **Every causal path 10.5 renders is
therefore correct; truncation only limits which firings can be *enumerated*, never the
correctness of a path for one already found.** `CausalPathPanel` shows all enumerable
firings plus a "N firings within the first 50k events — later firings not shown" banner
when the prefix is truncated, rather than an unavailable state. This correctness rests
on one precondition, load-bearing enough to be commented directly in `causalPath.ts`:
the events array passed to `causalPathAt` must never be a windowed slice with a non-zero
start — that would resurrect `ancestorStackAt`'s orphan-return case (a return with no
matching open frame, because its call opened before the window) and silently produce a
wrong path.

**Per-hop navigation fuses all three shipped mechanisms**, one action per button so none
of them compose in a way that could silently clobber another:

- **`→ time-travel`** — `setPlayhead(hop.callIndex)` alone, moving the global scrubber so
  `ValueInspectorPanel` reflects that hop's args/return, without touching node selection
  or the source viewer.
- **`select`** — graph-guarded `selectNode(hop.nodeId)` (the `ValueInspectorPanel`
  `onSelectFrame` precedent: skip nodes absent from the static graph, since selecting an
  unresolved/imported id would dim the whole Sigma view), re-rooting the panel to "climb"
  the chain.
- **`↳ call site`** — looks up the **parent→hop** edge in the static graph and, when it
  carries a 10.4 `metadata.line`, calls `jumpToSourceLine(parentPath, line)`. The root hop
  has no parent and so no call-site button. Because a `MultiDirectedGraph` can carry
  parallel parent→hop edges on different lines, the first line-bearing match is used —
  a documented approximation, not necessarily *this* firing's exact call site (see Known
  limitations). Each action is a single store dispatch; none combine `selectNode` (which
  clears `sourceViewerTarget`) with `jumpToSourceLine` (which sets it) in one click, so
  there is no risk of one silently undoing the other.

**Shared state extracted, not mirrored.** The seekable-prefix load state machine
(loading / error+retry / "Load call stack" / the bounded `captureSeen` scan) is
*behavioral*, not stylistic, and is identical across `ValueInspectorPanel` and
`CausalPathPanel` except for the truncation branch (hard gate vs. completeness banner).
A new shared hook, `graph/useSeekablePrefixState.ts`, is consumed by both — extracted
rather than mirrored because a silent divergence here (e.g. a forgotten guard) would be a
real correctness bug, unlike the purely cosmetic styling constants the two panels still
deliberately mirror (`PANEL_STYLE`, `Badge`, `argsPreview`, and similar), consistent with
the codebase's existing "mirror simple styling, extract genuinely-shared behavior"
judgment (the same one that keeps `ancestorStack.ts` a deliberate mirror of `callTree.ts`
rather than a shared implementation).

### 8. Future work

- **A server-side `trace_ancestors_at` query** (noted in ADR-0025 §6) — a runtime
  causal-path query that would add an 18th message type; deferred, client-side prefix
  reconstruction remains the MVP for both the time-travel inspector (10.3) and the
  causal path (10.5).
- **Backend-emitted snippet / column capture** — rejected below; revisitable if a
  consumer needs evidence without loading source, or sub-line precision.
- **Cross-language line-threading from *runtime* hints** — the static regex hints cover
  the common frameworks; a runtime path (observing an actual HTTP call / spawn and
  capturing its frame line) could complement them later.

## Alternatives rejected

- **A typed wire field for the line** (mirroring 10.2's `values`): re-opens the
  three-way schema ↔ `messages.ts` ↔ TypedDict hand-sync for a single integer with no
  structural-consistency benefit. The open `metadata` bag is exactly the surface
  ADR-0004 reserves for per-kind, per-adapter keys like this. Rejected.
- **Backend-emitted snippet string**: bloats every edge in the graph JSON, forces
  source-text reading into the parse hot path, and introduces escaping/truncation
  concerns — all to duplicate text the frontend already has loaded. Rejected in favor
  of a frontend-derived snippet from `metadata.line`.
- **Capturing a column (`col`) alongside the line**: nothing in the product consumes
  columns and the source-viewer jump is line-granular (whole-line refs, `scrollIntoView`
  on a line element). Rejected as unused precision.
- **A cache-format-version bump to auto-invalidate stale sidecars**: correct but out of
  scope for 10.4; the staleness degrades gracefully (see Known limitations) and a
  document-only note is the chosen posture. Recorded as a follow-up option.

## Constraints honored

- **No wire-schema change / `check-parity` no-op** — evidence is an open-metadata key
  (ADR-0004), not a schema field. 10.5 is frontend-only and touches no schema either;
  `check-parity` stays a no-op across both chunks under this ADR.
- **POSIX path discipline (ADR-0001)** — the edge carries no path; the line is an
  integer, the file comes from the already-POSIX source node ID.
- **Open strings, not enums (ADR-0004)** — `metadata.line` extends an open bag; no
  registry or `KNOWN_*` change.
- **Graceful degradation** — line-less edges (stale cache, Go method-set synthesis)
  render without a snippet and disable their jump rather than erroring; 10.5's causal
  path degrades the same way (no call-site button when the parent→hop edge carries no
  line) and additionally never hard-gates on trace truncation (§7).
- `mypy --strict` on all new Python code; Biome + `tsc -b` clean on the frontend.
- Bind only to `127.0.0.1` — N/A (no networking changes).

## Known limitations

- **A warm parse cache serves line-less edges until re-parse.** All four parser
  adapters use `CacheManager`, which returns a file's cached partial graph verbatim on a
  content-hash hit. After 10.4 ships, edges for files whose content is unchanged keep
  their pre-10.4 (line-less) metadata until the file changes (its hash flips) or the
  cache is cleared. This does not crash — the frontend degrades — but line evidence is
  silently absent for those files. **Clear `.grackle/cache/` to backfill evidence
  across an unchanged tree.** A cache-format-version bump (auto-invalidate old sidecars)
  is a documented follow-up option; 10.4 keeps scope to document-only.
- **Go method-set-satisfaction edges have no line.** `_detect_implements` synthesizes
  Go `implements` edges from whole-type method-set analysis, with no single justifying
  source line; these edges carry no `metadata.line` and degrade cleanly, consistent with
  the cross-language posture.
- **Tree-sitter inherit/implements use a per-declaration line approximation** where the
  positioned per-name node isn't available (e.g. Rust supertraits, extracted as bare
  name strings): the evidence line is the enclosing declaration line rather than the
  exact supertrait token. Good enough for a line-granular jump.
- **Frontend snippet is single-file per selection.** For a selected node, inline
  snippets are shown only for out-edges (which share the node's file); incoming edges
  show `path:line` and rely on the jump to reveal the code in the other file. Fetching
  every referenced file's source at once is out of scope.
- **(10.5) The causal path's "call site" jump can pick the wrong line among parallel
  edges.** When a parent calls the same hop from two different lines (a
  `MultiDirectedGraph` can carry both), `CausalPathPanel` picks the first line-bearing
  parent→hop **`call`/`cross_language_call`** edge rather than the one that actually
  produced *this specific* firing — the static graph edge doesn't record which
  invocation it corresponds to. (Only invocation-kind edges are considered; an
  import/inherit/implements edge sharing the same source/target pair is never mistaken
  for a call site.) The jump is still a correct, in-file call site for that parent→hop
  relationship; it just isn't guaranteed to be the exact call expression this firing
  came from. A per-firing exact call site would require correlating trace timing with
  source position, out of scope here.
- **(10.5) Firing enumeration is capped at 200 (`MAX_FIRINGS`) per node.** A node called
  thousands of times shows only its first 200 firings, with a "showing the first 200"
  note; `firingsOf` stops scanning as soon as the cap is hit, so the excess firings are
  never seen at all (cheaper than a full-prefix scan). When the playhead sits at or past
  the last collected firing while capped, `CausalPathPanel` shows an explicit warning
  that a truer "nearest" firing may exist beyond the cap, rather than silently presenting
  the last collected firing as a confirmed-nearest default.
