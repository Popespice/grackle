# Phase 9 Summary — Native runtime adapters

**Tag:** `v0.9.0-phase-9`
**Shipped:** 2026-06-30

Phase 9 closes the runtime-adapter gap left by Phase 8: Go and Rust now register real
`RuntimeAdapter`s (via coverage instrumentation, not a debugger/profiler protocol) and pay down
two named debts from ADR-0020 and ADR-0021 — all on the **unchanged** `TraceEvent` schema. Two
ADRs (0023–0024) were accepted; two prior ADRs (0020, 0021) were amended.

## What shipped

### 9.1 — Go runtime adapter (PR #40, ADR-0023)

`GoRuntimeAdapter` (language `"go"`, `packages/agent/src/grackle/go_runtime/`) drives Go programs
via `go build -cover -covermode=count -coverpkg=./...` → run with `GOCOVERDIR` → `go tool covdata
textfmt` → pure-Python parse (`covdata_parse.py`) → one `call` `TraceEvent` per executed function,
`metadata.count` = entry-block call count, `frame_depth: 0`. Reuses the whole Phase 6–8 pipeline —
no wire-schema change, no new Python deps (`go` toolchain ≥ 1.20 required, capability-gated).

Correctness traps handled: covdata import-path-prefixed paths stripped via a `go.mod` module read
+ `to_posix`; block start line ≠ decl line, resolved via a decl-line bisect
(`_resolve_by_decl_line`) added to the shared `RuntimeResolver` base. `GOWORK=off` neutralizes
inherited workspaces; stdin closed; `TimeoutExpired`/`OSError` → typed `GoRuntimeError`.
`TraceAggregates` now weights by `metadata.count` (default 1 → byte-identical for Python/Node
traces), via a memory-safe parallel prefix-sum (`weight_prefix`). `trace_streaming()` raises a
clean typed error.

### 9.2 — Rust runtime adapter (PR #43, ADR-0024)

`RustRuntimeAdapter` (language `"rust"`, `packages/agent/src/grackle/rust_runtime/`) drives Rust
programs via `RUSTFLAGS=-Cinstrument-coverage cargo build` → run with `LLVM_PROFILE_FILE` →
`llvm-profdata merge` → `llvm-cov export` → pure-Python parse (`llvm_cov_parse.py`) → one `call`
`TraceEvent` per executed function, `metadata.count` = summed entry count across all
monomorphisations, `frame_depth: 0`. Same no-wire-change, no-new-deps shape as 9.1 (Rust toolchain
+ `llvm-tools-preview` required, capability-gated).

Correctness traps handled: absolute-path normalization via `to_posix` (macOS `/var`→`/private/var`,
Windows short paths); monomorphised generics SUM counts by resolved `node_id`; sysroot binary
discovery via `rustc --print sysroot` + `host:` triple; macro-expansion regions (fileID>0) filtered
from `start_line` computation; workspace-root-as-package fallback in `_resolve_package`.
`trace_streaming()` raises a clean typed error.

### 9.3 — Small debts (PR #45, ADR-0020 + ADR-0021 amended)

Two debts named as deferred in their own ADRs, closed with zero new message types:

- **Live-stream recording sink** (ADR-0020 amendment). `serve --store PATH` now tees inbound live
  `--stream` sessions to `<store>/recordings/<id>.jsonl` and registers them in the `SessionLibrary`
  so they're loadable and seekable later. `RecordingSink` writes in **binary mode** with a
  hand-tracked byte offset (no per-event `tell()`), salvages a torn write with a single
  `truncate()`, and is finalized on clean `trace_session_end`, producer disconnect, *and* server
  shutdown (`asyncio.shield`-protected). `is_safe_session_id` allow-lists the session id
  (path-traversal guard); exclusive file creation (`"xb"`) guards a same-session-id collision;
  empty or broken recordings are never registered. A startup sweep clears orphaned `.part` files.
- **Diff-baseline persistence** (ADR-0021 amendment). The `DiffPanel` "Set as baseline" snapshot
  now persists to `sessionStorage`, keyed by `graphCacheKey` (the existing content-hash helper),
  and restores on reload. A baseline from one project never restores onto another (hash miss); the
  overlay auto-enables only on the first restore per mount, so it doesn't override a user's "Hide
  overlay" choice on a routine `static_graph` re-push.

Go/Rust ship `trace()` only — exact-count coarse events (`frame_depth: 0`, `metadata.count`), per
the ADR-0022 asymmetric-fidelity precedent established for the Node runtime; `trace_streaming()`
raises clean typed errors on both.

### Also on `main` (not a Phase 9 chunk) — PR #46

A sibling fix landed on `main` during the phase: a pre-existing `GraphCanvas` crash on duplicate
node IDs (`@property` getter/setter pairs and `@overload` stubs produced colliding IDs, crashing
`graphology`'s `addNode`). Fixed in `python_parser/visitors.py` (dedup at parse time) +
`buildGraphology.ts` (defensive dedup) + a new `ErrorBoundary.tsx` wrapping `SlotContainer` panels.
Unrelated to the runtime-adapter work; noted here for the historical record, not counted in the
acceptance grid below.

---

## Code-review fixes (per chunk, pre-merge)

Each chunk went through an adversarial `/code-review` pass; the actionable findings were fixed in
a follow-up commit folded into the squash before merge. The highest-signal fixes:

| Chunk | Representative fixes |
|---|---|
| 9.1 | Count-weighting rewritten to a memory-safe parallel prefix-sum (`weight_prefix`) instead of a per-event list scan; covdata import-path→fs mapping corrected via `go.mod` read; decl-line bisect fallback for block-start-line mismatches; `GOWORK=off` to avoid inherited workspace interference; typed `GoRuntimeError` on toolchain failures. 8 issues found and fixed pre-merge. |
| 9.2 | Sysroot binary discovery via `rustc --print sysroot` + host triple (was hardcoded, broke on custom toolchains); monomorphised generic counts summed by resolved `node_id` (was overwriting); macro-expansion regions (fileID>0) excluded from `start_line`; workspace-root-as-package fallback added. Two rounds of CI-only failures fixed (platform-specific sysroot layout). |
| 9.3 | **[HIGH]** `RecordingSink` rewritten from text-mode + `tell()`/`seek()` to **binary mode** with hand-tracked byte offsets — fixed an off-by-one between `_event_count` and the salvage offset, a per-event `tell()` perf regression, and a Windows `\n`→`\r\n` salvage-corruption risk, all in one change. Added `is_safe_session_id` path-traversal guard and an exclusive-create collision guard. Made `finalize()` fully guarded so a recording failure can never crash the receive loop. Also strengthened three weak/flaky tests written during the same chunk: a vacuous salvage test (flaky-file double raised before writing any bytes), a racy duplicate-session-id test, and a non-load-bearing persist-queue race test. |

---

## Acceptance grid — Phase 9

| # | Criterion | Status |
|---|---|---|
| 1 | **Go runtime adapter.** `grackle trace app.go -o t.jsonl` drives Go via `go build -cover` → `GOCOVERDIR` → `go tool covdata textfmt` → pure-Python parse → one `call` per executed function (`metadata.count`, `frame_depth: 0`), resolved to Go static-graph node IDs; replay / heat / `grackle diff` work on the file. | **9.1 ✓** automated (Go-gated e2e) |
| 2 | **Go correctness traps.** covdata import-path prefix stripped via `go.mod` module read + `to_posix`; block-start-line ≠ decl-line handled by decl-line bisect in `RuntimeResolver`; `GOWORK=off`; toolchain errors → typed `GoRuntimeError`. | **9.1 ✓** automated |
| 3 | **Count weighting.** `TraceAggregates` and `diff.py` weight by `metadata.count` (default 1 → byte-identical output for Python/Node traces). | **9.1 ✓** automated |
| 4 | **Rust runtime adapter.** `grackle trace app.rs -o t.jsonl` drives Rust via `RUSTFLAGS=-Cinstrument-coverage` → `llvm-profdata merge` → `llvm-cov export` → pure-Python parse → one `call` per executed function (`metadata.count`, `frame_depth: 0`), resolved to Rust node IDs. | **9.2 ✓** automated (Rust-gated e2e) |
| 5 | **Rust correctness traps.** absolute-path normalization via `to_posix`; monomorphised generics SUM counts by `node_id`; sysroot binary discovery via `rustc --print sysroot` + host triple; macro-expansion regions (fileID>0) filtered; workspace-root-as-package fallback. | **9.2 ✓** automated |
| 6 | **Channel contract (Go/Rust).** Both ship `trace()` only — exact-count coarse events per the ADR-0022 asymmetric-fidelity precedent; `trace_streaming()` raises a clean typed error with remediation. | **9.1 / 9.2 ✓** automated + design |
| 7 | **Capability gate (Go/Rust).** Both register unconditionally (visible in `grackle languages`); missing toolchain (Go ≥ 1.20; Rust cargo + rustc + `llvm-tools-preview`) → clean typed remediation, never a traceback. | **9.1 / 9.2 ✓** automated |
| 8 | **Runtime-adapter matrix complete.** Python (`sys.monitoring`), TypeScript/Node (V8 Inspector), Go (coverage), Rust (LLVM coverage) — all four language runtimes now register a `RuntimeAdapter`. | **9.1 / 9.2 ✓** automated |
| 9 | **Live-stream recording sink.** `serve --store PATH` tees inbound producer sessions to `<store>/recordings/<id>.jsonl` and registers them (loadable + seekable); finalize fires on clean `session_end`, producer disconnect, and server shutdown; binary-mode atomic write with single-`truncate` salvage; empty/broken sessions never registered. | **9.3 ✓** automated |
| 10 | **Recording-sink safety.** `is_safe_session_id` allow-list blocks path traversal; exclusive-create guards a colliding session id; a recording failure never harms the live fan-out or crashes the receive loop; startup sweep clears orphaned `.part` files. | **9.3 ✓** automated |
| 11 | **Diff-baseline persistence.** `DiffPanel` "Set as baseline" persists to `sessionStorage` keyed by `graphCacheKey`; restored on reload (same project → restore, different project → hash miss); cleared via the existing affordance; overlay auto-enable only on first restore per mount; empty/negative baselines rejected. | **9.3 ✓** automated |
| 12 | **ADR discipline.** ADR-0023 (Go) + ADR-0024 (Rust) accepted; ADR-0020 + ADR-0021 amended (recording sink; baseline persistence) — no new ADRs in 9.3, count stays 24. | **9.1 / 9.2 / 9.3 ✓** manual |
| 13 | **No wire-schema change; cross-OS.** `KNOWN_MESSAGE_TYPES` unchanged all phase, `check-parity` a no-op for every chunk; all green on the Ubuntu + Windows CI matrix (Go/Rust e2e capability-gated). | **9.1 / 9.2 / 9.3 / CI ✓** automated |
| 14 | **Ship.** ADRs 0023–0024 accepted (0020 + 0021 amended); `PHASE_9_SUMMARY.md`; `PROJECT_ACCEPTANCE.md` §E grid (24 ADRs); `CLAUDE.md` (Phase 9 shipped, Phase 10 planned); version 0.9.0; tag `v0.9.0-phase-9`. | **9.H ✓** |

---

## Known limitations

- **Go and Rust are `trace()`-only coarse.** Both emit exact entry-count `call` events at
  `frame_depth: 0` — no faithful `call`/`return` pairing, no real `frame_depth` ladder, no flame
  graph fidelity, and no `trace_streaming()` (raises a typed error). This mirrors the Node
  coverage channel's asymmetric fidelity (ADR-0022), not the Node sampling channel's faithfulness.
- **Toolchain-gated.** Go needs the Go toolchain ≥ 1.20; Rust needs cargo + rustc +
  `llvm-tools-preview`. Either missing degrades cleanly to static-only, never a crash.
- **Recording sink is metadata-only at write time.** The seek index is built on demand at
  `session_load_request` time via `build_seekable`, not during recording.
- **`JsonlIndex` is dense** (~8 bytes/event offset; ~80 MB at 10 M events) and stays re-parked —
  acceptable to ~10 M events per its own docstring.

---

## Phase 10 preview

Phase 10 is the north-star headline: point grackle at any filesystem and watch the graph grow
live as the system is built, with an explanation layer for how and why files connect and fire.
The concrete first step is **value capture → time-travel debugger** — capturing argument/return
values alongside the existing call-graph trace so execution can be scrubbed and inspected after
the fact, not just visualized as frequency/coverage. No ADRs are pre-committed; scope will be
chunked once design work starts.
