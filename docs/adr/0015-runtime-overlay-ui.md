# ADR-0015 — Runtime overlay UI: Timeline, heat-map, and coverage

**Status:** accepted

**Context:**

Phase 6.2 delivered trace events from the `sys.monitoring` tracer over the 127.0.0.1 WebSocket into a frontend Zustand store slice (`traceEvents`, `traceSessionId`, `traceSessionComplete`). The events arrive but nothing renders them. Phase 6.3 makes them visible: a scrubable **Timeline** panel, a node **heat-map** coloured by call frequency, a **runtime-coverage** summary in StatsPanel, and a fix for a latent Sigma colour-parsing bug introduced in Phase 5.2.

**Critical finding (verified from Sigma 3.0.3 source):**

`sigma/dist/colors-*.esm.js` `parseColor()` has exactly two branches — `#rrggbb` / `#rgb` and `rgb()/rgba()` — and falls through to `{r:0,g:0,b:0,a:1}` (opaque black) for everything else. CSS custom properties are returned verbatim by `getComputedStyle().getPropertyValue(...)`, not resolved to `rgb()`, so `cssVar(el, "--color-highlight-cycle")` yields `"oklch(72% 0.2 40)"`, which is truthy but Sigma-invalid. **Consequence:** every colour passed to Sigma must be `#hex` or `rgb()/rgba()` — never oklch, hsl, or CSS variables.

**Decision:**

### oklch cycle-highlight fix

`--color-highlight-cycle` in `tokens.css` is changed from `oklch(72% 0.2 40)` to its hex equivalent `#e6863c`. A `heatColor.test.ts` regression test asserts that `heatColor()` always returns a `#`-prefixed string, guarding against future regressions.

### Client-side Timeline (no server seek)

The Timeline panel operates entirely on the buffered `traceEvents` array already in the store — the server has no seek endpoint and ADR-0002 selected WebSocket over HTTP precisely to avoid request-response latency. Seeking is therefore a frontend concern: the playhead is an index into the local event buffer.

### Playback store slice

Six new state fields and six new actions are added to `useGraphStore`:

| Field | Purpose |
|---|---|
| `tracePlayhead` | Index into `traceEvents`, 0..N |
| `tracePlaying` | rAF loop active |
| `tracePlaybackSpeed` | Multiplier (1, 2, 4) |
| `traceEventTypeFilter` | `Set<string>` — empty = all kinds pass |
| `traceHeatMode` | `"cumulative"` \| `"sliding"` |
| `traceWindowSize` | Event span for sliding mode |

`startTraceSession` resets `tracePlayhead` and `tracePlaying` but preserves `traceHeatMode` and `traceEventTypeFilter` across re-runs.

`addTraceEvent` uses `[...spread]` which is O(n²). Batching is explicitly deferred to Phase 7 — render cadence is decoupled from appends via the rAF loop, so per-event Sigma refreshes do not occur.

### Heat-map pure function + Sigma hex-only ramp

`computeHeat(events, playhead, filter, mode, windowSize)` returns `{heat: Map<nodeId, count>, maxHeat}`. Cumulative mode slices `[0..playhead]`; sliding mode slices `[playhead-windowSize..playhead]`.

`heatColor(norm)` maps a normalised call count to a **hex colour constant** (7-stop cold-blue→hot-red ramp). All stops are `#rrggbb` literals — never oklch or CSS variables. The regression test `heatColor.test.ts` enforces this invariant.

`useHeatmap()` wraps `computeHeat` in `useMemo` over the five relevant store slices.

### Heat-map wiring in GraphCanvas (effect-2 reuse)

The existing `setSetting("nodeReducer", …) + sigma.refresh()` path (effect 2, deps: filter state) is extended with `heat, maxHeat, heatActive` (= `traceSessionId !== null`). This reuses the same mechanism as cycle-highlighting — no graph rebuild, just a nodeReducer swap + refresh. Color cascade precedence inside `makeNodeReducer`:

```
highlighted → dimmed → heat (if active && maxHeat>0) → resolved kind color
```

Untouched nodes during heat mode receive `COLD_HEX` (`#4a5568`), a desaturated hex constant.

### Runtime coverage hook (not a registry entry)

`runtimeCoverage(graph, events)` computes session-level touched/cold/hot sets. It is **not** registered in `AnalysisRegistry` because the registry caches by graph object reference (`WeakMap<Graph, T>`) and `compute(graph)` accepts only a `Graph` argument — it has no access to mutable `traceEvents`. Registering coverage would permanently cache the empty result at session start. Instead, `useRuntimeCoverage()` wraps `runtimeCoverage` in `useMemo([graph, traceEvents])`.

### rAF playback loop (StrictMode-safe)

`useTracePlayback()` uses `requestAnimationFrame` to advance the playhead while `tracePlaying` is true. Safety properties:

- rAF id stored in a `useRef` — `cancelAnimationFrame` in cleanup always targets the correct frame, even under React StrictMode double-invoke.
- Live state read via `useGraphStore.getState()` inside the rAF callback — no stale closure over playhead.
- Effect dep `[tracePlaying]` only — not restarted on every playhead advance, preventing drift from effect re-scheduling.
- Guard: `if (typeof requestAnimationFrame !== "function") return;` — jsdom has no rAF; tests that exercise the loop `vi.stubGlobal` it in.

### TimelinePanel + bottom-dock slot

A new `"bottom-dock"` slot is added to the `Slot` union in `registry.ts` (typed allowlist per ADR-0004's open-string discipline). `App.tsx` adds a fourth grid row (`gridTemplateRows: "auto 1fr auto auto"`) and a full-width container for `bottom-dock` between the center/sidebars row and the `bottom-status` row. `TimelinePanel` is registered at `{slot:"bottom-dock", id:"timeline-panel", order:0}`.

ADR-0007 compliance: `useTracePlayback()` and all store selectors are called before the `if (traceSessionId === null) return null` early return.

**Performance:**

- Heat recomputes only when `traceEvents`, `tracePlayhead`, `filter`, `mode`, or `windowSize` changes — not on every render.
- Sigma refresh is called once per rAF frame (per playhead advance), not once per incoming event.
- O(n²) `addTraceEvent` append is a known limitation, deferred to Phase 7 (see `useGraphStore.ts` comment). A large replay session (thousands of events) will observe quadratic time in the accumulation phase, but the rAF loop decouples rendering from accumulation.

**Consequences:**

- **Positive:** The Timeline panel renders immediately on `trace_session_start`; nodes heat-map by call frequency as the playhead advances.
- **Positive:** All Sigma node colours are now hex/rgb (never oklch). The cycle-highlight black bug is fixed.
- **Positive:** Runtime coverage (touched/cold/hot) surfaces in StatsPanel with zero overhead — it is computed lazily by `useMemo`.
- **Limitation:** Timeline is client-only; no server-side seek. Very long traces must be fully buffered in the browser before playback. Server seek is a Phase 7 follow-up.
- **Limitation:** `addTraceEvent` append is O(n²). Batching/ring-buffer is a Phase 7 follow-up.
- **Follow-up:** Phase 7 — batched `addTraceEvents(batch)` action; server-side trace seek; real-time event streaming (relax ADR-0013 no-async-in-hot-path).

**Cross-references:** ADR-0002 (WebSocket choice — no HTTP seek), ADR-0007 (hooks-before-return), ADR-0008 (analysis registry — why coverage is excluded), ADR-0013 (tracer hot-path), ADR-0014 (trace transport).
