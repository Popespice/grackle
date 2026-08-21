#!/usr/bin/env node
// Minimal, dependency-free mutation-testing harness.
//
// Doctrine (docs/test-campaigns/phase-12.md): every test must be demonstrated
// able to fail. This tool applies one hand-authored "mutant" (a targeted,
// deliberate bug) to a source file, runs the suite that's supposed to catch
// it, and asserts the outcome matches the spec's declared expectation —
// "killed" (the suite goes red) or "survives" (the suite stays green, which
// is itself sometimes the point: docs/test-campaigns/phase-12.md's T4-4
// calibration case proves the harness reports survivors honestly rather than
// always claiming a kill).
//
// This replaces the ad-hoc, unreproducible 30-mutant sweep from Phase 12.4
// (commit 99b5a9a) — that sweep's "30/30 killed" claim was never committed
// as anything re-runnable. Specs here are.
//
// Usage (spec paths resolve against the repo root, so pass them as written):
//   node tools/mutation/runner.mjs                                      # all
//   node tools/mutation/runner.mjs tools/mutation/specs/foo.json        # one
//   node tools/mutation/runner.mjs --check                              # validate only
//
// --check applies no mutation and runs no suite: it only asserts that every
// spec is well-formed and that its target.find still matches its source
// exactly once. That is the cheap guard against specs rotting silently as the
// code they target drifts, and it is safe to run on a dirty tree.
//
// Spec shape (see specs/*.json for real examples):
//   {
//     "id": "unique-slug",
//     "description": "one line: what bug this mutant introduces",
//     "target": { "file": "packages/agent/src/grackle/paths.py",
//                 "find": "<exact source substring, must occur exactly once>",
//                 "replace": "<the mutated substring>" },
//     "suite": { "kind": "pytest" | "vitest",
//                "cwd": "packages/agent" | "packages/frontend" | "packages/nn",
//                "args": ["-q", "tests/test_paths.py"] },
//     "expect": "killed" | "survives"
//   }
//
// The target file is always restored (mutation is applied only for the
// duration of one suite run), even if the suite command itself throws and
// even if the run is interrupted — see restorePending() below. This tool
// edits the working tree in place, so "always restored" has to mean always.

import { spawnSync } from "node:child_process";
import {
  closeSync,
  openSync,
  readdirSync,
  readFileSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, "..", "..");
const SPECS_DIR = join(HERE, "specs");
// Two runners mutating the same working tree would interleave their restores
// and write back each other's stale snapshots. A lock file is cruder than
// mutating a private copy, but the suites need the package's installed
// environment (node_modules, .venv), so a per-mutant copy is not viable.
const LOCK_PATH = join(HERE, ".runner.lock");

// The single file currently holding a mutation, or null. A mutation is a
// deliberate bug written into the developer's real working tree, so leaving
// one behind is the worst thing this tool can do: the next `git commit` would
// ship it. try/finally covers a throwing suite but NOT Ctrl-C, so the restore
// is also wired to the signals a developer actually sends and to 'exit' (which
// fires on an uncaught exception too). All handlers are synchronous because
// process teardown will not wait for async I/O.
let pendingRestore = null;

function restorePending() {
  if (pendingRestore === null) return;
  const { path, original } = pendingRestore;
  // Cleared first: if the write itself throws, a second handler firing during
  // teardown must not retry it forever.
  pendingRestore = null;
  writeFileSync(path, original, "utf-8");
}

let lockHeld = false;

function releaseLock() {
  if (!lockHeld) return;
  lockHeld = false;
  try {
    unlinkSync(LOCK_PATH);
  } catch {
    // Already gone (manually cleared, or a concurrent teardown). Nothing owed.
  }
}

// Restore first, then release: the lock must outlive the mutation it guards,
// or a waiting runner could start mutating before this one has cleaned up.
function cleanup() {
  restorePending();
  releaseLock();
}

process.on("exit", cleanup);
for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(signal, () => {
    cleanup();
    // 128 + signal number, the conventional shell encoding for "died to a
    // signal". Re-raising the signal would be more faithful but would need the
    // handler removed first; the exit code is what CI reads.
    process.exit(signal === "SIGINT" ? 130 : 143);
  });
}

function loadSpecs(argv) {
  const paths =
    argv.length > 0
      ? argv.map((p) => resolve(REPO_ROOT, p))
      : readdirSync(SPECS_DIR)
          .filter((f) => f.endsWith(".json"))
          .map((f) => join(SPECS_DIR, f));
  return paths.map((p) => {
    // A malformed or unreadable spec must fail as that one spec, not as an
    // uncaught stack trace that takes every other spec down with it.
    try {
      return { path: p, spec: JSON.parse(readFileSync(p, "utf-8")) };
    } catch (err) {
      return { path: p, spec: null, loadError: err.message };
    }
  });
}

function countOccurrences(haystack, needle) {
  if (needle.length === 0) return 0;
  let count = 0;
  let idx = 0;
  for (;;) {
    idx = haystack.indexOf(needle, idx);
    if (idx === -1) break;
    count += 1;
    idx += needle.length;
  }
  return count;
}

// pytest's exit codes are a documented contract, and only one of them means a
// test actually caught the mutant. 4 and 5 in particular are what a stale path
// in suite.args produces — that must surface as a broken spec, never as a kill.
const PYTEST_EXIT_MEANINGS = {
  2: "interrupted before finishing",
  3: "internal error",
  4: "usage error — suite.args is probably stale (bad path or flag)",
  5: "no tests collected — suite.args matched nothing",
};

// On Windows `uv`/`pnpm` on PATH are `.cmd` shims, and since the fix for
// CVE-2024-27980 Node refuses to spawn a `.cmd` without a shell (EINVAL). So
// win32 must use shell: true — which hands the command line to cmd.exe for a
// second round of parsing, hence quoteForCmd below. docs/cross-platform.md
// makes the Windows leg of the CI matrix non-negotiable.
const NEEDS_SHELL = process.platform === "win32";

function quoteForCmd(arg) {
  // cmd.exe splits on whitespace and interprets & | < > ^ ( ). Doubling is how
  // an embedded quote is escaped inside a cmd.exe quoted string.
  return /[\s&|<>^()"]/.test(arg) ? `"${arg.replace(/"/g, '""')}"` : arg;
}

function runSuite(suite) {
  const cwd = join(REPO_ROOT, suite.cwd);
  let cmd;
  let args;
  if (suite.kind === "pytest") {
    cmd = "uv";
    args = ["run", "pytest", ...suite.args];
  } else if (suite.kind === "vitest") {
    cmd = "pnpm";
    args = ["exec", "vitest", ...suite.args];
  } else {
    return {
      verdict: "error",
      detail: `unknown suite.kind ${JSON.stringify(suite.kind)} (expected "pytest" or "vitest")`,
      stdout: "",
      stderr: "",
    };
  }

  const result = spawnSync(cmd, NEEDS_SHELL ? args.map(quoteForCmd) : args, {
    cwd,
    stdio: "pipe",
    encoding: "utf-8",
    shell: NEEDS_SHELL,
    // The default 1MB cap silently SIGTERMs a verbose suite mid-run, which
    // would otherwise read as a mysterious signal death.
    maxBuffer: 64 * 1024 * 1024,
  });
  const stdout = result.stdout ?? "";
  const stderr = result.stderr ?? "";

  // Three-way, deliberately: "the suite ran and went red" (killed), "the suite
  // ran and stayed green" (survives), and "the suite never reached a verdict"
  // (error). Collapsing the third into the second-guess "non-zero == killed"
  // is what lets a harness certify a mutant that nothing actually caught.
  const classified = classifyExit(suite.kind, result, stdout, stderr);
  return { ...classified, stdout, stderr };
}

function classifyExit(kind, result, stdout, stderr) {
  if (result.error) {
    return {
      verdict: "error",
      detail: `failed to launch suite: ${result.error.message}`,
    };
  }
  if (result.signal) {
    return {
      verdict: "error",
      detail: `suite was killed by signal ${result.signal}`,
    };
  }
  if (result.status === 0) return { verdict: "survives", detail: null };

  if (kind === "pytest") {
    if (result.status === 1) return { verdict: "killed", detail: null };
    const meaning =
      PYTEST_EXIT_MEANINGS[result.status] ?? "unrecognized pytest exit code";
    return {
      verdict: "error",
      detail: `pytest exited ${result.status} (${meaning}) — no test verdict was produced`,
    };
  }

  // vitest exits 1 for a genuine test failure AND for "No test files found",
  // so the exit code alone cannot tell them apart. A run that actually
  // executed always prints a "Test Files" summary; its absence means vitest
  // never got as far as running anything.
  if (!`${stdout}${stderr}`.includes("Test Files")) {
    return {
      verdict: "error",
      detail: `vitest exited ${result.status} without running a test file (no "Test Files" summary) — suite.args is probably stale`,
    };
  }
  return { verdict: "killed", detail: null };
}

// Full structural validation up front, so a malformed spec is reported as a
// named failure rather than surfacing later as a TypeError on a nested field.
function validateSpec(spec) {
  for (const field of ["id", "target", "suite", "expect"]) {
    if (!(field in spec)) return `spec missing required field "${field}"`;
  }
  if (spec.expect !== "killed" && spec.expect !== "survives") {
    return `expect must be "killed" or "survives", got ${JSON.stringify(spec.expect)}`;
  }
  const { target, suite } = spec;
  if (typeof target !== "object" || target === null)
    return "target must be an object";
  for (const field of ["file", "find", "replace"]) {
    if (typeof target[field] !== "string")
      return `target.${field} must be a string`;
  }
  if (target.find === "") return "target.find must not be empty";
  if (target.find === target.replace) {
    return "target.find and target.replace are identical — this mutant changes nothing";
  }
  if (typeof suite !== "object" || suite === null)
    return "suite must be an object";
  if (suite.kind !== "pytest" && suite.kind !== "vitest") {
    return `suite.kind must be "pytest" or "vitest", got ${JSON.stringify(suite.kind)}`;
  }
  if (typeof suite.cwd !== "string") return "suite.cwd must be a string";
  if (
    !Array.isArray(suite.args) ||
    suite.args.some((a) => typeof a !== "string")
  ) {
    return "suite.args must be an array of strings";
  }
  return null;
}

// Reads the target and confirms the mutation site is still uniquely locatable.
// Shared by --check and the real run so the two cannot disagree.
function locateTarget(spec) {
  const targetPath = join(REPO_ROOT, spec.target.file);
  let original;
  try {
    original = readFileSync(targetPath, "utf-8");
  } catch (err) {
    return { error: `cannot read target ${spec.target.file}: ${err.message}` };
  }
  const occurrences = countOccurrences(original, spec.target.find);
  if (occurrences !== 1) {
    return {
      error: `spec.target.find matched ${occurrences} time(s) in ${spec.target.file}, need exactly 1 (spec is stale or ambiguous — update the "find" string)`,
    };
  }
  return { targetPath, original };
}

// Refuses to mutate a file that already has uncommitted edits: the restore
// writes back the snapshot taken before mutating, so on a dirty file an
// ill-timed interrupt could hand back a stale version of the developer's own
// in-progress work. Unknown git state is a warning, not a refusal — the tool
// still has to work outside a checkout.
function assertTargetCommitted(relFile) {
  const probe = spawnSync("git", ["status", "--porcelain", "--", relFile], {
    cwd: REPO_ROOT,
    encoding: "utf-8",
  });
  if (probe.error || probe.status !== 0) return null;
  if (probe.stdout.trim() !== "") {
    return `${relFile} has uncommitted changes — commit or stash them first (the harness restores a pre-mutation snapshot and will not risk your working copy)`;
  }
  return null;
}

function acquireLock() {
  try {
    closeSync(openSync(LOCK_PATH, "wx"));
    lockHeld = true;
    return null;
  } catch (err) {
    if (err.code === "EEXIST") {
      return `another mutation run holds ${LOCK_PATH}. If no run is active, delete that file.`;
    }
    return `could not acquire lock: ${err.message}`;
  }
}

// --check: validate only. No mutation, no suite, safe on a dirty tree.
function checkOne({ path, spec, loadError }) {
  if (loadError)
    return { id: path, ok: false, reason: `unreadable spec: ${loadError}` };
  const invalid = validateSpec(spec);
  if (invalid) return { id: spec.id ?? path, ok: false, reason: invalid };
  const located = locateTarget(spec);
  if (located.error) return { id: spec.id, ok: false, reason: located.error };
  return {
    id: spec.id,
    ok: true,
    reason: `spec valid; target.find still matches uniquely`,
  };
}

function runOne({ path, spec, loadError }) {
  if (loadError) {
    return { id: path, ok: false, reason: `unreadable spec: ${loadError}` };
  }
  const invalid = validateSpec(spec);
  if (invalid) {
    return { id: spec.id ?? path, ok: false, reason: invalid };
  }

  const dirty = assertTargetCommitted(spec.target.file);
  if (dirty) {
    return { id: spec.id, ok: false, reason: dirty };
  }

  const located = locateTarget(spec);
  if (located.error) {
    return { id: spec.id, ok: false, reason: located.error };
  }
  const { targetPath, original } = located;

  // Spliced by index rather than String.replace(): replace() would interpret
  // $&, $`, $' and $$ inside spec.target.replace as substitution patterns and
  // silently write a different mutant than the spec declares. `find` is known
  // to occur exactly once, so indexOf is unambiguous.
  const at = original.indexOf(spec.target.find);
  const mutated =
    original.slice(0, at) +
    spec.target.replace +
    original.slice(at + spec.target.find.length);

  pendingRestore = { path: targetPath, original };
  let outcome;
  try {
    writeFileSync(targetPath, mutated, "utf-8");
    outcome = runSuite(spec.suite);
  } finally {
    restorePending();
  }

  // A suite that never reached a verdict is a harness/spec failure, and is
  // deliberately not compared against `expect` — reporting it as a kill or a
  // survival would be inventing a result nothing measured.
  if (outcome.verdict === "error") {
    return {
      id: spec.id,
      ok: false,
      reason: outcome.detail,
      stdout: outcome.stdout,
      stderr: outcome.stderr,
    };
  }

  const actual = outcome.verdict;
  const ok = actual === spec.expect;
  return {
    id: spec.id,
    ok,
    reason: ok
      ? `mutant ${actual} as expected`
      : `expected the mutant to be "${spec.expect}" but it "${actual}" — ${
          actual === "survives"
            ? "no test in the named suite actually exercises this behavior"
            : "unexpected: the suite caught something the spec didn't predict, or the target drifted"
        }`,
    stdout: outcome.stdout,
    stderr: outcome.stderr,
  };
}

function main() {
  const argv = process.argv.slice(2);
  const checkOnly = argv.includes("--check");
  const specs = loadSpecs(argv.filter((a) => a !== "--check"));
  if (specs.length === 0) {
    console.error("no mutation specs found");
    process.exitCode = 1;
    return;
  }

  let results;
  if (checkOnly) {
    results = specs.map(checkOne);
  } else {
    // Taken once for the whole sweep, not per spec: the window that needs
    // protecting is "this process has a mutation somewhere in the tree".
    const lockError = acquireLock();
    if (lockError) {
      console.error(lockError);
      process.exitCode = 1;
      return;
    }
    try {
      results = specs.map(runOne);
    } finally {
      releaseLock();
    }
  }

  let failures = 0;
  for (const r of results) {
    const marker = r.ok ? "PASS" : "FAIL";
    console.log(`[${marker}] ${r.id} — ${r.reason}`);
    if (!r.ok) {
      failures += 1;
      if (r.stdout) console.log(indent(r.stdout));
      if (r.stderr) console.log(indent(r.stderr));
    }
  }

  console.log("");
  console.log(
    checkOnly
      ? `${results.length - failures}/${results.length} specs are valid and still match their target.`
      : `${results.length - failures}/${results.length} mutants behaved as their spec declared.`
  );
  // Not process.exit(): that tears the process down before a piped stdout has
  // necessarily flushed, which can swallow the summary line in CI logs.
  process.exitCode = failures > 0 ? 1 : 0;
}

// The TAIL, not the head: pytest and vitest both print the banner and
// collection noise first and the actual failure detail last, so the first 20
// lines are reliably the least useful 20 lines.
function indent(text) {
  const lines = text.trimEnd().split("\n");
  const tail = lines.slice(-30);
  const elided = lines.length - tail.length;
  const shown =
    elided > 0 ? [`... ${elided} earlier line(s) elided ...`, ...tail] : tail;
  return shown.map((line) => `    ${line}`).join("\n");
}

main();
