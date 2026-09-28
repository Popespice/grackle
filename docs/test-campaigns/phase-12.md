# Phase 12 Test Campaign — the full-stack battery

**Date drafted**: 2026-08-20
**Under test**: `v0.12.0-phase-12` (main `c305deb`) — the entire stack
**Environment**: macOS 26.5 / arm64 primary; CI matrix Ubuntu + Windows (+ macOS on main-push), Python 3.12/3.13, Node 22
**Status**: EXECUTED — C0–C6 built; C3–C6 open as stacked PRs for the owner's review; findings recorded here as tiers executed

## Lineage and doctrine

This campaign fuses the repo's two testing traditions:

1. **The tiered probe campaign** (`phase-0.md`, `phase-1.md`, `phase-1.5-1.6.md`, May 2026):
   baseline tiers re-run the documented suites, higher tiers actively probe surfaces the suites
   don't cover, findings are written up with reproducer / observed / expected / fix / severity,
   and positive evidence is recorded, not just failures. No campaign has run since `v0.1.x` —
   **eleven phases of surface have shipped since the last one**: the tracer, streaming, seek,
   aggregation, the session store, differential analysis, four language runtimes, value capture,
   the explanation layer, watch mode, the NN package, and the ML engine.
2. **The mutation-verified battery** (Phase 12.4, commit `99b5a9a`): every test must be
   *demonstrated able to fail* — vacuous tests are deleted, a mutant sweep proves the suite kills
   deliberate defects, and **known-real defects are committed as intentionally-red tests**
   (`it.fails` / strict `xfail`) so a future fix has a discriminating test already waiting and the
   defect is on the record instead of silently tolerated.

The fusion yields one doctrine, stated once and applied to every tier below:

> **Every probe has a stated fail possibility.** A probe that cannot fail is not a probe.
> Confirmed defects become expected-fail tests, committed red. Passing probes must themselves be
> validated — by mutation, by a discriminating-power companion, or by an oracle — before their
> green is trusted.

## Scope and ground rules

- **This is empirical probing and test authoring, not code review.** Findings come from executing
  probes against the real system, in the tradition of the prior campaigns. Fixes for confirmed
  findings land in their own chunks; any review of those fixes is the owner's call to initiate.
- **Chunked execution.** One tier-group per PR (execution plan at the bottom); each chunk stops
  for review before the next begins.
- **Additive, never destructive.** Existing pins are never weakened to make a probe pass. In
  particular, ADR-0029's escape hatch is binding: the synthetic-acceptance test's seeds are never
  changed — margin characterization is a *separate, additive* sweep (T9).
- **The PR gate stays fast.** Fast probes (sub-second meta-tests, boundary tests) join the normal
  suites — Ubuntu finishes ~3 minutes before Windows on every PR, so anything under ~2.5 minutes
  on Ubuntu adds zero wall-clock. Expensive probes (mutation sweeps, seed sweeps, property-test
  long runs) go to a new nightly `campaign.yml` (`schedule` + `workflow_dispatch`). Medium-cost
  cross-OS probes (the numpy floor matrix) go to `ci-matrix.yml` (main-push only, nobody waits
  on it).

## Prerequisites (chunk C0 — small, mechanical)

| # | Change | Why |
|---|---|---|
| P-1 | `xfail_strict = true` in `packages/agent/pyproject.toml` and `packages/nn/pyproject.toml` pytest config | Without strict mode, an xfail that starts passing goes unnoticed — the entire expected-fail ledger (T5) depends on this. Neither package has any xfail today, so this is a zero-risk flip. |
| P-2 | Commit `tools/mutation/` — a minimal, dependency-free mutant harness: a JSON spec per module (`file`, `find`, `replace`, `expect_killed_by`) and a runner script that applies one mutant, runs the named suite, asserts red, reverts | The 12.4 "30/30 mutants killed" figure was produced ad-hoc in-session and **nothing executable was committed** — it cannot be re-run or regression-checked. This harness is the campaign's core instrument and fixes that reproducibility gap. Hand-rolled per the 12.4 precedent; no Stryker/mutmut dependency. |
| P-3 | Decision point: add `hypothesis` to the agent + nn dev groups | Required by T8's property batteries. Dev-dep only; the agent's runtime deps stay untouched. If declined, T8 falls back to hand-written metamorphic sweeps (weaker but still additive). |
| P-4 | Campaign scope for commitlint: none needed — campaign commits land under the owning package scope, `tooling` (harness), or `ci` (workflows) | Confirmed the existing scope enum covers every planned commit. |

---

## The tiers

### T1 — Baseline static (re-baseline)

`pnpm lint` · `pnpm typecheck` · `pnpm check-parity` · per-package ruff / `mypy --strict`.
**Fails if** any drift since `c305deb`. Expected green; recorded for the baseline row.

### T2 — Baseline suites + the silent-skip census

Run all three suites and **enumerate every skip** with its reason. The census is the probe:

| Probe | Fails if | Status |
|---|---|---|
| T2-1: `test_stress_2k_layout.py` executes | ~~`fixtures/stress-2k/src` is not checked in~~ — **retracted (C1): stale premise, same pattern as the T3-2 README claim.** The fixture was committed in Phase 3 (`70fdae0`, "stats panel + 2k benchmark fixture"), well before this campaign was drafted; the test already runs and passes in CI on every OS (0.10s locally, well under the 20s budget). No action needed. | **Retracted (C1)** |
| T2-2: symlink skips are visible | `tests/test_paths.py:77,89` skip at runtime inside `except OSError` — on a Windows runner without Developer Mode both vanish indistinguishably from passing. Probe had two halves. **Done (C1):** `-ra` added to both CI `pytest` invocations (all skip/xfail reasons now print), plus a new unguarded `test_symlink_capability_present_on_posix` in `test_paths.py` — on Linux/macOS it creates a symlink with no try/except, so a genuine CI capability regression fails loudly instead of silently joining the two skip-tolerant tests. **Not done:** the second half — asserting in-job that the skip count matches an expected-per-OS table — was not implemented; `-ra` makes skips *visible* to a human reading the log but nothing *asserts* on them, so a newly-appearing skip on one OS still passes CI. Still open. | **Partially done (C1)** |
| T2-3: nn's implicit `sys.path` coupling | `test_synthetic_acceptance.py:15` does a bare `from synth import …` that only works via pytest's default `prepend` import mode — no `conftest.py` exists under `packages/nn/tests/`. Fix (C1): reproduced first (`--import-mode=importlib` genuinely raised `ModuleNotFoundError` before the fix), then `packages/nn/tests/ml/conftest.py` added, which appends its own directory to `sys.path` if absent — mode-independent because conftest.py files are always executed directly by pytest's own discovery, never gated by `--import-mode`. Verified green under both import modes. Note the append is a no-op under the default `prepend` mode (pytest has already put the directory at `sys.path[0]`, ahead of the stdlib) and only matters under `importlib`; it does not stop a helper in that directory from shadowing a stdlib module name under the default mode. | **Done (C1)** |

### T3 — Guard-of-the-guards (meta-tests: does the safety net itself work?)

The campaign's highest value-per-cost tier. The repo's guards are only ever exercised in the
passing direction; this tier drives each one through its **failure** paths.

| Probe | Fails if | Status |
|---|---|---|
| T3-1: parity guard failing-direction meta-test | `verify-parity.mjs`'s failure paths had **never executed once**. Confirmed (C1) via `packages/shared-types/scripts/verify-parity.test.mjs` (Node's built-in `node:test`, no new dependency — `packages/shared-types` had zero test infrastructure before this): `diffSets` genuinely reports drift on an added/removed/renamed type, and stays green on identical sets (sanity baseline). The vacuity sub-finding — empty-vs-empty comparing vacuously — was **fixed inline in C1**: a 3-line, product-risk-free change to `diffSets` itself (the guard's own logic, not application behavior), with a regression test independently re-verified to fail against the pre-fix code. **Retraction (this row's original trigger was false — the fourth stale premise found in this audit, after T2-1, T3-2 and the schema-README claim retracted in C0):** the original wording claimed "a formatter switching `messages.ts` to single quotes would make both type-set extractions return empty". Verified false — `schemaMessageTypes` parses JSON and is untouched by TS quoting (still yields 17 types), and `unionMessageTypes` *throws* rather than returning empty, so that scenario is caught as ordinary drift, not vacuously. Reaching empty-vs-empty in fact requires two independent simultaneous extraction failures; the guard is kept as a cheap backstop, but it closes a *hypothetical* hole, not the demonstrated one originally claimed. | **Confirmed + fixed (C1); original trigger retracted** |
| T3-2: schema authored under `definitions` is invisible | Both extractors read `$defs` only, with `?? {}` defaults, so a `$def` authored under Draft-07 `definitions` is silently absent from both sides and the guard passes vacuously. Confirmed (C1): a `test.mjs` case constructs a `definitions`-authored schema and asserts `schemaMessageTypes` returns empty today — committed as a real (non-xfail; `node:test` has no native strict-xfail primitive, unlike pytest/vitest) test named `KNOWN GAP (T3-2)`, asserting current behavior so it fails loudly the moment someone fixes the extractor and forgets to update this test. Extractor fix itself deferred to a later chunk. | **Confirmed, probe committed (C1)** |
| T3-3: message-type consts outside the hardcoded path | Both the JS and Python extractors hardcode `$defs.<X>.allOf[*].properties.type.const`. A type declared via `enum:` or `oneOf` generates fine and is **never parity-checked**; duplicate consts collapse silently in the `Set`. Confirmed (C1): both sub-cases reproduced as `KNOWN GAP (T3-3)` tests in `verify-parity.test.mjs` — a `oneOf`-declared type is invisible, and two distinct `$defs` entries sharing one `type.const` collapse into a single Set entry with no error. Fix deferred to a later chunk. | **Confirmed, probe committed (C1)** |
| T3-4: path-discipline lint test (the missing one) | `paths.py:3` declares itself "the single sanctioned location for `Path.relative_to`" — nothing enforced it until C1. `packages/agent/tests/test_path_discipline.py` now scans the whole AST (`ast.walk`, not the module-scope-only shape of `test_ml_bridge_import_hygiene.py`'s walker — both allow-listed sites are inside function bodies) and pins a per-file *use count* — one each in `cli.py` and `python_runtime/node_resolution.py` — both directions (a new or extra use fails loudly, including a second use added to an already-allow-listed file; a stale allow-list entry fails too). A use is any `.relative_to` or `os.path.relpath` reference: direct calls, bound-method aliases, `getattr`, and `from os.path import relpath` (ruff's PTH ruleset does not flag `relpath`). Green today — the codebase already respects the convention modulo the two known exemptions. CLAUDE.md corrected accordingly (see below). | **Done (C1)** |
| T3-5: import-hygiene scanners still discriminate | Re-run (C1): `test_ml_bridge_import_hygiene.py`'s own meta-tests are part of the 1218-test agent suite, still green. The "shared walker" idea from this row's original wording was deliberately **not** implemented: `test_ml_bridge_import_hygiene.py`'s walker stops recursion at function/class boundaries by design (function-local `grackle_nn` imports are fine), while T3-4's scanner must recurse into function bodies (both real allow-listed sites are inside one) — forcing one walker to serve both would be the wrong abstraction, not a shared one. Documented in `test_path_discipline.py`'s own module docstring rather than silently diverging from the original plan. | **Done (C1), design deviation noted** |
| T3-6: codegen determinism + degradation | **Partially done (C1) — two gaps remain open, (a) and (b) below, scope narrowed deliberately during C1, not silently dropped.** Done: the Phase-1 byte-identical-double-run check (previously manual, one-time, never committed) is now a real committed test in `verify-parity.test.mjs`; the non-`.schema.json`-filename gap is confirmed and committed as `KNOWN GAP (T3-6)`; `codegen.mjs` now resolves the `datamodel-code-generator` version once per process, logs it, and pins every generation call in that process to it (`==<ver>`), so the logged version is the one that produced the output. Across processes the requirement stays deliberately unpinned, per this row's original wording. **Not done:** (a) the typo'd-`$ref`-degrades-permissively-while-parity-stays-green sub-case — no probe was written for it in C1; (b) **cross-version** determinism. The committed determinism test runs codegen twice inside one process, and both runs are pinned to the same generator version — so it proves only *intra-run* determinism (no embedded timestamp, no hash-order dependence) and structurally cannot catch a different `datamodel-code-generator` resolving between two CI runs. That drift is live, not hypothetical: `uvx` resolved 0.74.0 and then 0.75.1 minutes apart during the C1 review; the outputs happened to match, so `check-parity` survived on luck. Mitigation today is visibility only (each run logs the version it is pinned to). Both still Open. | **Partially done (C1)** |

### T4 — The mutation battery, formalized and extended

Using the P-2 harness. Each module gets a committed mutant spec; the runner is the regression
check the 12.4 sweep never had.

| Probe | Target modules | Fails if |
|---|---|---|
| T4-1: re-verify the 12.4 sweep | The 8 frontend 12.4 modules (`networkSpec`, `layerStats`, `layerActivity`, `networkLayout`, `epochSeries`, `useAppendOnlyScan`, `beaconNode`, `playheadLookup`) | Any of the re-specified ~30 mutants survives — i.e. the in-session 30/30 claim doesn't reproduce under the committed harness. **Done (C3):** 34 committed specs (`frontend-<module>-*`, 3–6 per module). The 12.4 claim mostly reproduces: the pre-existing suite killed **31 of 34**. The three survivors were real gaps, each now closed by a test shown to fail under its mutant and pass on the original: padding spaces in the architecture beacon becoming phantom unnamed glyphs, which also shifts the backward-sweep highlight (`networkSpec`); a second `train_step`'s backward counter never reset (`layerActivity`, since no test ran two backward passes); and the minimum neuron radius for a dense column in a short canvas (`networkLayout`, where neurons would otherwise draw about 0.16 px across). Mutants that cannot change behavior were dropped, not specified. |
| T4-2: agent pure core | `aggregates.py` (bisect `at_index - 1` off-by-ones), `diff.py`, `value_repr.py` (budget decrements), `writer.py` (offset/count pairing, truncate guard), `jsonl_index.py` | A boundary-flip / dropped-guard mutant survives the agent suite. **Done (C3):** 24 specs. The existing suite killed 15; 9 gaps were closed with new tests: top-k tie-break, `sparse_k` round-down, `build_seekable`'s index/aggregate alignment across malformed and blank lines, `diff` at `at_index=0`, per-bucket ordering, regression counting only hotter (not new/gone), and `value_repr`'s exact-cap boundaries. Three specs are killed only by a suite outside the module's own test file (CLI, recording sink, `build_seekable` cross-check), and each spec names that suite. Found **T5-8** (ledgered). Also noted: the `sparse_k` docstrings overstate its accuracy on dense traces, and `diff_trace_vs_static` and `sparse_k > 1` have no production caller. One judgement call: the new `sparse_k` test pins the round-down the docstrings describe, although dropping it would arguably be more accurate. |
| T4-3: nn pure core | `features.py` (Tarjan lowlink, bucket prefix order, entry-set self-loop rule), `labels.py` (`_event_weight` branches), `metrics_rank.py` (tie averaging), `heat_model.py` (standardization symmetry) | Same. **Done (C3):** 20 specs, including 4 on Adam (T4-5). The existing suite killed 14; 6 gaps were closed. Imported or circularly imported file nodes stay BFS entry points. Tarjan's on-stack rule gets a local test (before, only the dev-only cross-check against the agent's fixture caught it, and only because of that fixture's node order). The top-k tie-break is pinned to its documented value (the old tie test compared an array with itself). `val_loss` uses the standardization the model ships with. Plus the three Adam cases below. |
| T4-4: **calibration case** — `server.py:132` | The `except RuntimeError` ("dictionary changed size during iteration") in the predicted-cache FIFO eviction is **provably unreachable by any existing test** — deleting it leaves the suite green. This is the harness's known-positive control: the mutant *must* survive, proving the harness reports survivors honestly. It then becomes a T7 concurrency target (the only thing that can reach it is a real race). | Expected survivor **Re-run (C3):** still survives, as declared. |
| T4-5: vacuous-test audit (the 12.4 deletion rule, applied repo-wide) | Known candidates seeded by audit: `StatsPanel.test.tsx:57-74` (disjunctive `toMatch(/Foo\|baz/)` over the only two candidates — passes under random ranking; two tests, one a duplicate of the other), `CyclesPanel.test.tsx:63-68` (same pattern), `test_optim.py:36` (Adam checked only with a constant gradient, where bias correction cancels **exactly** — cannot distinguish `β^t` from `β^(t±1)`), the SessionStore/stream-sender lock tests that pass with the lock removed (T7). Each is either strengthened to discriminate or deleted per the 12.4 rule. | A listed test survives its targeted mutant. **Done (C3)** for the frontend and optimizer candidates; the lock-test candidates move to T7 (C4). StatsPanel/CyclesPanel: specs written *before* the fix show four ranking/preview mutants surviving the old tests (8/8 and 6/6 green). The tests now assert exact order over tie-free fixtures, plus a largest-cycle-first test, and all four mutants are killed. Neither StatsPanel test was deleted: they cover different sections (Top and Hub), so they are not duplicates. **Retraction (Adam):** the claim that the constant-gradient test "cannot distinguish `β^t` from `β^(t±1)`" was false. The exponent mutant was already killed by `test_adam_two_step_matches_reference`. The gap was real but sat elsewhere: under a constant gradient, bias correction makes the corrected moments equal g and g² whatever the betas, so swapped betas, eps inside the square root, and a per-parameter step counter all gave bit-identical parameters. A 15-step sign-flipping gradient test against an independent NumPy reference (Kingma & Ba, Algorithm 1; rtol 1e-12) now kills all four Adam specs, and a guard test keeps it from being "simplified" back to a constant gradient. |

### T5 — The expected-fail ledger

Known-real defects, committed red. The 9 frontend `it.fails` from `99b5a9a` are the existing
ledger (4× `layerActivity` phase-never-returns-to-idle — 60/60 epoch markers on the real
`run-a.jsonl` read "loss" where every loss-curve click lands; 2× thread-clobbering single-slot
scan state; 2× glyph/neuron hit-test collision; 1× sub-210px layout mirroring). This tier adds
the agent-side ledger, each entry written as a **failing test first**, committed strict-xfail:

| Probe | The defect | Status |
|---|---|---|
| T5-1: KeyboardInterrupt outside the script body skips finalize | **Confirmed REAL GAP.** The incremental `-o` block (`cli.py:584-602`) catches `Exception` but has no `finally` — and `Tracer.run`'s `except BaseException` protects only `runpy.run_path`, not `_build_tracer` (`adapter.py:98`), which does a **full project parse** — the longest window in the whole command. A Ctrl-C there propagates, `_finalize_output` never runs, the `.part` is orphaned, and the next run at the same `-o` path is **bricked** by the exclusive-create refusal with an error message describing the wrong scenario. Four distinct escape windows identified (`_build_tracer`; `Tracer._start()` before the try — which also leaks the `sys.monitoring` tool registration process-wide; `_stop()` in the finally; the sink-captured-BaseException re-raise). The `--stream` tee path has the identical gap (`cli.py:539-541` sits after its try/finally). Repro: monkeypatch `_build_tracer` to raise `KeyboardInterrupt`; assert final file exists / no `.part` survives — fails today. The existing KI test (`test_cli_trace.py:1293`) covers only KI raised *by the traced script*, the one window that already works. **Confirmed (C2):** all four windows and the tee path reproduced — each left a `.part` that made the next run at the same `-o` refuse to start, and the `_start()` window also leaked tool id 3 process-wide. The probe surfaced a sharper fifth symptom: a Ctrl-C landing in the sink was latched and re-raised *even when the traced program caught it and carried on* — the tracer changing the semantics of what it observes. **Fixed (C2)**, with the semantics decided as: (1) a Ctrl-C delivered while grackle's sink is on the stack is the program's interrupt — `Tracer._emit` now latches only `Exception`, so the interrupt propagates into the program exactly as it would one bytecode earlier or later, with the same outcome as the pinned script-frame case (exit 0, trace finalized); (2) `_start()` releases the tool if interrupted after `use_tool_id()`; (3) an interrupt that still escapes (the project parse, `_stop()`) settles the `.part` before re-raising — events captured so far are finalized into `-o`, and with none the `.part` is discarded, so an aborted run leaves `-o` as it found it rather than replacing a previous trace with an empty file. Six regression pins (`test_cli_trace.py`, `python_runtime/test_tracer.py`). **Residual:** a Ctrl-C landing inside `_stop()`'s own `sys.monitoring` calls or inside the settle step itself is not defended further; the CLI process exits either way. | **Confirmed + fixed (C2)** |
| T5-2: `serve()` has no readiness signal | The `test_two_sessions_back_to_back` Windows flake diagnosed: `create_task(serve(...)); await asyncio.sleep(0.05)` — nothing awaitable exists between task creation and socket listen, and the store-backed fixture does strictly more pre-listen work (mkdir + orphan sweep + `detect_language` filesystem walk). `[WinError 1225]` is `ERROR_CONNECTION_REFUSED`: nothing was listening. A second window: `free_port` releases the port before `serve()` rebinds (TOCTOU; asyncio sets no `SO_REUSEADDR` on Windows). A third, latent: finalize's two default-executor round-trips inside the test's 100ms sleep budget. **The same create-task-then-sleep pattern exists at 34 sites across 10 server test files.** Repro: wrap `_ws_serve` with an injected pre-bind delay > 50ms — converts a twice-a-year Windows flake into a 100% cross-platform failure. **Confirmed (C2):** the repro holds exactly — with a 100 ms injected pre-bind delay, the old pattern was refused 10/10. Site count corrected: **27 sites across 11 files** (the audit's "34 across 10" did not survive a recount). **Fixed (C2):** `serve(ready=...)`, an `asyncio.Future[int]` resolved with the bound port once the socket is listening; binding port 0 now works, which closes the `free_port` probe-then-rebind window; a new `start_server` fixture in `packages/agent/tests/conftest.py` waits on it, and all 27 sites were migrated off the sleep. `ready` resolves only after the watch task exists: that task snapshots its file-change baseline before its first await, so a caller woken by `ready` can never have an edit absorbed into the baseline. The old 0.05 s sleep had been covering that ordering without saying so. `test_server_readiness.py` promoted to a regression pin. **Not done:** the third, latent window (fixed post-send sleeps before asserting store rows) — never observed failing, left as is. | **Confirmed + fixed (C2); third window open** |
| T5-3: events after `session_end` diverge recording from broadcast | `server.py:777-783`: after `trace_session_end`, subsequent `trace_event`s are still ring-buffered and broadcast but silently dropped from the recording — a misbehaving producer yields a recording whose `event_count` disagrees with what every connected UI showed. Repro: send end, then two more events; compare. **Probed (C2) — pinned as documented behavior, not ledgered.** The divergence is real: post-end events are broadcast and ring-buffered, the UI appends them after marking the session complete (`useGraphStore.addTraceEvents` does not gate on `traceSessionComplete`), and the recording omits them. Decided as behavior-by-design: the ADR-0020 amendment defines a recording as the `start..end` session, grackle's own producer cannot send after its end (the CLI calls `finish()`, which enqueues the end, only after the tracer stops), and the recording's `event_count` agrees with the producer's own `trace_session_end.event_count`. A `trace_event` carries no session id, so the only place a "fix" could live is the fan-out or UI dropping sessionless events — a product change, not a recorder defect. `test_events_after_session_end_are_broadcast_but_not_recorded` pins both halves, each backed by a mutation spec. | **Pinned as documented behavior (C2)** |
| T5-4: ENOSPC surfaces at the wrong layer | `JsonlPartWriter.write` writes through a **buffered** stream — ENOSPC doesn't surface at the failing `write()` but at a later implicit flush or at `close()` inside `finalize()`. So `broken` stays False, `_last_good_offset` is wrong, and the truncate-salvage guard never fires. The salvage design has an untested hole exactly at the buffering boundary. Repro: a small filesystem image or an injected flush-failure. **Confirmed (C2), both shapes**, with a disk-full raw layer placed *below* the `BufferedWriter` — where a real ENOSPC happens; the existing salvage tests inject their failure *above* it, where the failing `write()` is the one that raises. (a) Everything fits in the 8 KiB buffer: all 40 writes "succeed" (`count` 40, `broken` False, offset 4320) while 1000 bytes reached disk, ending mid-line; the error first appears at `finalize()`'s `close()`. (b) A later flush crosses the limit: the error appears at some later `write()`, not the one whose bytes were lost. Either way the torn tail survives and `count` overstates what is on disk. Salvage cannot work through the buffered handle at all: `BufferedWriter.truncate()` flushes first, so even a writer already marked broken fails again on a still-full disk. Downstream, `RecordingSink` discards the whole recording (its finalize raises) instead of salvaging the complete prefix. Ledgered strict-xfail ×2 (`python_runtime/test_writer.py`). | **Confirmed, ledgered (C2)** |
| T5-5: torn multi-byte UTF-8 kills the whole file | The SIGKILL path can tear mid-UTF-8-sequence; the kill test's script is ASCII-only and never asserts the surviving prefix decodes. `read_jsonl` does one whole-file `read_text(encoding="utf-8")` — an undecodable byte fails the **entire file**, not one line. Repro: kill mid-write of non-ASCII node names. **Premise refuted (C2); tolerance pinned.** Both halves checked. (a) "The SIGKILL path can tear mid-UTF-8-sequence" — rare in practice: `BufferedWriter` flushes its whole buffer *before* buffering a line that does not fit, so the kernel only ever receives whole lines. A tear needs the kill to land inside `write(2)` itself (Linux can cut one short at a page boundary on a fatal signal) or a short write on a full disk (T5-4); 0 of 8 real kills with CJK node names tore. (b) "An undecodable byte fails the entire file" — true only of `read_jsonl`, which is whole-file-strict by contract (it rejects any malformed line, ASCII or not) and is reached by no salvage path. Every salvage-path reader — `build_seekable` (serve `--trace-source`, session load, `diff`), `JsonlIndex.read_window`, and `grackle learn`'s `heat_from_jsonl` — decodes per line and loses only the torn line. Pinned by a constructed torn-tail test (`python_runtime/test_jsonl_index.py`), a real-kill non-ASCII test (`test_cli_trace.py`) and an nn-side test (`tests/ml/test_labels.py`), with three mutation specs. The probe surfaced T5-7. | **Refuted, pinned (C2)** |
| T5-6: `finalize()` failing at `replace()` | Only the `close()` failure is tested; a `replace()` failure (destination open in another process — realistic on Windows) leaves `_finalized` False and the `.part` orphaned with no open handle. **Probed (C2).** The rename is the last step, after the `close()` that flushed every event, so a failed `replace()` leaves a complete, closed `.part`: the CLI fails loudly, names it, and keeps it; `RecordingSink` discards it so it cannot block a later same-id recording. Both pinned, with three mutation specs (one reorders rename-before-close, which is harmless on POSIX but leaves an open handle behind on Windows). **One latent defect ledgered:** a retried `finalize()` on a *broken* writer re-runs `truncate()` on the already-closed handle and raises `ValueError`, not the documented `OSError` — latent because no caller retries today. **Observation, not ledgered:** after a replace failure the CLI still says "partial data kept" when the `.part` holds the whole trace — a copy fix. | **Probed; 1 latent defect ledgered (C2)** |
| T5-7 (new, C2): a torn tail is advertised as an event | Surfaced by the T5-5 probe. `JsonlIndex.build` and `build_seekable` give every non-blank line a slot — deliberately, so offset position equals aggregate index — including a torn, unterminated final line. A salvaged `.part` served with `--trace-source` or loaded from the store therefore advertises `event_count` N+1, and the timeline's last slot reads back empty. Repro: five complete lines plus a torn sixth → `len(index) == 6`. Chiefly downstream of T5-4, the realistic source of a torn tail. Fix direction: skip an *unterminated final* line that fails to parse, which keeps the alignment invariant intact for mid-file lines. Ledgered strict-xfail (`python_runtime/test_jsonl_index.py`). | **Confirmed, ledgered (C2)** |
| T5-8 (new, C3): a non-object JSON line crashes the aggregate builders | Found by the T4-2 workstream. A trace line that parses as JSON but is not an object (`[1, 2]`, `42`, `"str"`, `null`) makes `TraceAggregates.build` and `build_seekable` raise `AttributeError` on `.get`, where an unparsable line is skipped. One such line therefore fails `grackle diff`, `grackle learn`, `serve --trace-source` and session load for the whole file. `JsonlIndex.read_window` does not raise, but returns the non-object as if it were an event. Low severity: grackle never writes such lines. Ledgered strict-xfail ×4 (`tests/test_aggregates.py`). | **Confirmed, ledgered (C3)** |

**Promotion protocol** (applies to every ledger entry, frontend and agent): an expected-fail
turning green (strict xfail XPASS / `it.fails` passing) is the signal the defect got fixed — the
marker is removed in the fixing PR, promoting the test to a permanent regression pin. C2 did this for T5-1 (six tests) and T5-2 (one) in the same PR that ledgered them; the agent-side ledger still open after C2 is T5-4 (×2), T5-6's retry case, and T5-7.

### T6 — Fault injection and recovery

Beyond the ledger entries, the injection battery over every persistence/ingest surface:

| Probe | Fails if |
|---|---|
| T6-1: SQLite store — locked db, corrupt db file, use-after-close, concurrent writers, missing `source_path` | The store has **5 tests, all happy-path**. A corrupt db currently surfaces as an unhandled traceback at CLI startup; a shutdown-vs-finalize race silently loses the session row (`ProgrammingError` swallowed at `recording_sink.py:175-182`); the schema DDL has no migration path — an added column against an existing db no-ops then fails at INSERT. **Done (C4).** Pinned as correct: a save waits out another connection's write lock (WAL reads and `open()` are never blocked); two or four `SessionStore` instances writing to one db lose nothing; use-after-close raises `ProgrammingError` and `close()` is idempotent; a single cancel still lands the recording row even with a 300 ms injected save delay; a missing `source_path` is skipped. **Premise narrowed:** a single Ctrl-C does *not* lose the row, because websockets drains the handlers before `serve()`'s `finally` runs. Only a second interrupt during that drain does. **Ledgered: F-8's a–d** (corrupt db → raw traceback at CLI startup; second interrupt drops the row; a store read error drops the client's WebSocket; `session_load` guards with `exists()` not `is_file()`, so a FIFO hangs shutdown). |
| T6-2: orphan sweep hazards | `sweep_orphaned_recordings` with an unlinkable `.part` (permission error) **raises out of `serve()` startup before the socket binds** — the same unobserved-task-death shape as the T5-2 flake; and the 30-second age heuristic can eat a *live* recording's `.part`, admitted in its own docstring, pinned by nothing. **Done (C4). Both hazards confirmed, and the second is sharper than its docstring admits.** An unremovable orphan `.part` (injected `PermissionError` or a real read-only directory) raises out of `serve()` before the bind; `start_server` now surfaces this in 0.09 s instead of as a silent task death. A peer server's startup sweep deletes an *actively written* recording, not only an idle one: after a live event the `.part` is still 0 bytes with an unchanged mtime (the buffered writer), so it looks orphaned, and `RecordingSink` then discards the **whole** session. The idle case is pinned as documented behavior; **the unremovable-orphan and active-recording cases are ledgered (F-10's a–b).** |
| T6-3: WS ingest — oversized frame (>1 MiB default closes with 1009 *before* the receive loop sees it; whether the in-flight recording finalizes correctly is unpinned), `session_load_request` flood (unbounded `create_task` fan-out), slow-consumer stall (sequential `await ws.send` per connection inside the producer's receive loop — one slow consumer blocks ingest for everyone and for the recording sink; zero coverage) | Any of these crashes, hangs, corrupts a recording, or starves the ring buffer. **Done (C4).** Oversized frame: **correct, pinned.** The producer is closed with 1009 and the recording finalizes with exactly the events before the oversized one, including 5 still queued unconsumed. Load flood and slow consumer: **confirmed, ledgered (F-10's c–f).** The slow-consumer premise needed incompressible traffic to reproduce, since websockets negotiates permessage-deflate by default. Recovery once the stuck consumer leaves is correct and pinned. |
| T6-4: watch mode — file deleted mid-parse through the real watch loop; rebuild-during-rebuild serialization **pin** (currently structural via `max_workers=1` + sequential await; a future `create_task` refactor would silently race the unlocked caches) | The pin is the probe: assert two rapid edits never produce overlapping `_build_static_graph` executions. **Done (C4).** Recovery from a file deleted mid-rebuild is pinned through the real `serve(watch=True)` loop. The no-overlap pin holds, including an 80-edit hammer. **Premise corrected:** raising `max_workers` alone does not break serialization, and neither does fire-and-forget alone. Each mechanism serializes rebuilds by itself, and only losing both (killed: "3 rebuilds started while the first was still running") breaks it. The two single-mechanism specs are kept as `survives` tripwires. **Ledgered: F-9** (a file vanishing mid-parse aborts the whole rebuild; a file that disappears and reappears mid-rebuild leaves clients stale indefinitely). |
| T6-5: malformed-corpus sweep | Extend the `_EIGHT_LINES` in-test template (the repo's best malformed-input pattern) into a shared adversarial-trace generator, seeded, per the `stress-2k/generate.py` committed-generator precedent — used against `read_jsonl`, `JsonlIndex.build`, `TraceAggregates.build`, `heat_from_jsonl`, and the three external-tool parsers (covdata / llvm-cov / V8 profile), which parse untrusted output and whose e2e tests are toolchain-gated off most CI legs. Fails if any parser raises, hangs, or emits a node_id containing `\` or `..`. **Done (C4).** A seeded generator (18 trace line kinds, including invalid UTF-8, BOM, lone CR, 1e400 counts, 256-deep nesting, 64 KiB node ids and raw U+2028) plus adversarial covdata, llvm-cov, V8-profile and V8-coverage inputs. Gate runs seeds 0–7; the nightly hammer runs 8–399. With no unexpected exceptions and no hangs, `JsonlIndex.build`, `build_seekable` and `TraceAggregates.build` agree byte-for-byte on offsets and counts and match an independent oracle at every position; resolvers only ever return real graph ids, `<unresolved>` or None. 10 specs, all surviving the pre-existing suites and killed by the new ones. **Ledgered: F-11**, ten parser-robustness defects. |

### T7 — The concurrency battery

The audit found **9 concurrency seams; exactly 1 (`CacheManager`) has a test that fails if its
synchronization is removed.** Every other lock/serialization in the agent is decorative as far
as the suite can tell.

| Probe | Seam | Fails if |
|---|---|---|
| T7-1 | tree-sitter parser singleton — concurrent `.parse()` | The source itself flags this un-audited (`server.py:468-476`); the existing 4-thread test asserts only singleton *identity*, never racing `.parse()` — the actual documented risk, reachable today from the watch executor + connect path. **Done (C4) — pinned, and the safety turns out to be the GIL's, not a lock's.** py-tree-sitter 0.25.2 holds the GIL for the whole `parse(bytes)`: 8 threads × 4 parses per grammar, plus concurrent walker runs, all match the single-threaded trees (run in a child process, so a crash is a clean failure). Positive control: forcing real overlap through the read-callback form of `parse` crashes 10/10 (SIGSEGV/SIGBUS). Two specs switch to that form or add a debug logger, and both are killed by the crash. ADR-0027 was amended: the "cancellable parser" future work needs a per-thread `Parser` first. |
| T7-2 | stream sender `_counter_lock` | No test races `sink()` against `_drain_loop()` — the exact lost-decrement the lock's docstring says it prevents. Mutating the lock away likely leaves the suite green (T4-5 crossover). **Done (C4) — pinned; premise partly wrong.** The lost update the lock guards cannot happen on GIL builds: an unlocked `+=`/`-=` on a plain int attribute lost 0 of 1M operations on CPython 3.12, 3.13 and 3.14 at a 1 µs switch interval. The lock matters on a free-threaded build, or if the counter is ever routed through Python code. A paused-write test (the counter behind a property) catches both lost-decrement and lost-increment directions. The three lock-removal specs all survived the whole pre-existing suite, and all are now killed. |
| T7-3 | `SessionStore` lock | All 5 tests single-threaded; N-writer hammer test modeled on `test_cache.py:301` (the one good example in the repo). **Done (C4) — pinned.** With the lock neutered, 4 writers × 25 saves plus 2 readers lost 21–28 of 100 rows per run (`InterfaceError`, `SystemError`, "cannot start a transaction within a transaction"); with it, zero errors. A deterministic probed-connection test parks one call inside SQLite and records any call that gets in beside it. Per-method lock-removal specs (save/list/get/close) are all killed; 8 of this workstream's 12 specs survived the pre-existing suite. |
| T7-4 | `meta_cache` / `predicted_ctx.cache` unlocked dicts (executor thread + connect path) | The documented "benign race" has never been exercised; the `except RuntimeError` mitigation is the T4-4 calibration survivor. A two-thread hammer either reaches it (promoting it from unreachable to pinned) or the benign-race claim gets its first evidence. **Done (C4) — the T4-4 guard is reached and pinned.** Two threads on the real `_cache_bounded` hit the `RuntimeError` branch 10–15 times per 4M calls at the default switch interval, and 136–228 per 40k at 1 µs; the cache always ends at its cap. Deterministic gate tests hold the `iter()`/`next()` window open: through the real `_build_static_graph`, the guard absorbs exactly two errors and every graph comes out complete. Without it, `predicted_heat` silently drops and the watch thread dies. Two new specs are killed (hammered and stalled); the original calibration spec still survives its own suite, as designed. The `StopIteration` half is unreachable, since eviction only runs above the cap. |
| T7-5 | two producers, two connections, one store | Recording-sink interleave — never tested beyond sequential sessions on one connection. **Done (C4) — pinned.** Strict interleaving, a free-running pair and an 8 × 2000-event hammer: two producers on one store never cross-contaminate. The shared-recorder mutant was caught by the old suite in only 1 of 7 runs; the interleaved test catches it every time. Writing the hammer surfaced F-10's g, a first-party producer that never reads its socket. |

### T8 — Property and fuzz batteries (gated on P-3)

The agent suite is ~100% hand-picked examples (near-zero parametrize). These surfaces have rich
input spaces and, in two cases, exact oracles:

| Probe | Property / oracle | Fails if |
|---|---|---|
| T8-1: `value_repr.safe_repr` over generated adversarial objects | Bounded output (≤ max_len / max_depth / max_items), never raises, never invokes user code — the canonical property shape, over the package's largest pure surface (708 LOC, 66 excellent but purely example-based tests) | Any generated object breaks a bound or triggers a user `__repr__`. **Done (C5):** 10 properties hold (bounded, never raises; `truncated` false exactly when the text equals `repr()`; exact-fit limits; no leaf past depth/width; generated hostile classes' hooks never called; lazy iterators never advanced; sensitive names redacted before the value is touched); 5 specs killed. **Ledgered F-14 a–d** (4 xfails): the module runs user code it promises not to (`isinstance` reads the instance's `__class__`, a `str`-subclass key's `.lower()`, a class-defined `__dict__` property), and its `<unreprable>` fallback skips the `max_len` clamp. |
| T8-2: `TraceAggregates` metamorphic oracle | For any generated event list and any `at_index`: `cumulative_heat` / `coverage_count` / `top_k` equal a naive linear recount (inequality band where `sparse_k > 1` is documented approximate) | The bisect `at_index - 1` logic disagrees with the oracle anywhere. **Done (C5) — holds.** Every query matches a naive recount at every `at_index`; with `sparse_k > 1`, results match an exact recount of the documented sampling and never exceed the true count; `build_seekable` agrees with `build`; heat and coverage are monotone. 4 specs killed. Confirms C3's note that the `sparse_k` docstring bound ("at most `sparse_k - 1`") is false on dense traces. |
| T8-3: `JsonlIndex` vs `read_jsonl` differential | Same file, both implementations, byte-generated payloads **including U+0085 / U+2028 / U+2029** — the exact `split("\n")` vs `splitlines()` hazard `writer.py:78-83` documents; window concatenation reconstructs the file; absurd windows never raise | The two implementations disagree → seek corruption. **Done (C5).** Both readers return exactly the written events, including raw U+0085/U+2028/U+2029 — the documented hazard holds; windows concatenate back to the trace; absurd windows clamp; `write_jsonl` round-trips byte for byte. 3 specs killed. **Ledgered F-14 e** (1 xfail): the readers disagree on what a *blank* line is (`read_jsonl`'s `str.strip()` vs the index's `bytes.strip()` vs `read_window`'s no strip) for U+0085, U+001C–U+001F, NBSP and U+3000. |
| T8-4: `to_posix` path properties | Never contains `\`; never escapes root; round-trips — generated over unicode, reserved Windows stems (`CON`, `NUL`), trailing dots, long names | Exactly where the Windows-only bug class lives. **Done (C5).** Round-trips on real files (Unicode, reserved stems, trailing dots/spaces, long names; OS-aware — Windows filters what it cannot create), `..` never escapes, symlinks out of root raise and in-root ones resolve; 3 specs killed. **Premise narrowed:** on POSIX a backslash in a filename passes through verbatim, so "never contains `\`" holds on Windows only. **Ledgered F-14 f** (1 xfail): one escaping symlink aborts the *whole* static parse, for all four languages. **Not run on Windows locally** — CI is the first check. |
| T8-5: `diff.py` algebra | `diff(g,g)` empty; A→B and B→A inverse | **Done (C5) — holds.** `diff(A, A)` all `same`; A→B is the exact inverse of B→A; every entry matches a naive recount in severity order; appending events never yields `gone`/`colder`; the static diff equals a trace diff against an empty baseline. 4 specs killed. |
| T8-6: frontend beacon grammar (hand-adversarial extension, no new dep) | Extend the 12.4 adversarial-payload corpus for `FLOAT` / `EPOCH_RET_RE` / the arity-built stats regex — generated-ish sweeps via seeded loops rather than fast-check, keeping the frontend dep surface untouched | **Done (C5).** Seeded sweeps (mulberry32, no new dependency; ~0.3 s) over all three grammars: round-trips against a CPython-verified repr formatter, mutation sweeps against a regex-free reference recognizer, and cross-path consistency. 7 specs, all surviving the pre-existing suite and killed by the sweeps. **Ledgered F-15** as 4 `it.fails`: digit runs of 309+ overflow to `Infinity` in all three parsers; the live network panel's architecture latch disagrees with a one-shot replay of the same trace. |

### T9 — Numerics and the ML envelope

| Probe | Fails if | Status |
|---|---|---|
| T9-1: acceptance-margin telemetry | The bar sits at +0.05 with a **measured single-point margin of ~0.056** — 0.006 of headroom — and both computed metrics are consumed by bare asserts; `top10` is computed and **discarded**. Probe: emit margin + top10 on the passing path (minutes of work); nightly N-seed sweep over `(split_rng, train_seed)` characterizing the margin *distribution* — additive, per ADR-0029's never-unseed rule. Fails if the distribution's lower tail crosses the bar — i.e. the bar is a coin-flip, discovered before CI discovers it for us. **Done (C5) — the finding is worse than a coin-flip.** The passing path now reports margin, headroom and top10 (seeds and bars unchanged; margin bit-identical at +0.05565). A new `scripts/margin_sweep.py` (nightly, report-only) measured 200 joint seed pairs: **91% fall below the +0.05 bar**, median margin +0.008, only 57.5% above zero — the test passes on roughly a 1-in-11 draw, and on held-out graphs the model is statistically indistinguishable from raw in-degree. See **F-12** — an owner decision under ADR-0029's escape hatch. | **Confirmed (C5) — owner decision** |
| T9-2: SoftmaxCE `backward()` at extreme logits | Forward at `|logits|~1e4` is tested for *finiteness only*, one input, argmax-is-correct-class only; **backward at saturation is tested by nothing**, and the gradcheck runs at `standard_normal` magnitude (finite differences are useless at 1e4 — needs the analytic `(probs − onehot)/B` oracle). **Done (C5) — pinned.** Backward at ±1e2..1e4, ties, mixed scales and underflow matches `(softmax − onehot)/B` computed in 60-digit `decimal` from the exact float inputs (rtol 1e-12), plus a hypothesis property; 2 specs killed. | **Done (C5)** |
| T9-3: Adam with non-constant gradients | The only Adam test uses a constant gradient, where bias correction cancels **exactly** — and Adam is the optimizer the shipped `train_heat_model` actually uses; the well-tested SGD is the one the demo uses. Sign-flipping/varying gradient sequences against a NumPy reference implementation, steps 1..N. **Done in C3 via T4-5** — see the T4-5 row (the premise about which bug the old test misses was corrected). | **Done (C3)** |
| T9-4: ReLU/Tanh boundary sweep | `x == 0.0` subgradient convention unpinned (gradcheck *deliberately* excludes the kink, correctly — but nothing else covers it); Tanh saturation/±inf/nan unswept. **Done (C5).** ReLU's subgradient at exactly 0 is 0 (including ±5e-324 and −0.0), nan propagates forward and its gradient is blocked; Tanh saturates to exactly ±1 with slope exactly 0 for |x| ≥ 22, including ±inf; 3 specs killed. **Ledgered F-13 b** (2 xfails): ReLU's multiplicative mask turns `-inf` into nan, and an infinite upstream gradient at an inactive unit into nan. | **Done (C5); 1 defect ledgered** |
| T9-5: `test_train.py:100` window alignment | Pins **final-epoch-only** accuracy while `test_traceability.py:195` uses min-over-last-5 — and the codebase itself documents why final-only is fragile. One line. **Done (C5):** the demo test now asserts `min(acc over the last 5 epochs) >= 0.95` — strictly stronger than final-only (actual: 0.9792 min, 0.9870 final; ≥0.95 continuously since epoch 50). | **Done (C5)** |
| T9-6: checkpoint key-set pin | The `allow_pickle` history (a stray bool array written into every checkpoint on numpy 2.0/2.1) would be caught by exactly one thing — asserting the written npz's **key set** — which no test does. **Done (C5) — pinned.** The exact key/dtype/shape table for `HeatModel.save` (10 keys) and `Sequential.save` (p0..p5), checked through both the ZIP directory and `np.load(allow_pickle=False)`, on direct saves and on the production path (`ml_bridge.train_and_save` → `predict_scores`); stray-key specs killed. | **Done (C5)** |
| T9-7: numpy floor matrix | Declared `numpy>=2,<3`; locked 2.5.2; CI runs `--frozen` everywhere — **the 2.0/2.1 regime the code comments about is exercised by nothing**, `packages/nn` isn't even Dependabot-covered, and only two value-sensitive assertions defend demo convergence against a BLAS change (one of them the weak T9-5). Probe: a `ci-matrix.yml` main-push leg syncing `numpy==2.0.x` / `2.1.x` and running the nn suite. **Done (C5):** two `ci-matrix.yml` main-push legs sync the lock, swap numpy to 2.0.* / 2.1.*, and run the nn suite. Probed first on macOS arm64: 212 passed on both floors (including the new T10-2 golden and the acceptance test), with 276 spurious matmul `RuntimeWarning`s the locked 2.5 does not emit — consistent with numpy <2.2's Accelerate floating-point flags, not a correctness failure. | **Done (C5)** |
| T9-8 (new, C5): the 1e-8 std floor | `train_heat_model` sets `norm_std = max(std, 1e-8)`, so a feature constant in training (e.g. `is_async` in a project with no async functions) reaches the MLP as `(1 − 0)/1e-8 = 1e8` the moment an unseen value appears. With `grackle learn`'s defaults, marking one node async moved its predicted heat from 0.44 to 0.0. Ledgered strict-xfail (`tests/ml/test_standardization_envelope.py`, with a precondition test so it cannot pass for the wrong reason). **Entangled with T9-1:** the obvious fix (scale 1.0 for zero-variance columns) turns the acceptance test red (margin +0.0333), so it cannot land before the T9-1 decision. See F-13. | **Confirmed, ledgered (C5)** |

### T10 — Cross-platform byte discipline

The byte-identity pins that exist are good (both JSONL writers, learn-history, predicted-heat
payload — the last with a discriminating-power companion, the repo's best-constructed pair).
The gaps:

| Probe | Fails if |
|---|---|
| T10-1: server-produced recording bytes | No direct assertion a *recording* is CRLF-free (only transitively via the shared writer). One byte-level check on a real recorded session. **Done (C4) — pinned.** The producer's frames were pretty-printed with CRLF and carried non-ASCII text, an escaped CR LF and a raw U+2028. The server's recording has no `\r` byte and is byte-identical to `write_jsonl`'s output. |
| T10-2: checkpoint reload-equivalence cross-platform | `heat-model.npz` has no byte pin (unattainable — ZIP embeds mtimes) **and no reload-equivalence pin either**: nothing asserts a fixed-seed model trained on OS A predicts identically loaded on OS B. Given the 1-ULP libm history lives exactly in this pipeline, a seeded predict-vector golden (tolerance-banded) is the probe. *Moved to C5 (numerics).* **Done (C5).** A seeded 30-epoch model on a tiny rng-free graph (~6 ms): reload is bit-identical in-process, and 12 interior prediction rows match a committed golden at `atol=1e-6`, with a companion test proving seed±1, epochs±1, batch size and lr×1.1 each move the rows by >1000× the tolerance. Tolerance rationale: emulated BLAS reordering moved the rows ≤7e-15 and ±2-ulp libm noise ≤4.3e-8 (almost all of it through T9-8's floor), while the smallest real change moved them 0.043. Holds on numpy 2.0.2, 2.1.3 and the locked 2.5 locally; Ubuntu/Windows CI is the first cross-OS check. |
| T10-3: `sessions.db` forward-compat | `CREATE TABLE IF NOT EXISTS` + no migration path: open a db created by the previous schema, probe read + write. **Done (C4).** The schema has not changed since 8.3, so no older-schema db exists in the wild; the probe tests the mechanism the next schema change (13.0's `root` column) will hit. A db with extra columns, one of them mid-table, reads and writes correctly (pinned by `SELECT *` and column-list-free INSERT specs). **Ledgered: F-8's e–f** (INSERT OR REPLACE wipes columns this version doesn't know; no migration path, so any added column breaks every existing library). |

### T11 — Frontend rendering and panel hardening

| Probe | Fails if | Status |
|---|---|---|
| T11-1: `GraphCanvas` harness | **543 lines, the largest frontend module, zero tests** — sole owner of the Sigma/ForceAtlas2 lifecycle: three timer/RAF refs, `sigma.kill()` teardown, rebuild-vs-reheat (`hasSurvivor`), three event handlers, a manual RAF loop, theme-reactive repaints. Every collaborator is tested; their composition and every cleanup path are not. Probe: a mocked-Sigma lifecycle suite (mount/update/teardown, timer leak assertions via fake timers, rebuild-vs-reheat decision table). **Done (C6):** `graph/GraphCanvas.test.tsx`, 36 tests. `sigma` and the ForceAtlas2 worker are faked so every call is recorded (graphology is the real library), and the fakes copy the two library behaviours the component relies on: `Sigma.kill()` removes all listeners (the component's only handler teardown), and FA2 `start()` is a no-op while already running and is the only place the `fixed` pins are read. Covered: **mount** (no graph builds nothing; a graph builds exactly one Sigma and one layout with the documented settings and three handlers; the layout stops at 5 s, not before); **unmount** (Sigma and FA2 each killed exactly once, and `vi.getTimerCount()` is 0 afterwards, including mid-reheat and mid-fade); the **rebuild-vs-apply decision table** written out in the file (a disjoint or empty re-push rebuilds and kills the old pair; identical, attribute-only, edge and node add/remove re-pushes are applied in place, and survivors keep their x/y; node removal fades over the rAF loop, or is dropped at once under reduced motion; reheat does nothing during the initial settle, and after it pins survivors, restarts the layout, and unpins at 1.5 s, with a second reheat replacing the first); the three **handlers** and their retirement on rebuild; **repaint** on theme or selection change with no rebuild; **StrictMode**, five interrupted remount cycles, and a key-change remount all end with exactly one live Sigma/FA2 pair and nothing pending. 20 specs (teardown 3, timers/rAF 4, rebuild-vs-reheat 7, handlers 3, theme/repaint 3), all killed, each by at least one passing test rather than only by the new `it.fails`. **No leaked timer or rAF, no missed `kill()`, no handler firing after unmount, no second live instance.** One defect ledgered (F-18 a). | **Done (C6); 1 defect ledgered** |
| T11-2: port `makeRecorder` | `FlameGraphPanel` and `LossCurvePanel` still stub `getContext → null`, so **their entire paint paths run under zero coverage** — the exact defect class 12.4 fixed for NetworkView with `makeRecorder` (`NetworkViewPanel.test.tsx:309-356`), which is directly portable. **Done (C3):** `makeRecorder` extracted to `src/test/canvasRecorder.ts` (NetworkView's use unchanged) with new channels (filled/stroked rects, clears, path points, text position and style). FlameGraph and LossCurve each gained 7 paint-path tests (geometry, labels and clipping, outlines, dimming, gridlines, axis alignment, playhead marker, `devicePixelRatio` scaling, empty state). 13 specs, all killed, and every new paint test is the one that fails under at least one of them. | **Done (C3)** |
| T11-3: vacuous-assert fixes | The T4-5 StatsPanel/CyclesPanel disjunctive regexes → exact-value assertions (top-degree ranking must actually rank). **Done (C3)** — see T4-5. | **Done (C3)** |
| T11-4: store-reset unification | `CyclesPanel.test.tsx` partial-merges a mocked action that persists for the rest of the module — the exact leakage `CausalPathPanel.test.tsx:84-95` defends against with full snapshot-replace + its own regression test. Apply the full-replace pattern to every panel suite. **Done (C3):** a shared `restoreInitialState` helper (`src/test/storeReset.ts`, built on zustand 5's `getInitialState()`, so it does not depend on when a snapshot was taken) is applied to nine panel suites, including the client store in SourceViewer, which had a leaking `sendReadSource` stub. Cycles and ValueInspector gained no-cross-test-leak regression tests; the Cycles one was shown to fail with the reset removed. | **Done (C3)** |
| T11-5: `CausalPathPanel` perf cliff | The Windows CI timeout diagnosed: 199 sequential `getByRole` accessibility-tree scans + full React commits under the default 5s timeout — a genuine performance cliff, not nondeterminism. Fix: hoist the query out of the loop (the button node is stable). **Fixed (C3):** the button query is hoisted out of the loop, with assertions that it stays attached and disabled. The test ran 253–258 ms alone before and 84–86 ms after; inside the full parallel suite, 627–845 ms before and 147–209 ms after. | **Fixed (C3)** |
| T11-6: untested hooks + panels | `useTracePlayback` (timer-driven, zero tests), `panels/init.ts`, `SessionLibraryPanel` (no test exists anywhere), `ConnectionBadge`, `useHeatmap`/`useCallTree`/`useRuntimeCoverage` wrappers. **Done (C6):** `useTracePlayback` (16 tests on fake-timer rAF: first-frame floor and per-speed advance; a speed change mid-play without restarting the loop; the bound growing with a live buffer; one loop under StrictMode; stopping on the exact frame it reaches the end and clamping an overshoot; seekable mode playing to `traceTotal` rather than the end of the loaded window; pause cancelling the pending frame and resuming without jumping by the paused time; no frame surviving unmount). `panels/init` (the full 18-panel slot/order table and id→component wiring, plus every slot used checked against the six `App.tsx` renders via `?raw`, which catches a misspelt slot silently hiding a panel). `SessionLibraryPanel` (no request while disconnected or connecting; one on connect or mount and another after a reconnect; rows, pluralisation, empty and loading states; click sends the id; Refresh replaces the list; error display). `ConnectionBadge` (all three states, `data-status`, dot colour, pulse only when connected, following the store live). `useHeatmap`, `useCallTree` and `useRuntimeCoverage` (override precedence, aggregation, the seekable window-start offset, recompute as events arrive or the graph is replaced, memo stability). 13 specs, all killed. **Ledgered: F-18 b–f** (7 `it.fails`, each with a passing control beside it, and each flipped to a plain `it` once to confirm it fails on its final assertion, not during setup). | **Done (C6); 5 defects ledgered** |
| T11-7: matchMedia stub honesty | The setup stub answers `matches: false` to everything — `prefers-reduced-motion` / `prefers-color-scheme` true-branches never execute in any test (the 10.7 animation suppression is untested in the direction it exists for). **Done (C6).** **Premise corrected:** the stub was less blind than the row says. `graphAnimation.test.ts` already reached `prefersReducedMotion`'s true branch through `stubGlobal`; what had never run was the true branch of the *`GraphCanvas`* caller and `useTheme`'s light branch. A new `src/test/matchMedia.ts` makes the stub controllable (`setMatchingMediaQueries(...)`, `resetMatchingMediaQueries()`, `mediaQueryMatches(q)`, the `REDUCED_MOTION_QUERY`/`PREFERS_LIGHT_QUERY`/`PREFERS_DARK_QUERY` constants, `installMatchMediaStub()`/`resetMatchMedia()`), keeping the default `matches: false` for everything: matching compares the whole query ignoring whitespace and case, `matches` is a live getter, the state survives `vi.resetModules()`, and the property descriptor is the original one, so `spyOn`/`stubGlobal` behave as before. `setup.ts` now resets it after every test. **Hazard found and closed:** `vi.spyOn(window, "matchMedia")` returns the setup file's shared `vi.fn` itself, so a `mockImplementation` on it leaked into every later test in that file and `vi.restoreAllMocks()` did not undo it (it had masked F-18 a in T11-1's own harness until that test swapped the property instead); the per-test reset ends the leak, with leak-pair meta-tests for both patterns. New tests: `GraphCanvas.reducedMotion.test.tsx` (a watch-mode re-push under reduced motion drops removed nodes in the same commit, starts no rAF loop, and paints new nodes at full size with no flash and new edges with no pulse, each with a default-motion control) and `useTheme.colorScheme.test.ts` (an OS light preference gives a light theme and `data-theme`; a stored theme beats it; an unrecognised stored value is ignored; a dark-only match stays dark). 5 specs, including one that changes `reduce` to `no-preference` — which T11-1's own stub, matching on `query.includes("prefers-reduced-motion")`, answers true either way and so cannot catch. | **Done (C6)** |

### T12 — Live-system end-to-end probes

The phase-0 tradition (real server, real browser, real protocol), updated for the v0.12 stack.
Manual-driven via the preview browser, like prior campaigns; Playwright automation deliberately
deferred (a ~300MB dependency for a separate decision).

| Probe | Fails if |
|---|---|
| T12-1: full-pipeline | `parse → trace → serve → browser` on `tiny-python-app` + the nn demo: heat, flame, timeline, ValueInspector, LossCurve click-to-seek, NetworkView phases — against the *known* T5 ledger (e.g. the epoch-boundary chip reading "loss" is expected-broken; anything *else* wrong is a new finding). **Done (C6), in the in-app browser against a real server.** *tiny-python-app*, freshly traced with `--capture-values`: heat, the 49-event timeline, playback, the flame graph (24 frames), the cycles panel, and the value inspector (event 7: `call is_even`, depth 3, `n = 1`) all work. *nn demo* (25,870 events): the loss curve draws and click-to-seek moves the playhead; the network view header is right (`2-32-32-3 · epoch 29 · loss 0.0729 · acc 0.974`) and its chip reads "loss" at the epoch boundary — **the known 12.4 defect, as expected**. Two new findings: **F-16** (the graph column collapses to 0 px at a 1024-px viewport) and **F-17** (in a seekable session, no playhead move except the scrubber loads the event window, so the value inspector goes blank during Play and after click-to-seek). |
| T12-2: watch + learn live | `serve --watch --model`: edit a file mid-session — predicted_heat survives the re-push (the 12.2 regression test's claim, verified live), positions/camera survive (10.7), a retrain mid-serve is picked up without restart. **Done (C6).** `serve --watch` on a scratch copy of tiny-python-app with a trained model. Editing `main.py` mid-session re-pushed the graph (5 → 7 nodes) with `predicted_heat` intact; surviving nodes kept their relative layout and drifted slightly as the layout reheated, and the camera re-fit — Phase 10.7's reheat design, not a defect. Retraining mid-serve was picked up on the next re-push without a restart (`helper` went from 1.0000 to 0.0982). **Live evidence for F-13a (T9-8):** before the retrain, both brand-new functions — an async one and a plain one nothing calls — were predicted at 1.0000, the hottest in the project, because features constant in training reach the model scaled by 1e8. |
| T12-3: protocol edges against the live server | The T6-3 battery driven over a real socket: oversized frame, binary frame, malformed envelopes, event-after-end, two producers, kill-a-consumer-mid-stream. **Done (C6) — robust.** Over a real socket against a running server, with the browser connected throughout: a binary frame and six malformed envelopes (non-JSON, missing id, int id, bare array, 5000-deep nesting, unknown type) got no reply, and the same connection still answered a ping. A 2 MiB frame got a clean 1009 ("frame exceeds limit of 1048576 bytes"). Two concurrent producers, plus a consumer killed mid-stream (TCP abort), plus two events after `session_end`: the surviving consumer received all 106 messages, and the server answered a health ping in ≤ 8 ms after every probe. Observation, not a defect: the browser merged the two concurrent sessions into one 102-event timeline (`trace_event` carries no session id and the UI assumes one live session), while the recording sink keeps them separate per connection (T7-5). |
| T12-4: crashed-run recovery UX | SIGKILL a `trace -o` run; verify the `.part` story end-to-end: error message accuracy, salvage, the T5-1 bricked-path scenario. **Done (C6) — passes end to end with real signals.** SIGKILL of a real `trace -o` run left the `.part` with 370,512 complete lines and **no torn tail** (consistent with T5-5). The re-run refusal message accurately describes the killed-run case. The `.part` salvages cleanly through `grackle diff` and `build_seekable`. A real SIGINT at 1 s and at 2 s into a 6.5 s cold parse (2,081 files) gave "Aborted!", **no `.part`**, a previous trace at `-o` left untouched, and a clean re-run — the T5-1 fix verified outside the test harness. SIGINT mid-run finalized the trace (exit 0), ending on the `KeyboardInterrupt` exception event. |

### T13 — Docs and config integrity

Following the phase-1 T8 tradition: claims vs reality.

| Probe | Fails if |
|---|---|
| T13-1 | CLAUDE.md's "path-discipline lint test" claim (doesn't exist until T3-4 lands); `packages/nn` missing from `dependabot.yml`; ADR cross-references for 0029/0030 resolve. |
| T13-2 | Acceptance-grid claims spot-audit: every "automated" cell names a test that actually runs (T2-1 proves at least one doesn't). |

---

## Execution plan (chunks, in order — one PR each, stop for review after each)

| Chunk | Contents | Cost gate |
|---|---|---|
| **C0** | Prerequisites P-1..P-4 + T13 doc fixes | PR gate (trivial) |
| **C1** | T3 guard-of-the-guards (parity meta-test, path-discipline lint, codegen probes) + T2 census — **done, with three items explicitly deferred** (see tier tables above for per-probe outcomes): T2-2's per-OS skip-count assertion, T3-6(a) the typo'd-`$ref` degradation probe, and T3-6(b) a cross-version codegen determinism guard | PR gate — all sub-second, Ubuntu shadow |
| **C2** | T5 expected-fail ledger — **done**: all six probes executed against the real system (see the T5 table for per-probe outcomes). T5-1 and T5-2 confirmed, fixed, and promoted in the same PR; T5-4, T5-6's retry case, and the new T5-7 ledgered strict-xfail; T5-3 pinned as documented behavior; T5-5's premise refuted and the tolerance it doubted pinned. Every passing pin is backed by a committed mutation spec (8 new, all killed). Findings F-2–F-7 below — F-7 is a defect in C0's own mutation harness, found and fixed during this chunk | PR gate |
| **C3** | T4 mutation battery + T11-2..T11-5 — **done**: 95 new committed specs (106 in total), all behaving as declared in the full sweep. Of the 78 T4-1..T4-3 mutants, the pre-existing suites killed 60; the other 18 exposed real test gaps, each closed by an additive test proven to fail under its mutant. The 17 panel specs back new or strengthened tests: 13 cover paint paths that had no coverage at all before T11-2, and 4 are ranking mutants that survived the old StatsPanel/CyclesPanel tests. The 12.4 "30/30" claim mostly reproduces (31/34 re-specified). The Adam premise was corrected (see T4-5). One product defect was ledgered (T5-8). `pnpm mutation:check` is now in the PR gate and in pre-push; the full sweep (`pnpm mutation`) goes to C5's nightly workflow | Harness runs nightly; specs' *presence* checked at PR gate |
| **C4** | T6 fault injection + T7 concurrency (+ T10-1 and T10-3, which the original plan assigned to no chunk) — **done**. 25 product defects confirmed and ledgered as 58 strict xfails (F-8–F-11; 60 on Python 3.14, where F-11 #10 also applies); every correct behavior pinned, 40 new mutation specs, most surviving the pre-existing suites. Four premises corrected: T6-1's shutdown race needs a *second* interrupt; T6-4's rebuild serialization has two independent mechanisms; T7-2's lost update cannot occur on GIL builds; and the T4-4 "unreachable" guard is reachable (now pinned). ADR-0027 amended. Hammers are marked `@pytest.mark.hammer` and deselected by default; each has an unmarked small sibling in the gate | Fast cases PR gate; hammers nightly |
| **C5** | T8 property batteries + T9 numerics + T10-2 + F-1 + the nightly `campaign.yml` — **done**. P-3 approved: `hypothesis` added dev-only, with a derandomized "ci" profile in the gate and a 5000-example "nightly" one. 27 agent properties and 11 frontend sweeps hold; 38 new mutation specs, all killed. F-1 is fixed, and ADR-0022 is amended to match. The T9-7 numpy-floor legs are in `ci-matrix.yml`. New defects are ledgered as F-13, F-14 and F-15; **F-12 needs your decision**: the ADR-0029 acceptance bar passes on about one seed pair in eleven. `campaign.yml` runs the mutation sweep, the hammers, the nightly property profile and the margin sweep | Nightly + main-push |
| **C6** | T11-1 GraphCanvas harness, T11-6/7, and T12 live-system probes — **done**. 38 new mutation specs (20 GraphCanvas, 18 hooks, panels and matchMedia; 222 in total), all killed, each caught by a passing test. Frontend: 886 → 1018 passing (+132), 13 → 21 `it.fails` (+8), identical across three runs of the merged tree. T12 was run by hand in the in-app browser against real servers (T12-1..T12-4), with the results recorded in each row. **Three findings**: F-16 (the graph column collapses to 0 px at a 1024-px viewport) and F-17 (a seekable replay's value inspector goes blank during Play and after a loss-curve seek) are the two that matter; F-18 is six component-level defects, ledgered as eight `it.fails` tests. The T5-1 fix was also verified with real signals (T12-4) | Manual + PR gate |

## Findings

*(Populated as tiers execute. Seeded findings above carry audit-derived citations; each is
confirmed by executing its probe before being written up here in the phase-0/1 format:
Location / Reproducer / Observed / Expected / Fix / Severity / Recommendation.)*

### F-1 — An exact-count assertion on V8 coverage is load-dependent, and flakes Windows CI

**Location.** `packages/agent/tests/node_runtime/test_e2e.py:95`
(`test_coverage_emits_live_heat`), against fixture `fixtures/tiny-node-app`.

**Reproducer.** Observed in the wild rather than constructed: CI run `32436999328`,
`windows-latest / py3.12`, on PR #85 — a PR whose diff touches only `tools/mutation/`, docs,
`dependabot.yml` and pytest config, none of which CI executes on this path. The same commit
passed on `windows-latest / py3.13` and both Ubuntu legs. Re-running the failed job alone
turned it green.

**Observed.** `assert 130607 == 2000000` — V8's precise-coverage count for `src/math.ts:add`
came back at roughly 6.5% of the true call count.

**Expected.** The test asserts `metadata["count"] == 2_000_000` exactly, because
`main.ts` calls `busy(2_000_000)` and `busy` calls `add` once per round.

**Root cause.** `fixtures/tiny-node-app/src/math.ts` already documents the mechanism in its own
trailing comment: TurboFan **inlines `add` into `busy`** once the loop is hot. V8's
precise-coverage counter does not increment for inlined call sites, so the reported count is
however many calls were made *before the JIT tiered up* — a function of runner speed and load,
not of program semantics. 130,607 is simply where that runner happened to optimize.

This assertion also contradicts its own file's stated contract. The module docstring at
`test_e2e.py:7-8` reads: "Assertions are robust to sampling non-determinism: structure (which
nodes appear, call/return balance, depth, no leaked non-project frames) rather than exact
counts." Line 95 is the one assertion in the file that violates it.

**Fix.** Replace exact equality with the invariant that actually holds under inlining — a lower
bound plus an upper bound of the true count, e.g. `0 < metadata["count"] <= 2_000_000`, or assert
`count` is present and integral and move the exactness claim to a non-JIT-sensitive fixture.
Do not "fix" it by disabling the JIT: the adapter's fidelity contract (ADR-0022) is about what
grackle reports under *normal* Node execution, and inlining is normal.

**Severity.** Medium. It is not a product defect — the adapter behaves correctly — but it puts
red CI on unrelated PRs, which is the precise failure mode that trains a team to ignore the gate.
It also predates this campaign; no PR in flight introduced it.

**Status: fixed in C5.** `test_coverage_emits_live_heat` now asserts that `count` is a positive int bounded by the program's true call counts (`add` ≤ 2,000,000; `fib` ≤ 2·F(31) − 1), with a guard that fails if `main.ts` stops making those calls. It does not disable the JIT. Under 24-way concurrent load the old exact assertion failed 60 of 96 runs; the new one passed 96/96. Three specs (count dropped, zeroed, or swapped with the timestamp) are killed, each by a different assertion. The class-wide audit below found no other assertion of this kind. ADR-0022 is amended: the live path's counts are a lower bound, exact only for functions V8 never inlines.

**Recommendation (original).** Fold into **T9** (numerics and the ML envelope) as an exact-vs-tolerance
audit: grep the agent and nn suites for equality assertions on any quantity produced by a
sampling profiler, a JIT-instrumented counter, or a wall-clock timer, and convert each to the
invariant that survives optimization. This finding is one instance of a class.

### F-2 — A Ctrl-C outside the traced script's frames orphans the `-o` `.part` (T5-1)

**Location.** `packages/agent/src/grackle/cli.py` (the incremental `-o` block and the `--stream`
tee block of `trace`), `packages/agent/src/grackle/python_runtime/tracer.py` (`Tracer._start`,
`Tracer._emit`).

**Reproducer.** Monkeypatch a `KeyboardInterrupt` into each window in turn —
`PythonRuntimeAdapter._build_tracer`, `sys.monitoring.register_callback` (inside `_start`),
`Tracer._stop`, and the eighth `JsonlPartWriter.write` call — then run
`grackle trace script.py -o trace.jsonl` twice.

**Observed.** Every window: exit 1 ("Aborted!"), `trace.jsonl.part` left behind, no
`trace.jsonl`, and the second run refused with "`trace.jsonl.part` already exists — either another
trace is writing this same -o path right now, or a previous run was killed" — neither of which
happened. The `_start` window additionally left `sys.monitoring` tool 3 registered, so every later
`Tracer` in the process would fail at `use_tool_id()`. And a traced program that *caught* the
`KeyboardInterrupt` and carried on still had it re-raised by the tracer after it finished.

**Expected.** Which frame a Ctrl-C lands in is a race the user can neither see nor control, so the
outcome must not depend on it: the existing pinned case (an interrupt raised in the script's own
frame) finalizes the trace and exits 0, and the other frames should match it. An interrupt before
any event was captured should leave `-o` exactly as it was.

**Fix (applied in C2).** See the T5-1 row: `_emit` latches only `Exception`; `_start` releases the
tool on an interrupted setup; the CLI settles the `.part` (finalize if any events, discard if none)
before re-raising an interrupt that still escapes.

**Severity.** High. A Ctrl-C is the ordinary way to stop a long trace, and it most often lands in
the parse (the longest window) or in the sink (where a hot program spends much of its time). Before
the fix, the most common way of stopping a trace left the user unable to trace to the same path
again, with an error message that misdiagnosed why.

**Recommendation.** Done. Keep the six pins; the script-frame test remains the oracle the other
windows are held to.

### F-3 — `serve()` has no readiness signal; every server test raced its own bind (T5-2)

**Location.** `packages/agent/src/grackle/server.py` (`serve`), and 27
`create_task(serve(...)); await asyncio.sleep(0.05)` sites across 11 test files.

**Reproducer.** Wrap `server._ws_serve` in a context manager that sleeps 100 ms before binding,
then start the server the old way and connect.

**Observed.** Connection refused 10 times out of 10 (`ConnectionRefusedError(61, ...)` on macOS;
`[WinError 1225]` is the same condition on Windows — the diagnosed `test_two_sessions_back_to_back`
flake).

**Expected.** A caller can wait until the socket is listening.

**Fix (applied in C2).** `serve(ready=...)` resolves an `asyncio.Future[int]` with the bound port
once listening, and binding port 0 now works. Tests start servers through a new `start_server`
fixture that waits on it, which also retires `free_port`'s probe-then-rebind window.

**Severity.** Medium. Test-only in effect — no product caller needs the signal today — but it was
a real, recurring red-CI source on Windows, the failure mode that trains people to re-run instead
of read.

**Recommendation.** Done. New server tests should use `start_server`; a bare
`create_task(serve(...))` followed by a sleep is now the anti-pattern.

### F-4 — A full disk surfaces below the write buffer, so the salvage guard never fires (T5-4)

**Location.** `packages/agent/src/grackle/python_runtime/writer.py` (`JsonlPartWriter.write`,
`JsonlPartWriter.finalize`).

**Reproducer.** Replace the writer's handle with `io.BufferedWriter` over a raw layer that accepts
1000 bytes, short-writes the crossing chunk, then raises ENOSPC; write 40 events; call
`finalize()`. (`test_part_writer_disk_full_below_the_buffer_leaves_only_complete_lines`, two
parametrizations.)

**Observed.** All 40 `write()` calls return normally (`count` 40, `broken` False,
`_last_good_offset` 4320). `finalize()` raises ENOSPC at `close()`. The `.part` holds 1000 bytes
ending mid-line.

**Expected.** The surviving file holds only complete lines, and `count` — what the CLI reports as
"wrote N events" and `RecordingSink` registers as `event_count` — matches them.

**Fix.** Not applied (ledgered). The bookkeeping tracks bytes handed to the buffer, not bytes the
kernel accepted, and salvage cannot run through the buffered handle (`truncate()` flushes first).
Candidate directions: track the offset of the last complete line actually flushed (flush-aware
accounting), or on failure truncate the raw file descriptor back to its last newline. Either way,
avoid a syscall per event on the hot path (ADR-0020).

**Severity.** Medium. Disk-full is uncommon, but when it happens the server-side recording is thrown
away wholesale instead of salvaged, and the CLI's `.part` ends torn — exactly the case the salvage
design exists for, and the only one it cannot handle.

**Recommendation.** Fix in its own chunk; the two strict xfails are already waiting to promote.

### F-5 — A retried `finalize()` on a broken writer raises `ValueError` (T5-6)

**Location.** `JsonlPartWriter.finalize` (`writer.py`).

**Reproducer.** Break the writer with a failed write, make the first `.part` rename fail, call
`finalize()` twice.

**Observed.** The first call raises the rename's `PermissionError` as documented; the retry raises
`ValueError: truncate of closed file`.

**Expected.** The documented contract — "Raises OSError and leaves `.part` in place if any step
fails" — holds on a retry too, or the retry completes the rename.

**Fix.** Not applied (ledgered). Skip the truncate on a retry (it already ran before the close), or
record which steps completed.

**Severity.** Low — latent. No caller retries today; the first caller written to the documented
contract would crash on the undocumented exception type.

**Recommendation.** Fix alongside F-4, which reworks the same method.

### F-6 — A salvaged `.part`'s torn tail is counted as an event (T5-7)

**Location.** `packages/agent/src/grackle/python_runtime/jsonl_index.py` (`JsonlIndex.build`),
`python_runtime/aggregates.py` (`build_seekable`).

**Reproducer.** Five complete JSONL lines plus a sixth cut mid-UTF-8-sequence; `build_seekable`.

**Observed.** `len(index) == 6`. That length is what `serve --trace-source` and session load send
as `trace_session_end.event_count`, and what the timeline takes as the trace's total; a seek to the
final slot returns nothing.

**Expected.** 5.

**Fix.** Not applied (ledgered). See the T5-7 row: skip an unterminated final line that fails to
parse, and leave mid-file slot alignment as it is.

**Severity.** Low. Off by one, and only on salvaged files — which after the T5-5 probe means
chiefly after a full disk (F-4).

**Recommendation.** Fix with or after F-4.

### F-7 — The mutation harness could run the wrong bytecode, in both directions

**Location.** `tools/mutation/runner.mjs` (C0's harness, prerequisite P-2).

**Reproducer.** Run a same-size spec — `agent-part-writer-rename-before-close` swaps two lines —
several times back to back, importing the target between runs, then disassemble the loaded
`JsonlPartWriter.finalize`.

**Observed.** Found by accident: after C2's full sweep, the pre-push gate failed four writer tests
on a clean tree. `git status` was clean and the source was correct, but the loaded `finalize()`
called `replace` before `close` — the mutant. Old runner, three back-to-back runs: the mutant left
running from `__pycache__` in 2 of 3. The first fix (purge after restore) then exposed the reverse
case: 2 of 3 runs reported a false *survivor*, because the original's freshly compiled bytecode
still "matched" the just-written mutant, so the suite never ran the mutant at all.

**Expected.** The code under test is exactly the source on disk: the mutant during the suite, and
the original afterwards.

**Root cause.** CPython reuses a `.pyc` while the source's mtime — recorded in whole seconds — and
size both match. A same-size mutant written or restored within the same second as the cached
bytecode cannot be told apart from it.

**Fix (applied in C2).** The runner deletes the target's `__pycache__` entries after writing the
mutant and again after restoring it. Afterwards: four back-to-back runs, all killed, all clean.
Only one spec in the repo is same-size today, but nothing stops the next one.

**Severity.** High for the instrument, even though the product is unaffected. The harness exists to
certify outcomes, and this let it do both wrong things silently: certify a false survivor, and leave
a live mutant behind that `git status` cannot see — the outcome its own README calls the worst thing
it can do.

**Recommendation.** Done. When C3 adds the nightly sweep, keep the source-restore check that is
already there, and add a check of the loaded code too — for example, re-importing each Python
target after the sweep and comparing its compiled code with a fresh compile of the source.

### F-8 — Session store: six fault-path defects (T6-1, T10-3)

Every row is a strict xfail in `packages/agent/tests/test_session_store_faults.py` or
`test_cli_store_faults.py`, asserting the correct behavior; each fails for its stated reason
under `--runxfail`.

| # | Defect | Location | Fix direction | Severity |
|---|---|---|---|---|
| a | A corrupt `sessions.db` crashes `serve --store` / `learn --from-store` with a raw `sqlite3.DatabaseError` traceback (file left untouched) | `cli.py` — both `SessionStore.open` call sites | catch `sqlite3.Error`, raise a `ClickException` naming the file | Low — no data lost; looks like a crash |
| b | A second Ctrl-C while the server drains (websockets' 10 s close timeout) drops a finalized recording's row: `.jsonl` on disk, "Cannot operate on a closed database" logged | `serve()`'s `finally` closes the store; `recording_sink.py` swallows the error | await in-flight finalizes (shielded, bounded) before `store.close()`; a startup pass registering row-less `recordings/*.jsonl` would also recover other lost saves | Medium-low — the 10 s hang invites exactly the second Ctrl-C |
| c | A store read error in `session_list_request` / `session_load_request` escapes the receive loop and closes the client with 1011 | `server.py` receive loop — unlike `trace_query_request`, no try/except | wrap both branches | Medium — the panel lists sessions on every connect with no auto-reconnect, so an unreadable store makes the UI unusable |
| d | `session_load` guards `source_path` with `exists()`, not `is_file()`: a directory or `""` replays an empty session, and a FIFO blocks an executor thread (a real `grackle serve` was still up 20 s after Ctrl-C) | `server.py` session-load branch (`learn` already uses `is_file()` for this reason) | `is_file()` + warning | Low-medium — needs a bad row; the FIFO case blocks shutdown |
| e | `INSERT OR REPLACE` resets columns this version doesn't know (a newer build's `root`/`tags`) on every re-save — and `serve --store --trace-source X` re-saves X on every start | `session_store.py` `save_session` | `INSERT … ON CONFLICT(id) DO UPDATE SET <known columns>` | Low, latent |
| f | No migration path: `CREATE TABLE IF NOT EXISTS` no-ops against an older table, `user_version` is never stamped, and every call then fails with "no such column" (triggering c, and losing every live recording's row) | `session_store.py` `_DDL`/`open()` | stamp `user_version`; ALTER-based migration keyed on it | Low today; **high the moment any column is added** (13.0's planned `root`) |

**Recommendation.** Fix a, c and d together (small, contained). Land f before 13.0 adds
`root` — it is the prerequisite, not a follow-up. b and e ride along with f.

### F-9 — Watch mode: a transient file absence loses or stalls the rebuild (T6-4)

| # | Defect | Location | Fix direction | Severity |
|---|---|---|---|---|
| a | A file listed by the walker but gone by its `CacheManager.get` hash raises `FileNotFoundError` out of `walk()`; `_build_static_graph` returns `None`, so the rebuild is dropped and a client connecting at that instant gets no `static_graph` (2 xfails: Python and tree-sitter walkers) | `cache.py` `CacheManager.get` → `_hash_file`, called before the walkers' own `except OSError` | treat `OSError` from `get` as a vanished file | Low-medium — realistic during `git checkout`, codegen, or rename-aside saves |
| b | A file that disappears and reappears during a rebuild leaves every client stale until some unrelated edit, because the watcher has already advanced its snapshot past the triggering edit when `_watch_loop` drops the failed rebuild with `continue` (1 xfail). Fixing a alone does not fix it — simulated: the test still fails, now with the file missing from the graph | `server.py` `_watch_loop` | a failed or partial rebuild must mark its paths dirty for the next tick | Low-medium — silent, unbounded staleness |

### F-10 — Live ingest: seven defects in the server's receive path (T6-2, T6-3, T7-5)

Strict xfails in `test_orphan_sweep_faults.py` and `test_server_ingest_faults.py`.

| # | Defect | Location | Fix direction | Severity |
|---|---|---|---|---|
| a | An orphan `.part` the server may not delete (read-only dir; on Windows, held open by another server) raises out of `serve()` before the bind — raw traceback, store left unclosed (2 xfails) | `sweep_orphaned_recordings` — the `unlink` is unguarded, and runs before `serve()`'s `try` | catch `OSError` like the `stat()` branch; move pre-bind work inside the `try` | Low-medium — the whole server fails to start |
| b | A peer server's startup sweep deletes an **actively written** recording: buffered writes leave the `.part` at 0 bytes with an unchanged mtime, so the owner's rename fails and the whole session is discarded | the sweep's mtime heuristic + `JsonlPartWriter` buffering | an ownership signal the sweep honors (an OS lock), or a coarse mtime refresh — not a per-event flush (ADR-0020) | Low-medium — needs a shared store dir, but the loss is silent and total; on 3.14 the 128 KiB buffer makes a slow recording look orphaned for most of its life |
| c | A `session_load_request` flood starves every default-executor user: a live session's `.jsonl` lands but its row is withheld until the loads finish | `_receive_loop`'s untracked `create_task(load_stored_session(...))`; `build_seekable` on the shared default executor | bound in-flight loads per connection, keep task references, a dedicated bounded executor | Low-medium |
| d | Concurrent loads of one session each build the whole index (8 builds instead of 1) | `file_replay.load_stored_session` — check, await build, cache after | cache the in-flight build, or lock per session id | Low — it is the mechanism behind c |
| e | One stalled consumer freezes **all** live ingest — other consumers, the ring buffer and the recording — and nothing ends it: the keepalive's own ping blocks in the same `drain()` | `live_buffer.broadcast`'s sequential `await ws.send` inside the producer's receive loop, before the recording write | per-consumer bounded queues with a writer task each (or websockets' non-blocking `broadcast()`); write the ring buffer and recording before fan-out | Medium — total and unbounded blast radius |
| f | During that stall the keepalive kills the *healthy producer* (1011, pongs unread) while the stuck consumer stays open; the recording is finalized short (18 of 64 events observed) | same as e | e's fix, plus a send timeout that closes the stuck consumer | Medium |
| g | `grackle trace --connect`'s post-run replay never reads its socket, so a ring-buffer history push stalls the close handshake: a second run within 60 s took 10.12 s (closed 1006) instead of 0.13 s | `cli.py` `_stream_events_to_server` — no inbound drain, unlike `TraceStreamSender._recv_drain` | drain inbound frames concurrently | Low-medium — deterministic and user-visible on the default path |

**Recommendation.** e and f are the priority, one fix. g is a small, isolated fix. a and b share the
sweep and belong together.

### F-11 — Parser robustness: ten ways a malformed input loses a whole file (T6-5)

All Low severity: grackle's own writers and the real toolchains never produce these inputs, and
each failure loses a whole file, trace or session rather than corrupting data. 38 strict xfails
on Python 3.12/3.13 (36 agent in `test_malformed_corpus.py`, 2 nn); #10 adds 2 more on 3.14.

| # | Defect | Location |
|---|---|---|
| 1 | A >4300-digit integer or 100k-deep nesting raises `ValueError`/`RecursionError` past the per-line tolerance (`grackle diff` tracebacks; `serve` disables seek for the file; `read_window` fails every window containing it) | `aggregates.py` builders, `jsonl_index.read_window`, nn `heat_from_jsonl` |
| 2 | A non-string `node_id` (number, bool, array, object) enters the aggregates: arrays/objects raise `TypeError`; numbers become keys that later break `top_k` and `grackle diff` — sibling of T5-8; the nn mirror already skips it | `aggregates.py` builders |
| 3 | `read_window` skips a VT/FF-padded line that the aggregates count, so seek and heat disagree on whether a slot is an event | `jsonl_index.read_window` |
| 4 | `read_jsonl` splits on a raw CR (universal newlines), contradicting its own `\n`-only docstring | `writer.read_jsonl` |
| 5 | `parse_textfmt` raises on an over-long number instead of skipping the line as documented | `go_runtime/covdata_parse.py` |
| 6 | `parse_export` raises `RecursionError` on a deeply nested document instead of returning `[]` | `rust_runtime/llvm_cov_parse.py` |
| 7 | `parse_export` keeps negative counts, contradicting its own comment and `RustCoverFunction.count` | `rust_runtime/llvm_cov_parse.py` |
| 8 | `iter_coverage_deltas` crashes on non-object entries, non-list fields or an `inf` count, aborting the `--stream` session its own `_as_int` docstring says must survive | `node_runtime/coverage_poll.py` |
| 9 | The V8 sampling pipeline crashes on malformed ids, time deltas, callFrames or function names, losing the whole sampling trace | `node_runtime/profile_reconstruct.py`, `launcher._make_resolve` |
| 10 | On Python 3.14, a `file://` URL with a remote host makes `url2pathname` raise `URLError` outside `_normalize`'s `try` (3.12/3.13 drop it correctly; CI tests only those, but a local uv env resolved 3.14) | `node_runtime/node_resolution.py` |

**Recommendation.** One "parse defensively" chunk: a shared per-line decode helper (catching
`ValueError` + `RecursionError`, requiring a dict with a string `node_id`) fixes 1–4 and T5-8
together; 5–10 are one-line guards each.

### C4 observations (not ledgered)

- `learn --from-store` opens the store read-write, so pointed at an unrelated SQLite file it would
  switch it to WAL and add a `sessions` table (found by reading; not run).
- `profile_reconstruct` memory grows with the square of stack depth: a 10k-deep recursion peaks
  at 414 MiB; its docstring says O(nodes).
- CPython 3.14: an asyncio `_SelectorSocketTransport` can close via its drain path without
  counting the loss, so a later websockets `abort()` hits `_loop is None`. Hit by a test client,
  worked around in the test; server-side exposure not investigated.
- Live ingest runs at about 0.46 ms per event with no consumers attached (~2k events/s). Not
  investigated.

### F-12 — The ADR-0029 acceptance bar passes on roughly one seed pair in eleven (T9-1)

**Location.** `packages/nn/tests/ml/test_synthetic_acceptance.py` (now via the shared
`tests/ml/acceptance_eval.py`), against the bar ADR-0029 set: mean model Spearman > 0.5 **and**
at least +0.05 over the raw-in-degree baseline, on held-out synthetic graphs.

**Reproducer.** `cd packages/nn && uv run python scripts/margin_sweep.py --seeds 200 --json out.json`
(about a minute). The nightly campaign workflow runs it and publishes the distribution.

**Observed.** At the test's own draw (split 0, train 0) the margin is +0.0557 — 0.006 of
headroom. Over 200 joint seed pairs:

| Sweep | n | min | p5 | median | mean | max | below +0.05 |
|---|---|---|---|---|---|---|---|
| joint (k, k) | 200 | −0.192 | −0.065 | +0.008 | +0.002 | +0.095 | **91.0%** |
| train seed only (0, k) | 100 | −0.062 | −0.053 | −0.008 | +0.009 | +0.092 | 68% |
| split seed only (k, 0) | 100 | −0.059 | −0.028 | +0.005 | +0.007 | +0.066 | 93% |

Only 57.5% of joint draws beat the baseline at all, and 187 of 200 fall below the test's own
draw. No draw misses the absolute bar (Spearman > 0.5); every failure is the margin. Platform
noise is not the risk (emulated BLAS reordering and 1-ulp libm noise left the (0, 0) margin
unchanged in 24/24 runs, consistent with CI passing on every OS); real changes are — dropping
the minibatch shuffle gives +0.0455, and T9-8's natural fix gives +0.0333, both failing.

**Expected.** A bar that a correct model clears on most draws, so that the test detects
regressions rather than seed luck. The signal exists: a model that knew the generator's
noiseless formula beats the baseline by +0.102 on average; the trained model does not
capture it.

**Fix.** Not applied, and not ledgered as an xfail — this is an owner decision under
ADR-0029's escape hatch (the seeds are never changed). Options: evaluate over k-fold splits and
bar the mean; lower the margin bar to what the distribution supports; or treat it as a model-
quality finding and improve the model/features until the bar holds on most draws.

**Severity.** High for what the test claims, not for the product: the acceptance test is the
evidence that `predicted_heat` beats a trivial baseline, and today that evidence is one
favorable seed.

### F-13 — Numerics: two defects in the nn package (T9-4, T9-8)

| # | Defect | Location | Fix direction | Severity |
|---|---|---|---|---|
| a | **The 1e-8 std floor**: a feature constant in training reaches the MLP as ~1e8 when an unseen value appears; one async node's predicted heat went 0.44 → 0.0 (T9-8) | `grackle_nn/ml/heat_model.py` `train_heat_model` | scale 1.0 for zero-variance columns (existing checkpoints keep 1e-8 and need re-learning) — but this turns the acceptance test red, so it waits on F-12 | **Medium** — silently wrong predictions for async functions, decorators, dunders, inherit or cross-language edges absent from training, under `serve --watch` or when a model scores another project |
| b | ReLU's multiplicative mask: `ReLU(-inf)` is nan, and an infinite gradient at an inactive unit comes back nan (2 xfails) | `grackle_nn/layers.py` ReLU | `np.maximum(x, 0.0)` / `np.where(mask, grad, 0.0)` — verified to flip exactly those two tests with the rest of the suite (golden traces included) green | Low — latent; needs an already-diverged activation |

### F-14 — Agent: safe_repr runs user code; a symlink aborts the parse (T8-1, T8-3, T8-4)

All strict xfails with the shrunk counterexample as an `@example`.

| # | Defect | Location | Fix direction | Severity |
|---|---|---|---|---|
| a | `isinstance` checks in the dispatch read the *instance's* `__class__`, so a `__class__` property or `__getattribute__` override runs (5× per value), and if it raises the whole enclosing value becomes `<unreprable>` — the module docstring names this exact trap as avoided | `python_runtime/value_repr.py` `_repr1_dispatch`, `_repr_dict_safe` | test `type(x)` with `issubclass` | Low-medium — reachable through common proxies (Django `SimpleLazyObject`/`LazySettings` get force-evaluated; wrapt, werkzeug) and any captured `self` overriding `__getattribute__`; capture is opt-in |
| b | `is_sensitive_name(key)` calls a `str` subclass key's own `.lower()` | same module | `str.lower(key)` | Low |
| c | `_read_dataclass_field` trusts a class-defined `__dict__` property (output shows `DC(a=-1)` for `a=0`) | same module | accept only the C-level getset descriptor, as for slots | Low |
| d | The `<unreprable: T>` fallback skips the `max_len` clamp (a 300-character class name yields 314 characters at the default limit of 120) | same module | route the fallback through the clamp | Low |
| e | The JSONL readers disagree on what a blank line is: `read_jsonl` (`str.strip`) vs `JsonlIndex`/aggregates (`bytes.strip`) vs `read_window` (no strip), for U+0085, U+001C–U+001F, NBSP, U+2028/9, U+3000 — so the seekable and non-seekable replay of one file report different counts | `writer.py`, `jsonl_index.py`, `aggregates.py` | fold into F-11's shared per-line decode helper | Low |
| f | **One symlink escaping the root aborts the whole static parse** (`ValueError` from `to_posix`), for Python, TypeScript, Go and Rust: `grackle parse` exits 1 with a traceback, `serve` pushes no graph at all | `python_parser/walker.py`, `tree_sitter_walker.py` | one guarded posix-key helper for the walkers, skipping with a warning as `watcher._safe_posix_key` already does | **Medium** — a single shared-module symlink, common in monorepos, takes down the whole project's graph |

### F-15 — Frontend beacon grammars (T8-6)

Ledgered as `it.fails`, minimized by hand.

| # | Defect | Location | Severity |
|---|---|---|---|
| a | Every grammar takes an unbounded `\d+` and decodes it with `parseInt`, so a 309+-digit field becomes `Infinity`: an `Infinity` epoch gives the loss curve NaN x-coordinates, and two different oversized dimensions both become `Infinity`, so the network chain check passes when it should not (3 `it.fails`, one per parser; a passing boundary test shows 1e308 still parses) | `graph/epochSeries.ts`, `layerStats.ts`, `networkSpec.ts` | Low — the default `max_value_len=120` truncates the repr first; needs a raised limit or a hand-written trace |
| b | The architecture latch disagrees with a one-shot scan: resuming with `cache.spec ?? extractNetworkSpec(events, cache.scanned)` walks past a `record_architecture` beacon that parses but is incoherent, which a one-shot scan stops at — so the live panel shows `model: 1-1` while a replay of the same trace shows "No network beacons" (1 `it.fails`, at panel level so it stays right whichever layer is fixed) | `panels/NetworkViewPanel.tsx` + `graph/networkSpec.ts` | Low |

### C5 observations (not ledgered)

- **`profile_reconstruct` does not clamp a negative `timeDeltas`.** If V8 ever emits one, the
  sampling path's documented time order breaks and `node_runtime/test_e2e.py`'s ordering check
  would fail. That would be a product bug to record, not a test to loosen. Not reproduced
  locally (0 negatives in 15 runs).
- **`to_posix` docstring drift:** it says a symlink loop raises `RuntimeError`, but Python 3.13's
  `resolve()` no longer raises on loops (it returns `"loop/x.py"`). On 3.12 a self-referential
  symlink still aborts the parse (F-14 f's sibling), so that case was left out of the xfail.
- **macOS Unicode normalization:** NFC and NFD spellings of one file give different `to_posix`
  keys, an undocumented sibling of the documented case caveat.
- **Network dimensions above 2^53** lose precision, so two different huge dimensions can
  compare equal (same behavior as `Number()`).

### F-16 — The graph canvas collapses at common laptop widths (T12-1)

**Location.** `packages/frontend/src/App.tsx`, the shell grid: `gridTemplateColumns: "auto 1fr auto"`.

**Reproducer.** Serve any graph, open the UI at a 1024 × 768 viewport, and measure `<main>`.

**Observed.** The two `auto` side columns grow to the unwrapped width of their widest content,
and the `1fr` graph column gets what is left, down to its min-content, which is 0. Measured: the
left column is 447 px, set entirely by the Session Library's empty-state sentence ("No stored
sessions. Start the server with --store to save sessions.") laid out on one line, and the right
column 571–630 px, set by the inspector and stats panels. At 1024 px the graph column is **0 px**
(the Sigma canvases are 1 px wide). At 1440 px it gets 357 px, a quarter of the screen. It takes
roughly 1100 px before any graph is visible at all.

**Expected.** The graph — the product's main view — keeps most of the width at any common
laptop size, and the side panels wrap their text.

**Fix.** Bound the side tracks, e.g.
`gridTemplateColumns: "fit-content(320px) minmax(0, 1fr) fit-content(420px)"`, so panel text
wraps instead of widening its column. Not ledgered: jsdom has no layout engine, so no unit test
can see it. It is a candidate for the deferred browser-automation decision (T12's Playwright
note).

**Severity.** Medium — the main view is invisible at 1024 px and cramped at 1280–1440 px, and
nothing signals why.

### F-17 — In a seekable session, only the scrubber loads the event window (T12-1)

**Location.** `packages/frontend/src/panels/TimelinePanel.tsx`, `handleSeekablePlayheadChange`:
the debounced `trace_seek_request` lives in the scrubber's change handler. Every other
`setPlayhead` caller moves the playhead without it: `graph/useTracePlayback.ts` (Play),
`panels/LossCurvePanel.tsx` (click-to-seek), `panels/CausalPathPanel.tsx` (hop click), and
`panels/ValueInspectorPanel.tsx` (call-stack frame click).

**Reproducer.** Serve the nn demo (`packages/nn/run-a.jsonl`, 25,870 events — any
`--trace-source` replay over 200 events is seekable). Then (a) click the middle of the loss
curve, or (b) scrub to 0 and press Play.

**Observed.** (a) The playhead jumps to event 12,964, but the value inspector shows "No event at
this position.", and "next ▶" does nothing. A second click at event 21,134 does the same. (b)
From event 201 on — the default 200-event window — every sample during playback shows "No event
at this position." Dragging the scrubber to event 12,000 loads the window, and the inspector
shows `call Sequential.backward` at depth 4.

**Expected.** Wherever the playhead moves, the events around it load, so the time-travel
inspector works during playback and after every seek.

**Fix.** Move the window fetch out of the scrubber handler into one place every playhead move
passes through — an effect keyed on `tracePlayhead` in seekable mode, fetching when the playhead
leaves the loaded window. Not ledgered yet: the right test depends on where the fetch ends up (a
panel-level test would miss an App-level hook). Worth writing alongside the fix.

**Severity.** Medium-high — every `--trace-source` replay over 200 events is seekable, including
the nn "watch it learn" demo, and there the headline interactions (Play and loss-curve seek)
land on an empty inspector.

### F-18 — Frontend hooks and panels: six defects, eight `it.fails` tests (T11-1, T11-6)

Each has a passing control test beside it, and each was flipped to a plain `it` once to confirm it
fails on its final assertion rather than during setup.

| # | Defect | Location | Fix direction | Severity |
|---|---|---|---|---|
| a | A node removed and then **restored within its 400 ms fade** is still dropped. The fading node is still in the live graph, so `applyGraphDiff` sees nothing structural, `isEmptyDiff` is true, and the effect returns before `recordDiffAnimations` — the only code that cancels a fade — so the rAF tick then drops the node. The store graph has it; the live graph and canvas do not, until the next structural re-push. Repro: mount, wait out the 5 s settle, push the graph without an isolated node (an empty `pkg/__init__.py`), 100 ms later push the original back, advance 800 ms. It happens only when the restoring push brings back no edge; a passing companion shows the same restore survives when the node's edges return with it. | `graph/GraphCanvas.tsx` (the early return at ~line 468) | remove that early return — checked once, not committed: exactly the one `it.fails` turns red and the other 35 stay green | Low-medium — a stale canvas after an editor blink |
| b | **Speed multipliers are not proportional.** Each frame advances `max(1, round(dt·0.05·speed))` and throws the fractional part away. At 16 ms frames 4× runs at 3.02× the 1× rate; at 120 Hz, 2× plays at exactly the 1× rate and 1× runs at 120 events/s against the documented 50. One second at 1× and at 4× gives 62 and 184 events (2 tests) | `graph/useTracePlayback.ts` | carry the fractional remainder across frames | Low-medium — playback speed depends on the display's refresh rate |
| c | **The heat map freezes during seekable playback.** The canvas paints `agentHeat` whenever it is non-null, but `TimelinePanel` re-queries it only after the playhead has been still for 150 ms, which never happens while playing. Repro: a seekable session, play for 1 s — the playhead is past 50 and the heat is still the value computed at playhead 0. It contradicts `PredictedHeatPanel`'s own header, which says the heat is re-queried "on every playhead move". Same class as F-17 | `graph/useHeatmap.ts` with `panels/TimelinePanel.tsx` | fix together with F-17: one place every playhead move passes through | Medium |
| d | **The event-type filter is ignored in seekable + cumulative mode.** The agent-heat branch never checks the filter and the query carries none. Repro: with the filter set to `{"exception"}` and no exception events, `maxHeat` is 90 rather than 0 | `graph/useHeatmap.ts` | pass the filter in the query, or apply it client-side | Low-medium |
| e | **`SessionLibraryPanel` shows "Error: Error: session_list_request timed out"**, because `String(err)` already starts with "Error:" | `panels/SessionLibraryPanel.tsx` | use `err.message` | Low |
| f | **`SessionLibraryPanel` has no retry after a failed request** (the Refresh button exists only when the list is non-empty), **and a failed Refresh while sessions are listed shows nothing**, because `error` is rendered only in the empty branch, so the stale list stays up and reads as current (2 tests) | `panels/SessionLibraryPanel.tsx` | always render Refresh and the error | Low-medium |

**Recommendation.** Fix F-17 and F-18 c together, since they are one mechanism: the debounced seek
lives in the scrubber's handler, and every other playhead move bypasses it. The rest are
independent, small changes.

### C6 observations (not ledgered)

- **Concurrent live producers merge in the UI** (T12-3): two sessions streamed at once appear
  as one interleaved timeline, since `trace_event` carries no session id and the frontend models
  a single live session; the recording sink keeps them separate.
- **Watch re-push drifts surviving nodes slightly** as the layout reheats, and the camera re-fits
  to new nodes (T12-2) — the Phase 10.7 design, recorded here so it is not re-reported as a bug.
- **The theme follows the OS only on the first visit.** `useTheme` writes the OS-derived default to `localStorage`, so it is never consulted again. This may be intended; the question is whether to persist only an explicit choice.
- **One existing theme test proves less than its name:** `useTheme.test.ts`'s "reads stored theme from localStorage on init" never re-runs the initializer. The new `useTheme.colorScheme.test.ts` covers that path.
- **ADR-0015 is stale on rAF:** it says jsdom has no `requestAnimationFrame`, but vitest's jsdom does (the T11-1 and T11-6 suites both rely on it).
- **Misleading empty-state copy:** the Session Library's "Start the server with --store" shows even when the server already has `--store` but no sessions yet — it is also the sentence that set the left column's width in F-16.
- **Loading a session while disconnected silently does nothing.**
- **Unverified, from reading library source only:** `graphology-layout-forceatlas2`'s worker `kill()` does not appear to clear its pending `setTimeout(0)` respawn, so a graph change just before `kill()` could start a worker after the kill that is never stopped. This is upstream of `GraphCanvas`, and the harness fakes FA2, so it cannot see it.
- **Test-writing pitfalls recorded for the next suite author:** jsdom's `localStorage.setItem` queues a timer, so `useTheme.setTheme()` shows up in `vi.getTimerCount()`; graphology's `edges(a, b)` also matches a `b → a` edge on a directed graph, so use `outEdges(a, b)`.
- `GraphCanvas.reducedMotion.test.tsx` (T11-7) overlaps T11-1's reduced-motion case. It stays because it uses the honest stub and adds the node and edge paint assertions; it could later be folded into `GraphCanvas.test.tsx`.

### Open observation (C2) — one unexplained full-suite stall

During C2, one `uv run pytest -q -ra` of the agent suite stalled for over 10 minutes (normally about
20 s) and was killed. Four immediate re-runs — one verbose, three with the original flags plus
`faulthandler_timeout=45` — all passed in 20–24 s, and no stack was captured. It is not attributed to
any C2 change. **Recommendation:** set `faulthandler_timeout` in the agent's pytest config so the
next stall dumps every thread's stack into the CI log instead of reaching the job timeout silently.

## What worked well

*(Populated as tiers execute — positive evidence from active probing, per the phase-0 tradition.
Early candidates already visible from the audits: the `CacheManager` concurrency suite as the
model the other 8 seams should copy; the `predicted_heat` byte-identity + discriminating-power
pair; `callTree.test.ts` and the DiffPanel persistence block as pre-12.4 tests that already meet
the battery bar; the `_EIGHT_LINES` malformed-corpus template; `test_labels.py:69`'s 1-ULP sweep
as the numeric-property precedent.)*

**C2:**

- **The script-frame interrupt pin (D12.0.9) served as the oracle for T5-1.** Rather than invent
  Ctrl-C semantics, every other window was held to the behavior already pinned for the one window
  that worked.
- **`BufferedWriter` hands the kernel whole lines.** It flushes the whole buffer *before* buffering
  a line that does not fit, which is why 0 of 8 real SIGKILLs tore a line. The existing kill test's
  "a torn partial line if the kill landed exactly mid-write" is accurate, and rarer than it reads.
- **Rename-last ordering in `JsonlPartWriter.finalize`.** Because `replace()` runs after the
  `close()` that flushed everything, a failed rename always leaves a complete, closed `.part`: the
  CLI and `RecordingSink` policies (keep vs. discard) both behave correctly on it with no special
  case.
- **Consistent per-line decoding across packages.** `jsonl_index`, `aggregates`, and nn's `labels`
  all catch `(json.JSONDecodeError, UnicodeDecodeError)` per line, so a torn salvage costs exactly
  one line wherever it is read.

**C4:**

- **Every concurrency pin was shown to fail without its synchronization.** Deterministic
  window-widening (parked connections, paused writes, held-open iterators) made the gate tests
  reliable. The many-iteration hammers are corroboration, not the evidence.
- **The corpus sweep used an independent oracle, not just "doesn't raise."** Offsets, counts,
  heat and coverage are compared at every position against a separate recount, so a silently
  wrong answer fails as loudly as a crash.
- **Crashing probes ran in a child process.** The T7-1 parser race turned a SIGSEGV into a clean
  assertion failure instead of taking the test session down.

