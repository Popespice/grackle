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
// duration of one suite run), even if the suite command itself throws.

import { spawnSync } from "node:child_process";
import { readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(HERE, "..", "..");
const SPECS_DIR = join(HERE, "specs");

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
    throw new Error(`unknown suite.kind: ${suite.kind}`);
  }
  const result = spawnSync(cmd, args, {
    cwd,
    stdio: "pipe",
    encoding: "utf-8",
  });
  return {
    passed: result.status === 0,
    stdout: result.stdout ?? "",
    stderr: result.stderr ?? "",
    spawnError: result.error ?? null,
  };
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

  const mutated = original.replace(spec.target.find, spec.target.replace);
  writeFileSync(targetPath, mutated, "utf-8");

  let outcome;
  try {
    outcome = runSuite(spec.suite);
  } finally {
    writeFileSync(targetPath, original, "utf-8");
  }

  if (outcome.spawnError) {
    return {
      id: spec.id,
      ok: false,
      reason: `failed to launch suite: ${outcome.spawnError.message}`,
    };
  }

  const actual = outcome.passed ? "survives" : "killed";
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
