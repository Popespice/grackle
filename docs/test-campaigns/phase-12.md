# Phase 12 Test Campaign — the full-stack battery

**Date drafted**: 2026-08-20
**Under test**: `v0.12.0-phase-12` (main `c305deb`) — the entire stack
**Environment**: macOS 26.5 / arm64 primary; CI matrix Ubuntu + Windows (+ macOS on main-push), Python 3.12/3.13, Node 22
**Status**: DESIGN — probes defined, execution chunked and pending approval; findings recorded here as tiers execute

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
| T2-1: `test_stress_2k_layout.py` executes | `fixtures/stress-2k/src` is not checked in (only `generate.py`), so the module-level skipif means **this test has never once run in CI** — a green tick for nothing. Probe: generate the fixture in-job (seeded, `SEED=42`) or commit it, then run. Fails if the 1,500-node / 20-second budget doesn't hold — which has literally never been checked. | **Seeded finding** (`tests/test_stress_2k_layout.py:13`) |
| T2-2: symlink skips are visible | `tests/test_paths.py:77,89` skip at runtime inside `except OSError` — on a Windows runner without Developer Mode both vanish indistinguishably from passing. Probe: add `-rs` to CI pytest invocations so skips are reported, and assert in-job that the skip count matches an expected-per-OS table. | Open |
| T2-3: nn's implicit `sys.path` coupling | `test_synthetic_acceptance.py:15` does a bare `from synth import …` that only works via pytest's default `prepend` import mode — no `conftest.py` exists under `packages/nn/tests/`. Fails if anyone sets `importmode=importlib`. Probe: add the one-line conftest or a comment-pinned test. | Open |

### T3 — Guard-of-the-guards (meta-tests: does the safety net itself work?)

The campaign's highest value-per-cost tier. The repo's guards are only ever exercised in the
passing direction; this tier drives each one through its **failure** paths.

| Probe | Fails if | Status |
|---|---|---|
| T3-1: parity guard failing-direction meta-test | `verify-parity.mjs`'s failure paths have **never executed once**: `diffSets`' onlyA/onlyB branches, all three `throw` sites, the MISSING branches, `process.exit(1)`. Worse, the guard is regex-textual — a formatter switching `messages.ts` to single quotes would make both type-set extractions return empty, and empty-vs-empty compares **pass vacuously**. Probe: a pure-Node test that copies the schema dir to temp, mutates it (add a type const / remove one / rename a `$def` / author one under Draft-07 `definitions`), and asserts the guard reports drift each time — plus the vacuity case (zero extracted types must be an error, not a pass). | **Seeded finding** (`verify-parity.mjs:97-144`) |
| T3-2: schema authored under `definitions` is invisible | Both extractors read `$defs` only, with `?? {}` defaults — and `packages/shared-types/schema/README.md:15` **still instructs contributors to use `definitions`**, the exact keyword renamed away in a phase-1.5 finding. Following the README's own instructions produces a vacuously-passing guard. Probe: the T3-1 meta-test's Draft-07 case + fix the README line. | **Seeded finding** |
| T3-3: message-type consts outside the hardcoded path | Both the JS and Python extractors hardcode `$defs.<X>.allOf[*].properties.type.const`. A type declared via `enum:` or `oneOf` generates fine and is **never parity-checked**; duplicate consts collapse silently in the `Set`. Probe: meta-test cases for each shape. | Open |
| T3-4: path-discipline lint test (the missing one) | `paths.py:3` declares itself "the single sanctioned location for `Path.relative_to`" — **nothing enforces it**, and the invariant is already violated at `cli.py:437` and `node_resolution.py:91` (both arguably legitimate containment predicates, but that distinction exists nowhere). CLAUDE.md's claim of "a path-discipline lint test" is aspirational. Probe: port the AST-scan pattern from `test_ml_bridge_import_hygiene.py` (which already ships its own discriminating-power meta-tests) into a `test_path_discipline.py` with a two-site allow-list. Fails on any new unsanctioned `relative_to`. | **Seeded finding** |
| T3-5: import-hygiene scanners still discriminate | Re-run the existing meta-tests; extend the agent-side AST scan to flag `Path.relative_to` per T3-4 (shared walker). | Open |
| T3-6: codegen determinism + degradation | Re-run the phase-1 probe (byte-identical double run) plus new cases: a typo'd `$ref` (degrades to permissive types on the TS side while Python differs in shape — parity stays green because each generator is only compared to itself), a non-`.schema.json` filename (skipped silently by both codegen and parity), and the unpinned `uvx --from datamodel-code-generator` version (record the resolved version in CI output so drift is at least visible). | Open |

### T4 — The mutation battery, formalized and extended

Using the P-2 harness. Each module gets a committed mutant spec; the runner is the regression
check the 12.4 sweep never had.

| Probe | Target modules | Fails if |
|---|---|---|
| T4-1: re-verify the 12.4 sweep | The 8 frontend 12.4 modules (`networkSpec`, `layerStats`, `layerActivity`, `networkLayout`, `epochSeries`, `useAppendOnlyScan`, `beaconNode`, `playheadLookup`) | Any of the re-specified ~30 mutants survives — i.e. the in-session 30/30 claim doesn't reproduce under the committed harness. |
| T4-2: agent pure core | `aggregates.py` (bisect `at_index - 1` off-by-ones), `diff.py`, `value_repr.py` (budget decrements), `writer.py` (offset/count pairing, truncate guard), `jsonl_index.py` | A boundary-flip / dropped-guard mutant survives the agent suite. |
| T4-3: nn pure core | `features.py` (Tarjan lowlink, bucket prefix order, entry-set self-loop rule), `labels.py` (`_event_weight` branches), `metrics_rank.py` (tie averaging), `heat_model.py` (standardization symmetry) | Same. |
| T4-4: **calibration case** — `server.py:132` | The `except RuntimeError` ("dictionary changed size during iteration") in the predicted-cache FIFO eviction is **provably unreachable by any existing test** — deleting it leaves the suite green. This is the harness's known-positive control: the mutant *must* survive, proving the harness reports survivors honestly. It then becomes a T7 concurrency target (the only thing that can reach it is a real race). | Expected survivor |
| T4-5: vacuous-test audit (the 12.4 deletion rule, applied repo-wide) | Known candidates seeded by audit: `StatsPanel.test.tsx:57-74` (disjunctive `toMatch(/Foo\|baz/)` over the only two candidates — passes under random ranking; two tests, one a duplicate of the other), `CyclesPanel.test.tsx:63-68` (same pattern), `test_optim.py:36` (Adam checked only with a constant gradient, where bias correction cancels **exactly** — cannot distinguish `β^t` from `β^(t±1)`), the SessionStore/stream-sender lock tests that pass with the lock removed (T7). Each is either strengthened to discriminate or deleted per the 12.4 rule. | A listed test survives its targeted mutant. |

### T5 — The expected-fail ledger

Known-real defects, committed red. The 9 frontend `it.fails` from `99b5a9a` are the existing
ledger (4× `layerActivity` phase-never-returns-to-idle — 60/60 epoch markers on the real
`run-a.jsonl` read "loss" where every loss-curve click lands; 2× thread-clobbering single-slot
scan state; 2× glyph/neuron hit-test collision; 1× sub-210px layout mirroring). This tier adds
the agent-side ledger, each entry written as a **failing test first**, committed strict-xfail:

| Probe | The defect | Status |
|---|---|---|
| T5-1: KeyboardInterrupt outside the script body skips finalize | **Confirmed REAL GAP.** The incremental `-o` block (`cli.py:584-602`) catches `Exception` but has no `finally` — and `Tracer.run`'s `except BaseException` protects only `runpy.run_path`, not `_build_tracer` (`adapter.py:98`), which does a **full project parse** — the longest window in the whole command. A Ctrl-C there propagates, `_finalize_output` never runs, the `.part` is orphaned, and the next run at the same `-o` path is **bricked** by the exclusive-create refusal with an error message describing the wrong scenario. Four distinct escape windows identified (`_build_tracer`; `Tracer._start()` before the try — which also leaks the `sys.monitoring` tool registration process-wide; `_stop()` in the finally; the sink-captured-BaseException re-raise). The `--stream` tee path has the identical gap (`cli.py:539-541` sits after its try/finally). Repro: monkeypatch `_build_tracer` to raise `KeyboardInterrupt`; assert final file exists / no `.part` survives — fails today. The existing KI test (`test_cli_trace.py:1293`) covers only KI raised *by the traced script*, the one window that already works. | **xfail, then fix** (fix is a `try/finally` — but the sink-re-raise window means semantics need deciding first, which is why the test comes first) |
| T5-2: `serve()` has no readiness signal | The `test_two_sessions_back_to_back` Windows flake diagnosed: `create_task(serve(...)); await asyncio.sleep(0.05)` — nothing awaitable exists between task creation and socket listen, and the store-backed fixture does strictly more pre-listen work (mkdir + orphan sweep + `detect_language` filesystem walk). `[WinError 1225]` is `ERROR_CONNECTION_REFUSED`: nothing was listening. A second window: `free_port` releases the port before `serve()` rebinds (TOCTOU; asyncio sets no `SO_REUSEADDR` on Windows). A third, latent: finalize's two default-executor round-trips inside the test's 100ms sleep budget. **The same create-task-then-sleep pattern exists at 34 sites across 10 server test files.** Repro: wrap `_ws_serve` with an injected pre-bind delay > 50ms — converts a twice-a-year Windows flake into a 100% cross-platform failure. | **xfail, then fix** (an `asyncio.Event` readiness signal or `sock=` handoff in `serve()`, then migrate all 34 sites off the sleep) |
| T5-3: events after `session_end` diverge recording from broadcast | `server.py:777-783`: after `trace_session_end`, subsequent `trace_event`s are still ring-buffered and broadcast but silently dropped from the recording — a misbehaving producer yields a recording whose `event_count` disagrees with what every connected UI showed. Repro: send end, then two more events; compare. | **xfail or pin as documented behavior** — the probe forces the decision |
| T5-4: ENOSPC surfaces at the wrong layer | `JsonlPartWriter.write` writes through a **buffered** stream — ENOSPC doesn't surface at the failing `write()` but at a later implicit flush or at `close()` inside `finalize()`. So `broken` stays False, `_last_good_offset` is wrong, and the truncate-salvage guard never fires. The salvage design has an untested hole exactly at the buffering boundary. Repro: a small filesystem image or an injected flush-failure. | **xfail candidate** — likely a real defect |
| T5-5: torn multi-byte UTF-8 kills the whole file | The SIGKILL path can tear mid-UTF-8-sequence; the kill test's script is ASCII-only and never asserts the surviving prefix decodes. `read_jsonl` does one whole-file `read_text(encoding="utf-8")` — an undecodable byte fails the **entire file**, not one line. Repro: kill mid-write of non-ASCII node names. | **xfail candidate** |
| T5-6: `finalize()` failing at `replace()` | Only the `close()` failure is tested; a `replace()` failure (destination open in another process — realistic on Windows) leaves `_finalized` False and the `.part` orphaned with no open handle. | Open probe |

**Promotion protocol** (applies to all 15+ ledger entries, frontend and agent): an expected-fail
turning green (strict xfail XPASS / `it.fails` passing) is the signal the defect got fixed — the
marker is removed in the fixing PR, promoting the test to a permanent regression pin.

### T6 — Fault injection and recovery

Beyond the ledger entries, the injection battery over every persistence/ingest surface:

| Probe | Fails if |
|---|---|
| T6-1: SQLite store — locked db, corrupt db file, use-after-close, concurrent writers, missing `source_path` | The store has **5 tests, all happy-path**. A corrupt db currently surfaces as an unhandled traceback at CLI startup; a shutdown-vs-finalize race silently loses the session row (`ProgrammingError` swallowed at `recording_sink.py:175-182`); the schema DDL has no migration path — an added column against an existing db no-ops then fails at INSERT. |
| T6-2: orphan sweep hazards | `sweep_orphaned_recordings` with an unlinkable `.part` (permission error) **raises out of `serve()` startup before the socket binds** — the same unobserved-task-death shape as the T5-2 flake; and the 30-second age heuristic can eat a *live* recording's `.part`, admitted in its own docstring, pinned by nothing. |
| T6-3: WS ingest — oversized frame (>1 MiB default closes with 1009 *before* the receive loop sees it; whether the in-flight recording finalizes correctly is unpinned), `session_load_request` flood (unbounded `create_task` fan-out), slow-consumer stall (sequential `await ws.send` per connection inside the producer's receive loop — one slow consumer blocks ingest for everyone and for the recording sink; zero coverage) | Any of these crashes, hangs, corrupts a recording, or starves the ring buffer. |
| T6-4: watch mode — file deleted mid-parse through the real watch loop; rebuild-during-rebuild serialization **pin** (currently structural via `max_workers=1` + sequential await; a future `create_task` refactor would silently race the unlocked caches) | The pin is the probe: assert two rapid edits never produce overlapping `_build_static_graph` executions. |
| T6-5: malformed-corpus sweep | Extend the `_EIGHT_LINES` in-test template (the repo's best malformed-input pattern) into a shared adversarial-trace generator, seeded, per the `stress-2k/generate.py` committed-generator precedent — used against `read_jsonl`, `JsonlIndex.build`, `TraceAggregates.build`, `heat_from_jsonl`, and the three external-tool parsers (covdata / llvm-cov / V8 profile), which parse untrusted output and whose e2e tests are toolchain-gated off most CI legs. Fails if any parser raises, hangs, or emits a node_id containing `\` or `..`. |

### T7 — The concurrency battery

The audit found **9 concurrency seams; exactly 1 (`CacheManager`) has a test that fails if its
synchronization is removed.** Every other lock/serialization in the agent is decorative as far
as the suite can tell.

| Probe | Seam | Fails if |
|---|---|---|
| T7-1 | tree-sitter parser singleton — concurrent `.parse()` | The source itself flags this un-audited (`server.py:468-476`); the existing 4-thread test asserts only singleton *identity*, never racing `.parse()` — the actual documented risk, reachable today from the watch executor + connect path. |
| T7-2 | stream sender `_counter_lock` | No test races `sink()` against `_drain_loop()` — the exact lost-decrement the lock's docstring says it prevents. Mutating the lock away likely leaves the suite green (T4-5 crossover). |
| T7-3 | `SessionStore` lock | All 5 tests single-threaded; N-writer hammer test modeled on `test_cache.py:301` (the one good example in the repo). |
| T7-4 | `meta_cache` / `predicted_ctx.cache` unlocked dicts (executor thread + connect path) | The documented "benign race" has never been exercised; the `except RuntimeError` mitigation is the T4-4 calibration survivor. A two-thread hammer either reaches it (promoting it from unreachable to pinned) or the benign-race claim gets its first evidence. |
| T7-5 | two producers, two connections, one store | Recording-sink interleave — never tested beyond sequential sessions on one connection. |

### T8 — Property and fuzz batteries (gated on P-3)

The agent suite is ~100% hand-picked examples (near-zero parametrize). These surfaces have rich
input spaces and, in two cases, exact oracles:

| Probe | Property / oracle | Fails if |
|---|---|---|
| T8-1: `value_repr.safe_repr` over generated adversarial objects | Bounded output (≤ max_len / max_depth / max_items), never raises, never invokes user code — the canonical property shape, over the package's largest pure surface (708 LOC, 66 excellent but purely example-based tests) | Any generated object breaks a bound or triggers a user `__repr__`. |
| T8-2: `TraceAggregates` metamorphic oracle | For any generated event list and any `at_index`: `cumulative_heat` / `coverage_count` / `top_k` equal a naive linear recount (inequality band where `sparse_k > 1` is documented approximate) | The bisect `at_index - 1` logic disagrees with the oracle anywhere. |
| T8-3: `JsonlIndex` vs `read_jsonl` differential | Same file, both implementations, byte-generated payloads **including U+0085 / U+2028 / U+2029** — the exact `split("\n")` vs `splitlines()` hazard `writer.py:78-83` documents; window concatenation reconstructs the file; absurd windows never raise | The two implementations disagree → seek corruption. |
| T8-4: `to_posix` path properties | Never contains `\`; never escapes root; round-trips — generated over unicode, reserved Windows stems (`CON`, `NUL`), trailing dots, long names | Exactly where the Windows-only bug class lives. |
| T8-5: `diff.py` algebra | `diff(g,g)` empty; A→B and B→A inverse | |
| T8-6: frontend beacon grammar (hand-adversarial extension, no new dep) | Extend the 12.4 adversarial-payload corpus for `FLOAT` / `EPOCH_RET_RE` / the arity-built stats regex — generated-ish sweeps via seeded loops rather than fast-check, keeping the frontend dep surface untouched | |

### T9 — Numerics and the ML envelope

| Probe | Fails if | Status |
|---|---|---|
| T9-1: acceptance-margin telemetry | The bar sits at +0.05 with a **measured single-point margin of ~0.056** — 0.006 of headroom — and both computed metrics are consumed by bare asserts; `top10` is computed and **discarded**. Probe: emit margin + top10 on the passing path (minutes of work); nightly N-seed sweep over `(split_rng, train_seed)` characterizing the margin *distribution* — additive, per ADR-0029's never-unseed rule. Fails if the distribution's lower tail crosses the bar — i.e. the bar is a coin-flip, discovered before CI discovers it for us. | **Seeded finding** |
| T9-2: SoftmaxCE `backward()` at extreme logits | Forward at `|logits|~1e4` is tested for *finiteness only*, one input, argmax-is-correct-class only; **backward at saturation is tested by nothing**, and the gradcheck runs at `standard_normal` magnitude (finite differences are useless at 1e4 — needs the analytic `(probs − onehot)/B` oracle). | Open |
| T9-3: Adam with non-constant gradients | The only Adam test uses a constant gradient, where bias correction cancels **exactly** — and Adam is the optimizer the shipped `train_heat_model` actually uses; the well-tested SGD is the one the demo uses. Sign-flipping/varying gradient sequences against a NumPy reference implementation, steps 1..N. | **Seeded finding** (T4-5 crossover) |
| T9-4: ReLU/Tanh boundary sweep | `x == 0.0` subgradient convention unpinned (gradcheck *deliberately* excludes the kink, correctly — but nothing else covers it); Tanh saturation/±inf/nan unswept. | Open |
| T9-5: `test_train.py:100` window alignment | Pins **final-epoch-only** accuracy while `test_traceability.py:195` uses min-over-last-5 — and the codebase itself documents why final-only is fragile. One line. | **Seeded finding** |
| T9-6: checkpoint key-set pin | The `allow_pickle` history (a stray bool array written into every checkpoint on numpy 2.0/2.1) would be caught by exactly one thing — asserting the written npz's **key set** — which no test does. | Open |
| T9-7: numpy floor matrix | Declared `numpy>=2,<3`; locked 2.5.2; CI runs `--frozen` everywhere — **the 2.0/2.1 regime the code comments about is exercised by nothing**, `packages/nn` isn't even Dependabot-covered, and only two value-sensitive assertions defend demo convergence against a BLAS change (one of them the weak T9-5). Probe: a `ci-matrix.yml` main-push leg syncing `numpy==2.0.x` / `2.1.x` and running the nn suite. | **Seeded finding** |

### T10 — Cross-platform byte discipline

The byte-identity pins that exist are good (both JSONL writers, learn-history, predicted-heat
payload — the last with a discriminating-power companion, the repo's best-constructed pair).
The gaps:

| Probe | Fails if |
|---|---|
| T10-1: server-produced recording bytes | No direct assertion a *recording* is CRLF-free (only transitively via the shared writer). One byte-level check on a real recorded session. |
| T10-2: checkpoint reload-equivalence cross-platform | `heat-model.npz` has no byte pin (unattainable — ZIP embeds mtimes) **and no reload-equivalence pin either**: nothing asserts a fixed-seed model trained on OS A predicts identically loaded on OS B. Given the 1-ULP libm history lives exactly in this pipeline, a seeded predict-vector golden (tolerance-banded) is the probe. |
| T10-3: `sessions.db` forward-compat | `CREATE TABLE IF NOT EXISTS` + no migration path: open a db created by the previous schema, probe read + write. |

### T11 — Frontend rendering and panel hardening

| Probe | Fails if | Status |
|---|---|---|
| T11-1: `GraphCanvas` harness | **543 lines, the largest frontend module, zero tests** — sole owner of the Sigma/ForceAtlas2 lifecycle: three timer/RAF refs, `sigma.kill()` teardown, rebuild-vs-reheat (`hasSurvivor`), three event handlers, a manual RAF loop, theme-reactive repaints. Every collaborator is tested; their composition and every cleanup path are not. Probe: a mocked-Sigma lifecycle suite (mount/update/teardown, timer leak assertions via fake timers, rebuild-vs-reheat decision table). | **Seeded gap** |
| T11-2: port `makeRecorder` | `FlameGraphPanel` and `LossCurvePanel` still stub `getContext → null`, so **their entire paint paths run under zero coverage** — the exact defect class 12.4 fixed for NetworkView with `makeRecorder` (`NetworkViewPanel.test.tsx:309-356`), which is directly portable. | **Seeded gap** |
| T11-3: vacuous-assert fixes | The T4-5 StatsPanel/CyclesPanel disjunctive regexes → exact-value assertions (top-degree ranking must actually rank). | Seeded |
| T11-4: store-reset unification | `CyclesPanel.test.tsx` partial-merges a mocked action that persists for the rest of the module — the exact leakage `CausalPathPanel.test.tsx:84-95` defends against with full snapshot-replace + its own regression test. Apply the full-replace pattern to every panel suite. | Seeded |
| T11-5: `CausalPathPanel` perf cliff | The Windows CI timeout diagnosed: 199 sequential `getByRole` accessibility-tree scans + full React commits under the default 5s timeout — a genuine performance cliff, not nondeterminism. Fix: hoist the query out of the loop (the button node is stable). | **Seeded finding** |
| T11-6: untested hooks + panels | `useTracePlayback` (timer-driven, zero tests), `panels/init.ts`, `SessionLibraryPanel` (no test exists anywhere), `ConnectionBadge`, `useHeatmap`/`useCallTree`/`useRuntimeCoverage` wrappers. | Open |
| T11-7: matchMedia stub honesty | The setup stub answers `matches: false` to everything — `prefers-reduced-motion` / `prefers-color-scheme` true-branches never execute in any test (the 10.7 animation suppression is untested in the direction it exists for). | Open |

### T12 — Live-system end-to-end probes

The phase-0 tradition (real server, real browser, real protocol), updated for the v0.12 stack.
Manual-driven via the preview browser, like prior campaigns; Playwright automation deliberately
deferred (a ~300MB dependency for a separate decision).

| Probe | Fails if |
|---|---|
| T12-1: full-pipeline | `parse → trace → serve → browser` on `tiny-python-app` + the nn demo: heat, flame, timeline, ValueInspector, LossCurve click-to-seek, NetworkView phases — against the *known* T5 ledger (e.g. the epoch-boundary chip reading "loss" is expected-broken; anything *else* wrong is a new finding). |
| T12-2: watch + learn live | `serve --watch --model`: edit a file mid-session — predicted_heat survives the re-push (the 12.2 regression test's claim, verified live), positions/camera survive (10.7), a retrain mid-serve is picked up without restart. |
| T12-3: protocol edges against the live server | The T6-3 battery driven over a real socket: oversized frame, binary frame, malformed envelopes, event-after-end, two producers, kill-a-consumer-mid-stream. |
| T12-4: crashed-run recovery UX | SIGKILL a `trace -o` run; verify the `.part` story end-to-end: error message accuracy, salvage, the T5-1 bricked-path scenario. |

### T13 — Docs and config integrity

Following the phase-1 T8 tradition: claims vs reality.

| Probe | Fails if |
|---|---|
| T13-1 | CLAUDE.md's "path-discipline lint test" claim (doesn't exist until T3-4 lands); the schema README `definitions` instruction (T3-2); `packages/nn` missing from `dependabot.yml`; ADR cross-references for 0029/0030 resolve. |
| T13-2 | Acceptance-grid claims spot-audit: every "automated" cell names a test that actually runs (T2-1 proves at least one doesn't). |

---

## Execution plan (chunks, in order — one PR each, stop for review after each)

| Chunk | Contents | Cost gate |
|---|---|---|
| **C0** | Prerequisites P-1..P-4 + T13 doc fixes | PR gate (trivial) |
| **C1** | T3 guard-of-the-guards (parity meta-test, path-discipline lint, codegen probes) + T2 census | PR gate — all sub-second, Ubuntu shadow |
| **C2** | T5 expected-fail ledger (agent side: T5-1..T5-6 written as failing tests, committed strict-xfail) + the T5-1 and T5-2 **fixes** as follow-ups within the chunk if approved | PR gate |
| **C3** | T4 mutation harness + specs + T4-5 vacuous-test fixes; T11-2..T11-5 (recorder port, vacuity, store-reset, perf cliff) | Harness runs nightly; specs' *presence* checked at PR gate |
| **C4** | T6 fault injection + T7 concurrency battery | Fast cases PR gate; hammers nightly |
| **C5** | T8 property batteries (if P-3 approved) + T9 numerics (telemetry + sweeps + numpy matrix in `ci-matrix.yml`) + `campaign.yml` (nightly: mutation sweep, margin sweep, property long-runs, hammer tests) | Nightly + main-push |
| **C6** | T11-1 GraphCanvas harness + T11-6/7; T12 live-system probes executed and findings appended to this document in the F-N format | Manual + PR gate |

## Findings

*(Populated as tiers execute. Seeded findings above carry audit-derived citations; each is
confirmed by executing its probe before being written up here in the phase-0/1 format:
Location / Reproducer / Observed / Expected / Fix / Severity / Recommendation.)*

## What worked well

*(Populated as tiers execute — positive evidence from active probing, per the phase-0 tradition.
Early candidates already visible from the audits: the `CacheManager` concurrency suite as the
model the other 8 seams should copy; the `predicted_heat` byte-identity + discriminating-power
pair; `callTree.test.ts` and the DiffPanel persistence block as pre-12.4 tests that already meet
the battery bar; the `_EIGHT_LINES` malformed-corpus template; `test_labels.py:69`'s 1-ULP sweep
as the numeric-property precedent.)*
