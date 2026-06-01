# ADR-0022 — Polyglot Runtime Overlay via the V8 Inspector

**Status:** Accepted (implemented in Phase 8.5, 2026-05-31)
**Date:** 2026-05-31
**Phase:** 8.5

---

## Context

The static graph is polyglot (Python, TypeScript, Go, Rust — ADRs 0006/0009/0010),
but the **runtime overlay is Python-only**: `RuntimeAdapter` is a `@runtime_checkable`
Protocol (ADR-0003) and only `python_runtime` implements it. Phase 8.5 closes that gap
for **TypeScript/Node**, the highest-value second runtime, by driving Node through the
**V8 Inspector Protocol (Chrome DevTools Protocol, "CDP")** over a `127.0.0.1` socket and
emitting the **same `TraceEvent` schema** (`{event, node_id, ts_ns, thread_id, frame_depth,
metadata?}`, ADR-0013), resolved to the Phase-4 TypeScript adapter's node IDs.

Everything downstream is reused unchanged: the WebSocket transport (ADR-0014), the
real-time stream sender (ADR-0016), server-side seek + aggregation (ADRs 0017/0018), and
the Timeline / heat-map / flame-graph frontend (ADRs 0015/0019). The Node adapter only has
to **produce `TraceEvent`s**; the rest of the pipeline does not know or care which language
produced them.

Three hard problems drive the design:

1. **How does V8 surface execution?** CDP offers a CPU **sampling** profiler, **precise
   coverage** call-counts, a **debugger** (breakpoints/stepping), and a **NodeTracing**
   trace-event stream. They differ wildly in fidelity, overhead, and whether they are
   real-time or post-run.
2. **Node-ID resolution.** V8 reports positions of the file it *actually executes*. The
   static graph indexes `.ts` source. A naïve `node app.js` (compiled) reports `.js`
   positions that do not match any `.ts` node.
3. **Capability gating.** Node may be absent, too old, or unable to inspect. The adapter
   must degrade with a clear message and never crash — mirroring the Python 3.12 gate.

## Decision

### 1. Mechanism — a two-channel **hybrid**

No single CDP mechanism gives both real-time heat **and** a faithful nested flame at
acceptable overhead. The two that matter measure *different things*, so they are kept in
*different delivery channels* rather than summed into one event buffer:

| Channel | CDP mechanism | Produces | Delivery |
|---|---|---|---|
| **Live** | `Profiler.startPreciseCoverage({callCount, detailed})`, polled ~250 ms via `takePreciseCoverage` | **exact** per-function call counts → coarse `call` events (`frame_depth: 0`, `metadata.live: true`) | `trace_streaming(sink)` → `--stream` (mid-execution heat + Timeline) |
| **Faithful** | CPU **sampling** profiler (`Profiler.start` → `Profiler.stop`) | sampled call tree → full `call`/`return` stream with real `frame_depth` + `ts_ns` from `timeDeltas` | `trace()` → `--connect` replay or `-o` file (faithful flame + heat) |

This maps onto the **two transport modes that already exist** (live stream vs. completed-trace
replay), so it needs **no frontend change and no double-counting**:

```
grackle trace app.ts --connect … --stream   → live exact heat (coverage poll)
grackle trace app.ts --connect …             → faithful flame replay (sampling)
grackle trace app.ts -o flame.jsonl          → faithful flame to file → reload as session
```

**Why sampling for the faithful path:** a sampled call tree *is* a flame graph — it maps
directly onto the Phase-8.2 flame infra; its callFrames carry `url` + `lineNumber`
(function-start) + `functionName`, exactly what node resolution needs; and it is the same
data `node --cpu-prof` produces, so the reconstruction logic is **unit-testable from a
captured `.cpuprofile` fixture with no Node in the loop**.

**Why precise coverage for the live path:** `takePreciseCoverage` returns *exact* call
counts (not sampled), so polling it yields a true, cheap, real-time heat signal.

Rejected: the **Debugger** domain (per-function breakpoints) — faithful but catastrophic
overhead; **NodeTracing** — real-time but coarse and version-dependent category→position
mapping. Both are noted as non-viable here.

**Deferred (fast-follow):** merging both channels into a *single* `--stream` session
(live coarse heat during the run, then an authoritative "replace with the faithful stream"
at `session_end`) requires a frontend replace-on-session-end step. Out of scope for 8.5.

### 2. Node-ID resolution — type-stripping is the unlock

V8 reports positions of the executed file. **Type-stripping** (Node ≥ 22.6
`--experimental-strip-types`; default ≥ 23.6) replaces TypeScript type annotations with
**whitespace** — so **line numbers are preserved** and the script URL stays `app.ts`. A
profiler callFrame `{url:"file://…/src/app.ts", lineNumber:11 (0-based), functionName:"handle"}`
then resolves directly:

1. `url` → strip `file://` → `Path` → `grackle.paths.to_posix(p, root)` → `src/app.ts`
2. `(src/app.ts, lineNumber + 1)` → `_sym_index` → `src/app.ts:handle`
   (V8 0-based line + 1 == tree-sitter 1-based declaration line)
3. Fallbacks: `functionName`-within-file → file node → `<unresolved>`

A new TypeScript **`NodeResolver`** mirrors the Python one (`python_runtime/node_resolution.py`):
build `(posix_path, line) → node_id` for function/method nodes and `posix_path → node_id`
for file nodes, from the TS static graph. **Pseudo-frames** V8 emits — `(root)`, `(program)`,
`(idle)`, `(garbage collector)`, and `node:internal/*` / empty URLs — are **filtered**, not
surfaced as `<unresolved>` noise.

For the **live coverage** channel, ranges are byte **offsets**, not lines, so the resolver
also builds a per-script **offset→line table** to reach the same `(path, line) → node_id`
index. The source for that table is read **from disk** (the `.ts` file, raw bytes,
BOM-stripped, newlines preserved) rather than via `Debugger.getScriptSource`: enabling the
`Debugger` domain causes V8 to close the inspector when the script finishes (it deopts the
"keep alive until detached" behaviour), and type-stripping preserves line boundaries so the
on-disk lines match V8's positions exactly. Note that the offset→line table only *refines*
resolution — the `functionName` name-fallback resolves most coverage frames on its own.

### 3. Process lifecycle

An open inspector WebSocket keeps Node alive, so the process will not exit while attached —
giving us a window to stop the profiler, but requiring an explicit "user script finished"
signal:

1. Spawn `node --inspect-brk=127.0.0.1:0 [--experimental-strip-types] bootstrap.mjs <abs script>`
   via `asyncio.create_subprocess_exec` (spawn-compatible, cross-OS). `--inspect-brk`
   guarantees we attach before any user code runs (no lost early frames). Port `0` → OS
   picks a free port; parse the `Debugger listening on ws://127.0.0.1:<port>/<uuid>` line
   from **stderr** for the CDP URL.
2. A shipped **`bootstrap.mjs`** does `await import(process.argv[2])` in a `try/finally`,
   then emits a sentinel (`console.error("\x00GRACKLE_DONE")`). We observe it via
   `Runtime.consoleAPICalled`, then `Profiler.stop` → collect → close CDP → `await proc.wait()`.
3. Robustness: timeout guard; if the script throws, the `finally` still signals and we
   capture the error text for an `exception` event; if user code calls `process.exit()`
   before the sentinel, detect process death and stop with whatever profile we have.

A **minimal CDP client** (`cdp_client.py`, ~120 LOC) is written over the existing
`websockets` dependency (send `{id, method, params}`, await the matching `{id, result}`,
dispatch `{method, params}` notifications). **No new dependency.**

### 4. Capability gate

`NodeRuntimeAdapter` registers **unconditionally** (so it is discoverable via
`grackle languages` / the registry), but `capabilities().runtime_tracing` is `True` only
when `shutil.which("node")` resolves **and** `node --version` ≥ 22.6 (cached). The CLI
checks this before tracing and raises a clean `click.ClickException` with remediation text
when Node is missing/old — never a traceback. This mirrors the Python 3.12 gate (Python is
always-on because `requires-python = ">=3.12"`; Node is conditional on the toolchain).

### 5. CLI dispatch

`grackle trace` currently hardcodes `PythonRuntimeAdapter()`. Phase 8.5 adds
**adapter-by-language dispatch**: infer language from the script extension
(`.ts/.tsx/.mts/.cts → typescript`, `.py → python`) or an explicit `--language`, then
`registry.get_runtime(lang)`. The rest of the trace flow (`--connect`, `--stream`,
`--output`, `--max-events`) is adapter-agnostic and unchanged. Node-ID resolution uses
`registry.get_static("typescript")` for the project static graph, exactly as the Python
path uses the Python static adapter.

### 6. Module layout

New `packages/agent/src/grackle/node_runtime/`:
`__init__.py` (registration) · `adapter.py` (Protocol impl) · `cdp_client.py` (async CDP over
`websockets`) · `launcher.py` (spawn + lifecycle + `Profiler.stop`) · `profile_reconstruct.py`
(**pure** sample/`timeDeltas` → `call`/`return` stream) · `coverage_poll.py` (**pure** snapshot
diff → live `call` events + offset→line) · `node_resolution.py` (TS `NodeResolver`) ·
`capability.py` (cached node detect/version) · `bootstrap.mjs` (done-signal shim, shipped as
package data).

Touched: `grackle/__init__.py` (one import to trigger registration) · `cli.py` (language
dispatch + gate) · `pyproject.toml` (package-data include for `bootstrap.mjs`).

## Consequences

- **The whole Phase 6–8 pipeline becomes polyglot for free** — server, seek, aggregation,
  Timeline, heat, flame, diff, session store all work on Node `TraceEvent`s unchanged.
- **`--stream` for Node is activity-coarse, not magnitude-faithful.** The coverage poll
  emits one `call` event per active function per poll (`frame_depth: 0`), with the exact
  per-poll call delta carried in `metadata.count`. Because the existing heat/aggregation/diff
  consumers count *events* and do not read `metadata.count`, the rendered live heat reflects
  *which functions were active per poll*, not their call frequency — and `grackle diff` on a
  `--stream`-captured file compares poll-activity, not call counts. Magnitude-faithful heat
  and the intended `diff` input come from the sampling path (`trace()` → `--connect` / `-o`),
  which emits real per-call frames with nesting. Two fast-follows are noted: a consumer that
  weights by `metadata.count` (would deliver exact live heat), and the single-session
  live→faithful merge. Until then the live channel is a real-time "what's running now"
  signal, not a call-count heat map.
- **Type-stripping is the supported execution model** (Node ≥ 22.6). Non-erasable TS
  (enums, namespaces, parameter properties) fails type-stripping → a clear error pointing
  at the limitation. CI runs Node 22 (for the frontend), so the gated end-to-end test runs
  **in CI**, not only locally.
- **No new runtime dependency** (CDP client over `websockets`; `bootstrap.mjs` is a few lines).
- **Testability is high**: `profile_reconstruct`, `coverage_poll`, and `NodeResolver` are
  pure and fixture-driven (run everywhere); only the spawn/CDP end-to-end is Node-gated.

### Out of scope for 8.5 (→ Phase 9)

- **Sourcemap translation** for compiled `.js` / bundled output (map transpiled positions →
  original `.ts`). Today: run `.ts` directly via type-stripping.
- **`.tsx`** via external loaders (tsx/ts-node) and their sourcemaps.
- **Worker threads** (each is a separate inspector target → multiple profiles/`thread_id`s).
- **Cross-process correlation** (Python↔Node over the existing subprocess/HTTP cross-language
  edges → a distributed-trace timeline). If pursued, earns ADR-0023.
- **Go/Rust runtime adapters** (extend this pattern: Go `runtime/trace`, Rust `tracing`).

## Alternatives considered

- **Single mechanism (sampling only).** Simplest, but no real-time signal — `--stream`
  would deliver nothing until process end. Rejected in favour of the hybrid so live heat
  works mid-execution (per the 8.5 scope decision).
- **Single mechanism (coverage only).** Real-time and exact for heat, but no nesting →
  no faithful flame, the headline Phase-8.2 view. Rejected.
- **Debugger stepping for true call/return.** Exact and ordered, but 10–100× overhead makes
  any non-trivial script unusable. Rejected.
- **Run compiled `.js` + parse `.js` statically.** Avoids type-stripping, but there is no JS
  static parser, and `.js`↔`.ts` line drift reintroduces the resolution problem. Rejected;
  type-stripping keeps source positions intact.
- **In-process tracing.** Impossible — grackle is a Python agent and cannot execute JS
  in-process; an external Node subprocess driven over CDP is mandatory.
