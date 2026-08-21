#!/usr/bin/env node

/**
 * Guard-of-the-guards meta-tests for verify-parity.mjs (test campaign tier T3,
 * docs/test-campaigns/phase-12.md). These exercise the parity guard's own
 * extraction/comparison logic directly — no full `main()` codegen run except
 * where T3-6 specifically calls for it.
 *
 * Uses node:test / node:assert (no new devDependency — see tools/mutation/
 * for the repo's existing dependency-free-tooling precedent). Run with:
 *   node --test scripts/*.test.mjs
 */

import assert from "node:assert/strict";
import {
  cp,
  mkdtemp,
  readdir,
  readFile,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { main as runCodegen } from "./codegen.mjs";
import { diffSets, schemaMessageTypes } from "./verify-parity.mjs";

const SCRIPT_DIR = fileURLToPath(new URL(".", import.meta.url));
const SCHEMA_DIR = join(SCRIPT_DIR, "..", "schema");
const MESSAGES_SCHEMA = join(SCHEMA_DIR, "messages.schema.json");

/**
 * Run `fn` (sync) with console.log/console.error captured instead of printed,
 * so test output stays readable. Returns { result, logs, errors }.
 */
function captureConsole(fn) {
  const logs = [];
  const errors = [];
  const origLog = console.log;
  const origError = console.error;
  console.log = (...args) => logs.push(args.join(" "));
  console.error = (...args) => errors.push(args.join(" "));
  let result;
  try {
    result = fn();
  } finally {
    console.log = origLog;
    console.error = origError;
  }
  return { result, logs, errors };
}

async function loadRealSchemaTypes() {
  const schema = JSON.parse(
    await readFile(MESSAGES_SCHEMA, { encoding: "utf-8" })
  );
  return schemaMessageTypes(schema);
}

// ---------------------------------------------------------------------------
// A) T3-1 — the guard genuinely catches drift (positive cases)
// ---------------------------------------------------------------------------

test("T3-1: diffSets reports failure when a type is added to one side", async () => {
  const real = await loadRealSchemaTypes();
  assert.ok(real.size > 0, "sanity: the real schema must have message types");

  const withExtra = new Set([...real, "__synthetic_extra_type__"]);
  const { result } = captureConsole(() =>
    diffSets("added", real, withExtra, "a", "b")
  );

  assert.equal(
    result,
    1,
    "adding a type to one side must be reported as drift"
  );
  const onlyB = [...withExtra].filter((x) => !real.has(x));
  assert.deepEqual(onlyB, ["__synthetic_extra_type__"]);
});

test("T3-1: diffSets reports failure when a type is removed from one side", async () => {
  const real = await loadRealSchemaTypes();
  const [removed] = real;
  const withoutOne = new Set(real);
  withoutOne.delete(removed);

  const { result } = captureConsole(() =>
    diffSets("removed", real, withoutOne, "a", "b")
  );

  assert.equal(
    result,
    1,
    "removing a type from one side must be reported as drift"
  );
  const onlyA = [...real].filter((x) => !withoutOne.has(x));
  assert.deepEqual(onlyA, [removed]);
});

test("T3-1: diffSets reports failure when a type is renamed", async () => {
  const real = await loadRealSchemaTypes();
  const [target] = real;
  const renamed = new Set(real);
  renamed.delete(target);
  renamed.add("__synthetic_renamed_type__");

  const { result } = captureConsole(() =>
    diffSets("renamed", real, renamed, "a", "b")
  );

  assert.equal(result, 1, "renaming a type must be reported as drift");
  const onlyA = [...real].filter((x) => !renamed.has(x));
  const onlyB = [...renamed].filter((x) => !real.has(x));
  assert.deepEqual(onlyA, [target]);
  assert.deepEqual(onlyB, ["__synthetic_renamed_type__"]);
});

test("T3-1: diffSets reports success when the two sides are identical (sanity baseline)", async () => {
  const real = await loadRealSchemaTypes();
  const copy = new Set(real);

  const { result } = captureConsole(() =>
    diffSets("identical", real, copy, "a", "b")
  );

  assert.equal(result, 0, "identical sets must not be reported as drift");
});

// ---------------------------------------------------------------------------
// B) Regression test for the vacuity fix (both sides extracting zero types
//    must now be a failure, not a silent "OK"). Self-verified against the
//    pre-fix code by temporarily reverting the fix locally and re-running
//    this test (it failed, as expected) before re-applying the fix.
// ---------------------------------------------------------------------------

test("regression: diffSets fails (not passes vacuously) when both sides extracted zero types", () => {
  const { result, errors } = captureConsole(() =>
    diffSets(
      "schema ↔ KNOWN_MESSAGE_TYPES",
      new Set(),
      new Set(),
      "schema",
      "KNOWN_MESSAGE_TYPES"
    )
  );

  assert.equal(
    result,
    1,
    "comparing two empty sets must be treated as a guard failure, not silent success"
  );
  assert.ok(
    errors.some((line) => /zero message types/i.test(line)),
    `expected a clear "extracted zero message types" error message, got: ${JSON.stringify(errors)}`
  );
});

// ---------------------------------------------------------------------------
// C) T3-2 — KNOWN GAP, not fixed here
// ---------------------------------------------------------------------------

// Confirmed defect, tracked in docs/test-campaigns/phase-12.md (tier T3-2). Fix deferred
// to a later campaign chunk (Additive, never destructive: this campaign chunk (C1) only
// writes probes; T3-2's actual fix is out of scope here). If this ever starts returning a
// non-empty set, this test will fail — update it to assert the fixed behavior and delete
// this comment.
test("KNOWN GAP (T3-2): a $def authored under Draft-07 definitions is silently invisible to schemaMessageTypes", () => {
  const schema = {
    definitions: {
      Foo: { allOf: [{ properties: { type: { const: "foo" } } }] },
    },
  };

  const types = schemaMessageTypes(schema);

  assert.equal(types.size, 0);
  assert.ok(!types.has("foo"));
});

// ---------------------------------------------------------------------------
// D) T3-3 — KNOWN GAP, not fixed here (two sub-cases)
// ---------------------------------------------------------------------------

// Confirmed defect, tracked in docs/test-campaigns/phase-12.md (tier T3-3). Fix deferred
// to a later campaign chunk (Additive, never destructive: this campaign chunk (C1) only
// writes probes; T3-3's actual fix is out of scope here). If this ever starts returning a
// non-empty set, this test will fail — update it to assert the fixed behavior and delete
// this comment.
test("KNOWN GAP (T3-3): a type declared via oneOf instead of allOf is invisible to schemaMessageTypes", () => {
  const schema = {
    $defs: {
      Bar: { oneOf: [{ properties: { type: { const: "bar" } } }] },
    },
  };

  const types = schemaMessageTypes(schema);

  assert.equal(types.size, 0);
  assert.ok(!types.has("bar"));
});

// Confirmed defect, tracked in docs/test-campaigns/phase-12.md (tier T3-3). Fix deferred
// to a later campaign chunk (Additive, never destructive: this campaign chunk (C1) only
// writes probes; T3-3's actual fix is out of scope here). If this ever starts returning a
// non-empty set, this test will fail — update it to assert the fixed behavior and delete
// this comment.
test("KNOWN GAP (T3-3): duplicate type consts across different $defs collapse silently into one Set entry", () => {
  const schema = {
    $defs: {
      Baz1: { allOf: [{ properties: { type: { const: "dup" } } }] },
      Baz2: { allOf: [{ properties: { type: { const: "dup" } } }] },
    },
  };

  const types = schemaMessageTypes(schema);

  assert.equal(
    types.size,
    1,
    "two distinct $defs producing the same type const are indistinguishable in the resulting Set"
  );
  assert.ok(types.has("dup"));
});

// ---------------------------------------------------------------------------
// E) T3-6 — codegen determinism, made real/committed (previously a manual,
//    one-time Phase-1 check; this is the first time it's committed as code).
//    Runs against a single representative schema (messages.schema.json) via
//    a temp schema-dir copy, not the whole schema directory, to bound wall-time.
// ---------------------------------------------------------------------------

test("T3-6: codegen is byte-for-byte deterministic across two runs of the same schema", async () => {
  const schemaTmp = await mkdtemp(join(tmpdir(), "grackle-codegen-schema-"));
  const tsOut1 = await mkdtemp(join(tmpdir(), "grackle-codegen-ts1-"));
  const tsOut2 = await mkdtemp(join(tmpdir(), "grackle-codegen-ts2-"));
  const pyOut1 = await mkdtemp(join(tmpdir(), "grackle-codegen-py1-"));
  const pyOut2 = await mkdtemp(join(tmpdir(), "grackle-codegen-py2-"));

  try {
    await cp(MESSAGES_SCHEMA, join(schemaTmp, "messages.schema.json"));

    await runCodegen({
      schemaDir: schemaTmp,
      tsOutDir: tsOut1,
      pyOutDir: pyOut1,
    });
    await runCodegen({
      schemaDir: schemaTmp,
      tsOutDir: tsOut2,
      pyOutDir: pyOut2,
    });

    const ts1 = await readFile(join(tsOut1, "messages.ts"), {
      encoding: "utf-8",
    });
    const ts2 = await readFile(join(tsOut2, "messages.ts"), {
      encoding: "utf-8",
    });
    assert.equal(
      ts1,
      ts2,
      "TypeScript codegen output must be byte-identical across runs"
    );

    const py1 = await readFile(join(pyOut1, "messages.py"), {
      encoding: "utf-8",
    });
    const py2 = await readFile(join(pyOut2, "messages.py"), {
      encoding: "utf-8",
    });
    assert.equal(
      py1,
      py2,
      "Python codegen output must be byte-identical across runs"
    );
  } finally {
    await Promise.all(
      [schemaTmp, tsOut1, tsOut2, pyOut1, pyOut2].map((d) =>
        rm(d, { recursive: true, force: true })
      )
    );
  }
});

// ---------------------------------------------------------------------------
// F) T3-6 — KNOWN GAP, not fixed here: non-.schema.json filenames are
//    silently skipped by codegen.
// ---------------------------------------------------------------------------

// Confirmed defect, tracked in docs/test-campaigns/phase-12.md (tier T3-6). Fix deferred
// to a later campaign chunk (Additive, never destructive: this campaign chunk (C1) only
// writes probes; the actual fix is out of scope here). If this ever starts erroring or
// producing output for the bogus file, this test will fail — update it to assert the
// fixed behavior and delete this comment.
test("KNOWN GAP (T3-6): a non-.schema.json filename in the schema dir is silently skipped by codegen", async () => {
  const schemaTmp = await mkdtemp(
    join(tmpdir(), "grackle-codegen-bogus-schema-")
  );
  const tsOut = await mkdtemp(join(tmpdir(), "grackle-codegen-bogus-ts-"));
  const pyOut = await mkdtemp(join(tmpdir(), "grackle-codegen-bogus-py-"));

  try {
    await cp(MESSAGES_SCHEMA, join(schemaTmp, "messages.schema.json"));
    await writeFile(
      join(schemaTmp, "bogus.json"),
      JSON.stringify({
        $defs: {
          Bogus: { allOf: [{ properties: { type: { const: "bogus" } } }] },
        },
      })
    );

    await assert.doesNotReject(() =>
      runCodegen({ schemaDir: schemaTmp, tsOutDir: tsOut, pyOutDir: pyOut })
    );

    const tsFiles = await readdir(tsOut);
    assert.deepEqual(tsFiles.sort(), ["messages.ts"]);

    const pyFiles = await readdir(pyOut);
    assert.deepEqual(pyFiles.sort(), ["messages.py"]);
  } finally {
    await Promise.all(
      [schemaTmp, tsOut, pyOut].map((d) =>
        rm(d, { recursive: true, force: true })
      )
    );
  }
});
