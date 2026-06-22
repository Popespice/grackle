# ADR-0023 — Go Runtime Adapter via Coverage Instrumentation

**Status:** Accepted (implemented in Phase 9.1, 2026-06-22)
**Date:** 2026-06-22
**Phase:** 9.1

---

## Context

The static graph is polyglot (Python, TypeScript, Go, Rust — ADRs 0006/0009/0010), and the
runtime overlay now covers Python (`sys.monitoring`, Phase 6) and TypeScript/Node (V8 Inspector,
ADR-0022, Phase 8.5). Go and Rust have **static adapters only**. Phase 9.1 closes the gap for
**Go** by adding a real `GoRuntimeAdapter` that emits the **same `TraceEvent` schema**
(`{event, node_id, ts_ns, thread_id, frame_depth, metadata?}`, ADR-0013), resolved to the
Phase-4 Go static-parser's node IDs, so the entire Phase 6–8 pipeline works on Go events with
**no wire-schema change**.

Go has no zero-touch call/return firehose like `sys.monitoring` or CDP. The options are:

| Mechanism | Fidelity | Overhead | Dependency | Notes |
|---|---|---|---|---|
| `go build -cover` (coverage) | coarse — call counts | low (counter incr) | stdlib | survives `os.Exit`, cross-platform |
| `runtime/trace` + `x/exp/trace` | scheduler events, not calls | medium | `x/exp` dep | scheduler-level; no call/return |
| `go tool pprof` (sampling) | sampled flame | medium | stdlib | async; harder to correlate to node IDs |
| Delve debugger | exact | very high | heavy dep | not suitable for CI, changes timing |

Coverage is chosen: it is Go's **first-party, cross-platform** instrumentation available in the
Go stdlib since 1.20 with no extra dependencies, survives `os.Exit` (counters flushed on return
and `os.Exit`), and is pure-fixture-testable without a Go toolchain installed in the parser layer.

**Deviation from ADR-0022's `runtime/trace` pointer (recorded explicitly):** ADR-0022's
"out of scope for Phase 9" section sketched Go via `runtime/trace`, but that produces
scheduler events (goroutine create/block/unblock), not call/return events, and would require
the `x/exp/trace` package (non-stdlib dep). Coverage is semantically superior for this use
case — it gives per-function call counts directly.

## Decision

### 1. Mechanism — `go build -cover` → run → `go tool covdata textfmt`

```
go build -cover -covermode=count -coverpkg=./... -o <bin> <pkg>
GOCOVERDIR=<tmpdir> ./<bin>
go tool covdata textfmt -i <tmpdir> -o <out>
```

All three steps run inside a `tempfile.TemporaryDirectory` — nothing is written into the
user's project tree. The build target is the enclosing package directory of the script (Go
builds packages, not individual files). `-coverpkg=./...` instruments all packages in the
module regardless of import reachability, so cross-package coverage is deterministic.

A non-zero program exit is **not fatal**: coverage counters are flushed on normal return and
`os.Exit`. They are lost only on panic or SIGKILL (documented limitation). Failure is detected
by the absence of `covmeta*`/`covcounters*` files after the run.

Minimum Go version: **1.20** — first release where `go build -cover` works for non-test
binaries with `GOCOVERDIR`.

### 2. Event shape — "exact counts, coarse events" (ADR-0022 contract)

Each executed function emits **one `call` TraceEvent** with:
- `event: "call"`
- `node_id`: resolved static-graph node ID
- `ts_ns`: single `time.monotonic_ns()` stamp (Go gives no per-function timestamps)
- `thread_id`: `threading.get_ident()` (Go gives no goroutine IDs at this layer)
- `frame_depth: 0` (coarse — no stack reconstruction)
- `metadata.count`: entry-block call count (see §3)

No `return`, `line`, or `exception` events. `trace_streaming()` raises `GoRuntimeError` with
a clear remediation message — live streaming requires the program to still be running, which
conflicts with the completed-trace coverage model.

### 3. Entry-block = call count

`go tool covdata textfmt` emits per-**block** counts. The function's **entry block** (the
block with the lowest start line within the function) executes exactly once per call, so its
count is the per-function call count. We do **not** sum blocks — that would measure
statement-execution volume (loop iterations inflate it). This keeps Go heat/diff semantically
aligned with Python/Node call-counting.

### 4. Count-weighted aggregation contract

`TraceAggregates` (`python_runtime/aggregates.py`) now weights by `metadata.count` (default
1 per event). This means:

- (a) Python/Node-sampling traces omit `count` → weight defaults to 1 → **byte-identical**
  behavior for all existing trace files.
- (b) Node live-coverage traces already carry `metadata.count` → now weighted server-side
  (more correct replay heat).
- (c) Go traces: one event per function, `metadata.count = N` → `cumulative_heat` returns N.
- (d) Wire schema is **unchanged** (`metadata.count` is already an open field) →
  `check-parity` remains a no-op.

### 5. Correctness traps

**Trap 1 — import-path-prefixed covdata paths.** `go tool covdata textfmt` emits paths like
`example.com/tinyapp/models/user.go`, not filesystem paths. `GoResolver._normalize` strips
the module prefix (read from `go.mod` via the existing `_read_go_mod`) and re-anchors via
`to_posix`, honoring POSIX path discipline.

**Trap 2 — block start line ≠ declaration line.** The Go static graph stores `line` =
func-keyword line; covdata block starts are statement lines inside the body. Resolution uses
`_resolve_by_decl_line` (added to the shared `RuntimeResolver` base in Phase 9.1): bisect the
sorted per-file `(decl_line, node_id)` list to find the function whose declaration is the
greatest ≤ statement line. Only `GoResolver` calls this method; Python/Node resolvers are
unaffected.

### 6. Registry dispatch

`GoRuntimeAdapter` registers unconditionally with `extensions=(".go",)`. The existing
`_resolve_runtime_adapter` in `cli.py` is fully registry-driven — **zero `cli.py` changes**
are needed. `grackle languages` automatically lists `go` with its runtime capability status.

`_test.go` inputs are cleanly rejected at the gate (test coverage needs `go test`, which is
out of scope).

## Alternatives rejected

- **`runtime/trace` + `x/exp/trace`**: scheduler events, not calls; non-stdlib dep; see §Context.
- **Delve debugger**: heavyweight dep, changes program timing, not suitable for CI.
- **`go test -cover`**: requires test files, wrong granularity for production tracing.
- **GOFLAGS/overlay into the user tree**: violates the "never write into the user's tree"
  invariant (ADR-0003).

## Constraints honored

- POSIX path discipline (ADR-0001, cross-platform contract)
- Open strings, not enums, on all extension surfaces (ADR-0004)
- No new Python dependencies
- No Go code shipped in this repo
- `mypy --strict` on all new Python code
- `RuntimeAdapter` is a `@runtime_checkable` Protocol, not an ABC (ADR-0003)
- Bind only to `127.0.0.1` — N/A for this adapter (no network)
- Cross-platform: CI matrix covers Ubuntu + Windows; `GOCOVERDIR` is passed as a native
  path string (Go's `os` layer reads it), while emitted node IDs use POSIX discipline

## Known limitations

- `trace_streaming()` / `--stream` for Go: unsupported (raises typed error).
- Faithful flame / frame-depth for Go: out of scope (would require pprof sampling overlay).
- `go.work` multi-module workspaces, `vendor/` dirs, nested modules: single `go.mod` at root
  assumed — same limitation as the Go static parser (ADR-0009).
- Generic-receiver methods: absent from the static graph → blocks fall through to the file
  node (visible, not crashing).
- Coverage is lost on panic or SIGKILL (not on normal return or `os.Exit`).
- Frontend live-heat count-weighting: parked (frontend does not yet read `metadata.count`).
