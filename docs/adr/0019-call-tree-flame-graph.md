# ADR-0019 — Call-tree reconstruction & flame graph (Phase 8.2)

**Status:** Accepted  
**Date:** 2026-05-28  
**Deciders:** Connor Allen

> ADR-0018 (server-side analysis & aggregation engine) is **reserved** for a
> later Phase 8 chunk and not yet written; Phase 8.2 shipped before it, so the
> ADR numbers are intentionally non-contiguous (the plan maps 8.2 → 0019).

---

## Context

Phases 6–7 made grackle *watch* code: the runtime overlay renders a heat map and
a coverage overlay from the `TraceEvent` stream, and the timeline scrubs it.
But the stream already carries `frame_depth` and `thread_id` — enough to
reconstruct the **call tree** — and nothing used them. A flame graph answers a
question the heat map cannot: *where did time actually go, and what is the hot
path?*

The event stream is produced by the `sys.monitoring` tracer (ADR-0013). Its
real vocabulary is narrower than a textbook profiler's, and that shapes the
whole design:

- Emitted `event` kinds are exactly **`call`**, **`return`**, **`exception`**,
  and (only with `--lines`) **`line`**.
- There is **no `unwind` event**: `PY_UNWIND` fires when a frame exits via a
  propagating exception, but the tracer uses it purely for depth bookkeeping and
  emits nothing. A frame that raises therefore has a `call` with **no matching
  `return`**.
- There is **no `yield`/`resume` event**: `PY_YIELD`/`PY_RESUME` are not
  subscribed, so generators emit only their initial `call` and final `return`,
  and `frame_depth` can drift by one inside a generator (a documented ADR-0013
  limitation).
- `call` and the matching `return` report the **same** `frame_depth` (the
  frame's own depth): `call` captures the counter before incrementing, `return`
  after decrementing.
- `--max-events` truncates the stream *mid-frame*, leaving dangling opens.
- Events from all threads interleave in one flat stream; depth counters are
  per-thread.

A naive `call`↔`return` pairing breaks on every one of these. The algorithm has
to be depth-driven and self-correcting.

There is also a transport constraint. In **buffered** (live / streaming)
sessions the browser holds the whole event array. In **seekable** (file-replay)
sessions, after ADR-0017, the browser holds only a ~200-event window and fetches
more on demand. A whole-run flame graph needs the whole run.

---

## Decision

### 1. Pure client-side reconstruction — no new wire message

`graph/callTree.ts` reconstructs the forest entirely in the browser from
already-received `TraceEvent`s, mirroring `heatmap.ts` / `runtimeCoverage.ts`.
`KNOWN_MESSAGE_TYPES` stays at 12; no schema/codegen change. For seekable
sessions the panel pages the full trace over the **existing** `trace_seek_request`
channel (`fetchFullTrace`, capped at 50 000 events) rather than inventing a
message. Genuine server-side aggregates (cumulative heat/coverage over seeks,
top-K) are the job of the reserved aggregation engine (ADR-0018), not this chunk.

### 2. Depth-driven reconstruction with implicit-close recovery

Per `thread_id`, maintain a stack:

- **`call` at depth `d`** — first implicitly close every still-open frame at
  depth `>= d` (they unwound silently via exception), then push the new frame.
- **`return` at depth `d`** — implicitly close any deeper frames (depth `> d`),
  then close the depth-`d` frame normally. A `return` with no open frame at `d`
  (a frame opened *before* a seekable window) is counted as an **orphan return**
  and skipped.
- **`exception`** — annotate the matching open frame (`raised = true`), located
  by `node_id` on the stack (RAISE fires for every frame an exception is raised
  in or propagates through, while the deeper frames are still open), falling
  back to the top; it is an observation and does **not** pop.
- **`line`** and unknown kinds — non-structural, ignored (ADR-0004).
- **Stream end** — close everything still open (truncation, top-level uncaught
  exception, or a window ending mid-stack).

Implicit closes are stamped with the *triggering* event's `ts_ns`, an upper
bound that keeps the tree well-formed (a parent never ends before its children;
`selfNs = totalNs − Σ children` stays `>= 0`). This slightly over-attributes
wall-time to exception-unwound frames; that plus generator depth drift are the
only timing approximations, surfaced to the UI as `hadSynthetic`. **Display
depth comes from tree position, not `frame_depth`**, so a windowed forest whose
roots carry a non-zero `frame_depth` still renders from row 0.

### 3. Two views from one reconstruction

`buildCallTree` returns the **raw** time-ordered forest (one frame per
invocation). `aggregateCallTree` merges sibling frames sharing a `node_id`
(summing `count`/`totalNs`/`selfNs`, recursion preserved, children sorted
left-heavy) for the classic flame shape. The panel renders the aggregated tree;
the interchange exporters serialize the **raw** tree (it has per-frame
start/end). `hotPath` walks the heaviest child chain for highlighting.

### 4. Canvas with geometry extracted to pure functions

`FlameGraphPanel.tsx` is a thin `<canvas>` shell (DPR-aware, `ResizeObserver`-
driven). All geometry lives in `flameLayout.ts` — `layoutFlame` (width ∝
`totalNs` icicle layout), `hitTest`, `maxDepth`, `frameColor` — so it is unit-
tested without a canvas (jsdom has no 2D context; the draw effect no-ops on a
null context). Fills use `hsl()` (canvas-safe) rather than the project's `oklch`
tokens, which `parseColor` rejects (ADR-0015). Clicking a frame calls
`selectNode(node_id)` (trace `node_id` ≡ static-graph node id) and clears any
active highlight so the selection is visible. The flame is a **whole-run**
aggregate, deliberately *not* playhead-bound — the heat map already provides the
time-scrubbed lens.

### 5. Interchange: speedscope + Chrome Trace Event Format

`export/speedscope.ts` emits the "evented" format (one profile per thread, frame
name = `node_id`, nanosecond unit, session-relative timestamps).
`export/chromeTrace.ts` emits "complete" (`ph: "X"`) events in microseconds.
Both round-trip back via `parseSpeedscope` / `parseChromeTrace` (the latter also
accepts third-party `B`/`E` events and rebuilds nesting from `X`-event time
containment). Because frame names are node ids, a round-trip preserves node
identity; third-party files import best-effort (frames that aren't grackle node
ids simply won't resolve to graph nodes). Import loads the parsed events into a
buffered session so the existing timeline/heat/flame light up.

---

## Consequences

**Positive:**

- grackle now *answers* "what's the hot path?" — a flame graph and self/total
  timings, plus click-to-focus into the static graph.
- Traces open in speedscope.app / `chrome://tracing`, and external traces open
  in grackle — round-trip verified.
- Zero protocol surface change; pure additive frontend modules, all unit-tested
  (`callTree`, `flameLayout`, exporters, `fetchFullTrace`, panel smoke).
- Robust to the real tracer's quirks: exceptions, generators, truncation, and
  seekable windows all degrade to a well-formed (if approximate) tree rather
  than a corrupt or crashing one.

**Negative / trade-offs:**

- In live (buffered) sessions the whole tree rebuilds on each event batch
  (O(n) per `traceEvents` identity change), the same cost profile as the heat
  map — acceptable with the Phase-7 rAF batching.
- Seekable sessions need an explicit "Load full trace" paged fetch, capped at
  50 000 events (a "first 50k" badge flags truncation). Proper server-side
  aggregation lands with ADR-0018.
- Exception-unwound and generator frame durations are approximate (implicit-
  close timestamping; documented via the `~approx` badge).
- Multiple threads render as side-by-side root stacks rather than swim-lanes.
- The Chrome Trace `X`-event (interval) format is lossy for **zero-duration**
  frames at a shared timestamp: a `[t, t]` frame preceding a sibling at `t` is
  indistinguishable from one nested at `t`, so it re-imports as nested. Only
  possible when `ts_ns` repeats; the speedscope evented format is lossless and
  is preferred for round-trips.

---

## Alternatives considered

| Alternative | Reason rejected |
|---|---|
| Server-side call-tree reconstruction | Belongs with the aggregation engine (ADR-0018); 8.2 stays pure-frontend and ships independently. The seek channel already lets the browser page the full trace. |
| Naive `call`↔`return` pairing | Breaks on exception-unwound frames (no `return`), generators, and truncation. Depth-driven implicit-close recovery is required. |
| SVG / DOM-node flame rendering | A deep trace is thousands of rectangles; canvas keeps redraw cheap and avoids DOM bloat. Geometry is still pure-tested. |
| Playhead-bound flame (events `[0..playhead]`) | The heat map already gives the time-scrubbed view; a flame graph's value is the whole-run aggregate. |
| Add `CallTree`/speedscope types to `shared-types` | `shared-types` is the wire contract (schema-derived). These are frontend-only app/interchange types and live with their modules (like `RuntimeCoverage`). |

---

Cross-references: ADR-0013 (trace event schema — the `call`/`return`/`exception`
vocabulary and generator/`frame_depth` limitation this builds on), ADR-0015
(runtime overlay UI — `oklch`→canvas colour caveat, hook-first panels), ADR-0017
(server-side seek — the channel `fetchFullTrace` reuses), ADR-0004 (open-string
`event` kinds), ADR-0007 (panel/slot system).
