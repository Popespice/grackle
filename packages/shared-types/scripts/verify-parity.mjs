#!/usr/bin/env node

/**
 * Parity guard: regenerates types into a tmp dir and diffs against the locally
 * existing outputs (gitignored; created by the last codegen run). Exits non-zero
 * if drift is detected — meaning codegen was not run after the last schema change.
 *
 * Called by `pnpm check-parity` (root) and by `pnpm verify-parity`
 * (shared-types package).
 */

import { existsSync } from "node:fs";
import { mkdtemp, readdir, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { main as runCodegen } from "./codegen.mjs";

const SCRIPT_DIR = fileURLToPath(new URL(".", import.meta.url));
const PACKAGE_DIR = resolve(SCRIPT_DIR, "..");
const ROOT = resolve(PACKAGE_DIR, "../..");

const SCHEMA_DIR = join(PACKAGE_DIR, "schema");
const TS_OUT_DIR = join(PACKAGE_DIR, "src", "generated");
const PY_OUT_DIR = join(
  ROOT,
  "packages",
  "agent",
  "src",
  "grackle",
  "_generated"
);
const AGENT_DIR = join(ROOT, "packages", "agent");

const MESSAGES_SCHEMA = join(SCHEMA_DIR, "messages.schema.json");
const MESSAGES_TS = join(PACKAGE_DIR, "src", "messages.ts");

async function readIfExists(path) {
  return existsSync(path) ? readFile(path, { encoding: "utf-8" }) : null;
}

/** Collect every message `type` const from messages.schema.json. */
function schemaMessageTypes(schema) {
  const out = new Set();
  for (const def of Object.values(schema.$defs ?? {})) {
    for (const branch of def.allOf ?? []) {
      const c = branch.properties?.type?.const;
      if (typeof c === "string") out.add(c);
    }
  }
  return out;
}

/** Parse the KNOWN_MESSAGE_TYPES string-literal array from messages.ts source. */
function knownMessageTypes(src) {
  const block = src.match(
    /KNOWN_MESSAGE_TYPES\s*=\s*\[([\s\S]*?)\]\s*as const/
  );
  if (!block) {
    throw new Error("KNOWN_MESSAGE_TYPES array not found in messages.ts");
  }
  return new Set([...block[1].matchAll(/"([^"]+)"/g)].map((m) => m[1]));
}

/**
 * Collect the `type:` literal of every interface/type named in the
 * AnyKnownMessage union. Returns the set of those literals.
 */
function unionMessageTypes(src) {
  const block = src.match(/AnyKnownMessage\s*=([\s\S]*?);/);
  if (!block) {
    throw new Error("AnyKnownMessage union not found in messages.ts");
  }
  const names = [...block[1].matchAll(/\|\s*([A-Za-z0-9_]+)/g)].map(
    (m) => m[1]
  );
  if (names.length === 0) {
    throw new Error("AnyKnownMessage union has no members");
  }
  const out = new Set();
  for (const name of names) {
    // interface Foo extends ... { ... type: "x"; ... }
    // or: type Foo = ... & { type: "x"; ... }
    const decl = src.match(
      new RegExp(`(?:interface|type)\\s+${name}\\b[\\s\\S]*?type:\\s*"([^"]+)"`)
    );
    if (!decl) {
      throw new Error(`AnyKnownMessage member ${name}: no type: literal found`);
    }
    out.add(decl[1]);
  }
  return out;
}

function diffSets(label, a, b, aName, bName) {
  const onlyA = [...a].filter((x) => !b.has(x)).sort();
  const onlyB = [...b].filter((x) => !a.has(x)).sort();
  if (onlyA.length === 0 && onlyB.length === 0) {
    console.log(`  OK       ${label}`);
    return 0;
  }
  if (onlyA.length) {
    console.error(
      `  DRIFT    ${label}: in ${aName} not ${bName}: ${onlyA.join(", ")}`
    );
  }
  if (onlyB.length) {
    console.error(
      `  DRIFT    ${label}: in ${bName} not ${aName}: ${onlyB.join(", ")}`
    );
  }
  return 1;
}

/** Assert schema type consts === KNOWN_MESSAGE_TYPES === AnyKnownMessage members. */
export async function checkCanonicalParity() {
  const schema = JSON.parse(
    await readFile(MESSAGES_SCHEMA, { encoding: "utf-8" })
  );
  const tsSrc = await readFile(MESSAGES_TS, { encoding: "utf-8" });

  const schemaTypes = schemaMessageTypes(schema);
  const knownTypes = knownMessageTypes(tsSrc);
  const unionTypes = unionMessageTypes(tsSrc);

  let failures = 0;
  failures += diffSets(
    "schema ↔ KNOWN_MESSAGE_TYPES",
    schemaTypes,
    knownTypes,
    "schema",
    "KNOWN_MESSAGE_TYPES"
  );
  failures += diffSets(
    "schema ↔ AnyKnownMessage",
    schemaTypes,
    unionTypes,
    "schema",
    "AnyKnownMessage"
  );
  return failures;
}

export async function main() {
  const tmpTs = await mkdtemp(join(tmpdir(), "grackle-parity-ts-"));
  const tmpPy = await mkdtemp(join(tmpdir(), "grackle-parity-py-"));
  let failures = 0;

  try {
    await runCodegen({ tsOutDir: tmpTs, pyOutDir: tmpPy });

    const schemaFiles = (await readdir(SCHEMA_DIR))
      .filter((f) => f.endsWith(".schema.json"))
      .sort();

    for (const schemaFile of schemaFiles) {
      const baseName = schemaFile.replace(".schema.json", "");

      // TypeScript parity
      const expectedTs = await readIfExists(join(tmpTs, `${baseName}.ts`));
      const actualTs = await readIfExists(join(TS_OUT_DIR, `${baseName}.ts`));
      if (expectedTs !== null) {
        if (actualTs === null) {
          console.error(
            `  MISSING  src/generated/${baseName}.ts — run \`pnpm codegen\``
          );
          failures++;
        } else if (actualTs !== expectedTs) {
          console.error(
            `  DRIFT    src/generated/${baseName}.ts — run \`pnpm codegen\``
          );
          failures++;
        } else {
          console.log(`  OK       src/generated/${baseName}.ts`);
        }
      }

      // Python parity (only when agent package exists)
      if (existsSync(AGENT_DIR)) {
        const expectedPy = await readIfExists(join(tmpPy, `${baseName}.py`));
        const actualPy = await readIfExists(join(PY_OUT_DIR, `${baseName}.py`));
        if (expectedPy !== null) {
          if (actualPy === null) {
            console.error(
              `  MISSING  _generated/${baseName}.py — run \`pnpm codegen\``
            );
            failures++;
          } else if (actualPy !== expectedPy) {
            console.error(
              `  DRIFT    _generated/${baseName}.py — run \`pnpm codegen\``
            );
            failures++;
          } else {
            console.log(`  OK       _generated/${baseName}.py`);
          }
        }
      }
    }
  } finally {
    await rm(tmpTs, { recursive: true, force: true });
    await rm(tmpPy, { recursive: true, force: true });
  }

  failures += await checkCanonicalParity();

  if (failures > 0) {
    console.error(`\nparity: ${failures} file(s) out of sync`);
    process.exit(1);
  }
  console.log("\nparity: all files up to date");
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch((err) => {
    console.error("verify-parity failed:", err.message ?? err);
    process.exit(1);
  });
}
