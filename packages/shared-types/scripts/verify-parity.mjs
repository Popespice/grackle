#!/usr/bin/env node

/**
 * Parity guard: regenerates types into a tmp dir and diffs against committed
 * outputs. Exits non-zero if drift is detected.
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

async function readIfExists(path) {
  return existsSync(path) ? readFile(path, { encoding: "utf-8" }) : null;
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
