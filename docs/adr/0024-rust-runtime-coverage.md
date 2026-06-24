# ADR-0024 — Rust Runtime Adapter via LLVM Coverage Instrumentation

**Status:** Accepted (implemented in Phase 9.2, 2026-06-23)
**Date:** 2026-06-23
**Phase:** 9.2

---

## Context

The static graph is polyglot (Python, TypeScript, Go, Rust — ADRs 0006/0009/0010), and the
runtime overlay covers Python (`sys.monitoring`, Phase 6), TypeScript/Node (V8 Inspector,
ADR-0022, Phase 8.5), and Go (coverage instrumentation, ADR-0023, Phase 9.1). Rust has a
**static adapter only**. Phase 9.2 closes the gap by adding a real `RustRuntimeAdapter` that
emits the **same `TraceEvent` schema** (`{event, node_id, ts_ns, thread_id, frame_depth,
metadata?}`, ADR-0013), resolved to the Phase-4 Rust static-parser's node IDs, so the entire
Phase 6–8 pipeline works on Rust events with **no wire-schema change**.

ADR-0023 establishes the "native coverage channel" template. Rust follows the same decision
pattern: pick a first-party coverage tool with no external deps, emit coarse call counts, and
count-weight aggregation server-side (already in `aggregates.py` from 9.1).

Rust has no zero-touch call/return firehose comparable to `sys.monitoring` or the V8 Inspector.
The options are:

| Mechanism | Fidelity | Overhead | Dependency | Notes |
|---|---|---|---|---|
| `RUSTFLAGS=-Cinstrument-coverage` (source-based coverage) | coarse — entry counts | low (counter incr) | none (stable since 1.60) | first-party, cross-platform |
| `tracing` crate + subscriber | call spans | low | crate dep | author-written spans only; misses all uninstrumented code |
| perf/dtrace/pprof-rs | sampled flame | medium | platform-specific | Unix-only → fails Windows CI contract |
| proc-macro auto-instrumentation | exact | medium | invasive macro dep | modifies user source; changes compilation semantics |

`-Cinstrument-coverage` is chosen: it is Rust's **first-party, cross-platform** mechanism,
stable since 1.60, requires no external crates, and produces per-function call counts directly
comparable to Go's `go build -cover` approach (ADR-0023).

## Decision

### 1. Mechanism — `-Cinstrument-coverage` → run → `llvm-profdata merge` → `llvm-cov export`

```
RUSTFLAGS="-Cinstrument-coverage" cargo build -p <pkg> --bins \
    --message-format=json-render-diagnostics --target-dir <tmp>/target
LLVM_PROFILE_FILE=<tmp>/prof/grackle-%p-%m.profraw ./<binary>
<sysroot>/lib/rustlib/<host>/bin/llvm-profdata merge -sparse <profraw>... \
    -o <tmp>/merged.profdata
<sysroot>/lib/rustlib/<host>/bin/llvm-cov export <binary> \
    -instr-profile=<tmp>/merged.profdata --format=json
```

All steps run inside a `tempfile.TemporaryDirectory` — nothing is written into the user's
project tree. The sysroot binaries (`llvm-profdata`, `llvm-cov`) are discovered via
`rustc --print sysroot` + `host:` line from `rustc -vV`, then checked for `.exe` on Windows
(`llvm-tools-preview` installs uniformly via `rustup component add llvm-tools-preview`).

The build uses `--bins` (not bare `-p`) to instrument binary targets only, avoiding
lib/tests/examples instrumentation. The correct binary artifact is selected by matching
`target.src_path` (from `cargo build --message-format=json` stdout) to `script.resolve()` —
no name-guessing, robust across `src/main.rs`, `src/bin/<name>.rs`, and custom target-dirs.

The subprocesses run with `CARGO_NET_OFFLINE=true` (no network access during trace),
`CARGO_INCREMENTAL=0` (force full rebuild — incremental build with RUSTFLAGS changes would
produce stale artifacts), with stdin closed (a stdin-reading program fails fast instead of
hanging), and with stdout/stderr captured. A non-zero program exit is **not fatal**: coverage
counters are flushed on normal return. They are lost on abort/SIGKILL/`panic=abort`
(documented limitation). Failure is detected by the absence of `*.profraw` files after the run.

Minimum Rust version: **1.60** — first stable release where `-Cinstrument-coverage` is stable.

### 2. Event shape — "exact counts, coarse events" (ADR-0022 contract)

Each executed function emits **one `call` TraceEvent** with:
- `event: "call"`
- `node_id`: resolved static-graph node ID
- `ts_ns`: single `time.monotonic_ns()` stamp (Rust gives no per-function timestamps)
- `thread_id`: `threading.get_ident()` (Rust gives no thread IDs at this layer)
- `frame_depth: 0` (coarse — no stack reconstruction)
- `metadata.count`: summed entry count across all monomorphisations (see §3)

No `return`, `line`, or `exception` events. `trace_streaming()` raises `RustRuntimeError`
with a clear remediation message — live streaming requires the program to still be running,
which conflicts with the completed-trace coverage model.

### 3. Monomorphisation folding — sum by resolved node_id

`llvm-cov export --format=json` emits one `functions[]` entry **per type instantiation** of a
generic function. Each entry carries an independent `count`. Two entries for `fn foo<T>` (say,
`foo::<i32>` with count 3 and `foo::<str>` with count 1) resolve to the **same** static-graph
node ID because they share the same source file and declaration line.

The adapter folds by resolved `node_id`, **summing counts** across all entries with that ID.
This gives the total number of times the source function was entered, regardless of type
arguments. (Compare Go: `go tool covdata textfmt` emits one entry per block, and we take the
entry-block min-line count, not the sum. The distinction is that Go emits separate records for
distinct functions, whereas Rust emits separate records for distinct type instantiations of the
same source function.)

### 4. Count-weighted aggregation — no change needed

`TraceAggregates` (`python_runtime/aggregates.py`) already weights by `metadata.count`
(default 1) since ADR-0023. Rust events carry `metadata.count = N` → `cumulative_heat`
returns N. Existing Python/Node traces are byte-identical (count defaults to 1). The wire
schema is **unchanged** → `check-parity` remains a no-op.

### 5. Correctness traps

**Trap 1 — absolute-path normalization.** `llvm-cov export` emits real filesystem absolute
paths (`/Users/me/project/src/main.rs`). `RustResolver._normalize` calls
`to_posix(Path(abs_path), self._root)` with `except (ValueError, OSError): return None`.
`to_posix` resolves both sides via `.resolve()`, so macOS `/var`→`/private/var` symlinks and
Windows short-path aliases canonicalize before the relative-to computation. Non-project paths
(stdlib, deps) return `None` and are silently skipped. No module-prefix stripping is needed
(unlike Go's import-path-prefixed covdata paths).

**Trap 2 — region start line ≠ declaration line.** The Rust static graph stores `line` =
fn-keyword declaration line; `llvm-cov export` region start lines are body statement lines.
`RustResolver` sets `_build_decl_index = True` (same as `GoResolver`) to use
`_resolve_by_decl_line` (bisect by `(decl_line, node_id)` list per file). This is the same
bisect implemented in the shared `RuntimeResolver` base in Phase 9.1.

**Trap 3 — `--bins` artifact `src_path` match.** Selecting the built binary by
`target.src_path == script.resolve()` avoids guessing binary names and handles multiple
`[[bin]]` entries in one `Cargo.toml`. A missing match → `RustRuntimeError` with a clear
"not a binary entry point" message. `src/lib.rs` is not gate-rejected by name at
`runtime_unavailable_reason` (a bin-crate may have both lib + main); the toolchain's bin-match
error handles it cleanly.

**Trap 4 — sysroot binary discovery on Windows/MSVC.** `llvm-profdata.exe` / `llvm-cov.exe`
live under `<sysroot>/lib/rustlib/<host>/bin/`. The `llvm_tool_path(name)` helper checks both
the non-`.exe` and `.exe` suffixes. `rustup component add llvm-tools-preview` installs both
on all platforms.

### 6. Crate and package selection

For workspace projects, `toolchain.run` uses `rust_parser.workspace.get_crates(root)` (the
same function used by the static parser) and selects the crate by **`posix_root` prefix-match**
against `to_posix(script, root)` — the longest matching crate prefix wins. Single-crate
(non-workspace) projects have `posix_root == ""`, which matches everything — correct behavior.

### 7. Registry dispatch

`RustRuntimeAdapter` registers unconditionally with `extensions=(".rs",)`. The existing
`_resolve_runtime_adapter` in `cli.py` is fully registry-driven — **no `cli.py` dispatch
changes** are needed. `grackle languages` automatically lists `rust` with runtime capability
status. Files under `tests/` or `benches/` are cheaply gate-rejected at
`runtime_unavailable_reason` (those components clearly don't contain binary entry points).

### 8. CI — Rust toolchain in `_checks.yml`

`dtolnay/rust-toolchain@stable` with `components: llvm-tools-preview` is added to
`_checks.yml` after the Go toolchain step. This shared workflow file covers both `ci.yml`
(PR: ubuntu + windows) and `ci-matrix.yml` (push: + macOS), so one edit covers all. Without
it the e2e tests silently skip on every OS (capability gate closes → `pytestmark` → green CI
testing nothing).

## Alternatives rejected

- **`tracing` crate + subscriber**: requires author-written `#[instrument]` spans — misses all
  undecorated code; invasive and only sees what the user explicitly annotated.
- **perf/dtrace/pprof-rs**: platform-specific; fails the Ubuntu + Windows CI contract
  (cross-platform contract, `docs/cross-platform.md`).
- **proc-macro auto-instrumentation**: adds a crate dependency, modifies compilation semantics,
  changes binary size/timing in ways that could affect tests.
- **`cargo-llvm-cov`**: third-party wrapper around the same `llvm-cov` mechanism; adds a Cargo
  plugin dependency for no additional capability over calling `llvm-cov` directly.

## Constraints honored

- POSIX path discipline (ADR-0001, cross-platform contract)
- Open strings, not enums, on all extension surfaces (ADR-0004)
- No new Python dependencies; no Rust code shipped in this repo
- `mypy --strict` on all new Python code
- `RuntimeAdapter` is a `@runtime_checkable` Protocol, not an ABC (ADR-0003)
- Bind only to `127.0.0.1` — N/A for this adapter (no network)
- Cross-platform: CI matrix covers Ubuntu + Windows; POSIX path discipline applies to all
  emitted node IDs regardless of OS

## Known limitations

- `trace_streaming()` / `--stream` for Rust: unsupported (raises typed error); faithful flame
  (sampling/pprof-rs) is documented future work in this ADR.
- Cold instrumented build can take ~300 s (Rust compiles slower than Go under instrumentation;
  Go uses 120 s). Warm subsequent builds within the same `TemporaryDirectory` are fast, but
  the temp dir is discarded after each `trace()` call.
- Coverage is lost on abort, SIGKILL, or `panic=abort` (not on normal return or `panic=unwind`
  with `std::process::exit`).
- env `RUSTFLAGS` override: the adapter appends `-Cinstrument-coverage` to the current
  `RUSTFLAGS` env variable. This overrides any RUSTFLAGS set in `.cargo/config.toml` (env
  takes precedence over config). This is the same single-assignment posture as Go's `GOWORK=off`.
- Generic-receiver methods and trait impls: the static parser maps these to `interface` nodes
  where applicable; uncaptured monomorphisations fall through to the file node (visible, not
  crashing).
- Frontend live-heat count-weighting: parked (frontend does not yet read `metadata.count`).
