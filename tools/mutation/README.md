# Mutation harness

A minimal, dependency-free mutation-testing tool. Part of the repo-wide test
campaign (`docs/test-campaigns/phase-12.md`), prerequisite P-2.

## Why this exists

Phase 12.4 (commit `99b5a9a`) ran a "30-mutant sweep" over its 8 new frontend
modules and reported 30/30 killed. That sweep was done ad hoc, in-session,
with nothing committed — it cannot be re-run, cannot regression-check itself
against a future change, and nobody can verify the claim today. This tool
fixes that: mutants are JSON specs, committed to `specs/`, and `runner.mjs`
applies one, runs the suite that's supposed to catch it, and reports whether
the outcome matched what the spec declared.

## Doctrine

Every test must be demonstrated able to fail. A spec's `expect` is usually
`"killed"` — the mutant introduces a real bug and some test must go red. But
`expect: "survives"` is equally legitimate and often more valuable: it is how
you *prove* a gap exists rather than merely suspect one. See
`specs/agent-cache-bounded-race-guard.json` — it demonstrates that no test in
the package exercises a documented benign-race handler, by showing the suite
stays green even after that handler is narrowed. That is a finding, recorded
as an executable fact instead of a claim in prose.

## Usage

```bash
node tools/mutation/runner.mjs                                    # all specs
node tools/mutation/runner.mjs tools/mutation/specs/some-spec.json # one spec
```

Each spec's target file is mutated, the named suite runs, the file is
restored, and the actual outcome is compared against `expect`. Exit code is
non-zero if any spec's actual outcome didn't match its declaration — i.e. the
harness itself failed to discriminate correctly, or a target has silently
changed behavior since the spec was written (in which case update the spec,
don't just re-run it).

Restoration is unconditional: it survives a throwing suite command (`finally`)
and an interrupted run (`SIGINT`/`SIGTERM`/`SIGHUP` handlers, plus an `exit`
backstop that also covers an uncaught exception). This matters because the
mutation is written into your real working tree — a mutant left behind is a
deliberate bug one `git commit` away from shipping.

### Three outcomes, not two

A suite run resolves to `killed` (it ran and went red), `survives` (it ran and
stayed green), or **`error`** — it never reached a verdict at all. The third is
reported as a spec failure and is deliberately *not* compared against `expect`,
because a mutant nothing measured must never be certified as caught. `error`
covers a suite that failed to launch, one killed by a signal, a pytest exit
outside {0, 1} (4 and 5 are what a stale path in `suite.args` produces), and a
vitest run that printed no `Test Files` summary — vitest exits 1 both for a
genuine failure and for "No test files found", so its exit code alone cannot
tell the two apart.

## Writing a new spec

```json
{
  "id": "unique-slug",
  "description": "one line: what bug this mutant introduces, and why",
  "target": {
    "file": "packages/agent/src/grackle/paths.py",
    "find": "<exact source substring — must occur exactly once in the file>",
    "replace": "<the mutated substring>"
  },
  "suite": {
    "kind": "pytest",
    "cwd": "packages/agent",
    "args": ["-q", "tests/test_paths.py"]
  },
  "expect": "killed"
}
```

- `target.find` must match **exactly once** — the runner refuses to apply an
  ambiguous or stale mutant rather than guess.
- `suite.kind` is `"pytest"` (runs via `uv run pytest`, `cwd` one of
  `packages/agent` or `packages/nn`) or `"vitest"` (runs via
  `pnpm exec vitest`, `cwd` `packages/frontend`).
- Scope `suite.args` to the smallest test file/suite that's expected to
  catch the mutant — the harness is meant to run inside the CI budget
  (`docs/test-campaigns/phase-12.md`'s tier T4 plan: fast specs in the PR
  gate, a fuller sweep nightly), not to re-run the whole package per mutant.
- A mutant that turns out to always survive *and* was expected to be killed
  is itself a finding: either the target isn't actually tested (write it up
  in `docs/test-campaigns/phase-12.md`'s Findings section and consider a new
  regression test), or the spec's `find`/`replace` doesn't introduce the bug
  you think it does.

## What this is not

Not a general-purpose mutation-testing framework (no AST-level mutation
operators, no automatic candidate generation, no coverage-guided targeting).
Each spec is a deliberately hand-authored, understood defect — that's the
Phase 12.4 precedent this tool formalizes, not a replacement for it. Tools
like `mutmut`/`cosmic-ray` (Python) or Stryker (JS) do the automated version;
this repo has chosen not to take that dependency (see
`docs/test-campaigns/phase-12.md` prerequisite P-2's rationale).
