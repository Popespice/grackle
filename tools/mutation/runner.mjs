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
// Usage:
//   node tools/mutation/runner.mjs                  # run every spec in specs/
//   node tools/mutation/runner.mjs specs/foo.json    # run one spec
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
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, "..", "..");
const SPECS_DIR = join(HERE, "specs");

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

process.on("exit", restorePending);
for (const signal of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(signal, () => {
    restorePending();
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
  return paths.map((p) => ({
    path: p,
    spec: JSON.parse(readFileSync(p, "utf-8")),
  }));
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

function runOne({ path, spec }) {
  const requiredFields = ["id", "target", "suite", "expect"];
  for (const field of requiredFields) {
    if (!(field in spec)) {
      return {
        id: spec.id ?? path,
        ok: false,
        reason: `spec missing required field "${field}"`,
      };
    }
  }
  if (spec.expect !== "killed" && spec.expect !== "survives") {
    return {
      id: spec.id,
      ok: false,
      reason: `expect must be "killed" or "survives", got ${JSON.stringify(spec.expect)}`,
    };
  }

  const targetPath = join(REPO_ROOT, spec.target.file);
  const original = readFileSync(targetPath, "utf-8");
  const occurrences = countOccurrences(original, spec.target.find);
  if (occurrences !== 1) {
    return {
      id: spec.id,
      ok: false,
      reason: `spec.target.find matched ${occurrences} time(s) in ${spec.target.file}, need exactly 1 (spec is stale or ambiguous — update the "find" string)`,
    };
  }

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
  const specs = loadSpecs(argv);
  if (specs.length === 0) {
    console.error("no mutation specs found");
    process.exit(1);
  }

  const results = specs.map(runOne);

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
    `${results.length - failures}/${results.length} mutants behaved as their spec declared.`
  );
  process.exit(failures > 0 ? 1 : 0);
}

function indent(text) {
  return text
    .split("\n")
    .slice(0, 20)
    .map((line) => `    ${line}`)
    .join("\n");
}

main();
