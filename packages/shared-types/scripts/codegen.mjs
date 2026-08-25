#!/usr/bin/env node

/**
 * Schema → TypeScript + Python codegen.
 * Cross-platform: uses node:path throughout, no shell-specific tools.
 *
 * TS output:  packages/shared-types/src/generated/
 * Python output: packages/agent/src/grackle/_generated/
 *   (skipped if packages/agent/ does not yet exist)
 *
 * Accepts an optional `opts` argument for alternate output dirs (used by
 * verify-parity to generate into a tmp directory for diffing).
 */

import { execFile } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdir, readdir, readFile, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";
import { compile } from "json-schema-to-typescript";

const execFileAsync = promisify(execFile);

const SCRIPT_DIR = fileURLToPath(new URL(".", import.meta.url));
const PACKAGE_DIR = resolve(SCRIPT_DIR, "..");
const ROOT = resolve(PACKAGE_DIR, "../..");

const SCHEMA_DIR = join(PACKAGE_DIR, "schema");
const DEFAULT_TS_OUT = join(PACKAGE_DIR, "src", "generated");
const DEFAULT_PY_OUT = join(
  ROOT,
  "packages",
  "agent",
  "src",
  "grackle",
  "_generated"
);
const AGENT_DIR = join(ROOT, "packages", "agent");

const TS_HEADER =
  "// GENERATED — do not edit by hand. Run `pnpm codegen` to regenerate.\n" +
  "// Source: packages/shared-types/schema/\n";

const PY_HEADER =
  "# GENERATED — do not edit by hand. Run `pnpm codegen` to regenerate.\n" +
  "# Source: packages/shared-types/schema/";

/**
 * Run codegen. Accepts alternate output dirs so verify-parity can use a tmp dir,
 * and an alternate schema dir so tests can point codegen at a single schema (or
 * a temp copy of the schema dir) without touching the real one.
 * @param {{ tsOutDir?: string; pyOutDir?: string; schemaDir?: string }} [opts]
 */
// Resolved once per process, not per main() call: the probe costs a full uvx
// spawn (~1.3s, comparable to the generation it precedes), and verify-parity's
// tests call main() several times in one process.
let resolvedGeneratorVersion = null;

/**
 * Log which datamodel-code-generator `uvx` actually resolved. The version is
 * deliberately unpinned, so recording it is the only thing making drift
 * visible after the fact.
 *
 * Never fatal. This is diagnostics, not generation — if it fails, the real
 * datamodel-codegen call later in the loop reports the real error, and the
 * TypeScript half of codegen (which needs no Python toolchain at all) still
 * completes rather than being blocked by a broken probe.
 */
async function logResolvedGeneratorVersion() {
  if (resolvedGeneratorVersion === null) {
    try {
      const { stdout } = await execFileAsync("uvx", [
        "--from",
        "datamodel-code-generator",
        "datamodel-codegen",
        "--version",
      ]);
      resolvedGeneratorVersion = stdout.trim();
    } catch (err) {
      resolvedGeneratorVersion = `unavailable (${err.message ?? err})`;
    }
  }
  console.log(
    `  Py  \u24d8 datamodel-code-generator resolved version: ${resolvedGeneratorVersion}`
  );
}

export async function main(opts = {}) {
  const tsOutDir = opts.tsOutDir ?? DEFAULT_TS_OUT;
  const pyOutDir = opts.pyOutDir ?? DEFAULT_PY_OUT;
  const schemaDir = opts.schemaDir ?? SCHEMA_DIR;
  const generatePython = existsSync(AGENT_DIR);

  await mkdir(tsOutDir, { recursive: true });

  const schemaFiles = (await readdir(schemaDir))
    .filter((f) => f.endsWith(".schema.json"))
    .sort();

  if (generatePython && schemaFiles.length > 0) {
    await logResolvedGeneratorVersion();
  }

  for (const schemaFile of schemaFiles) {
    const schemaPath = join(schemaDir, schemaFile);
    const baseName = schemaFile.replace(".schema.json", "");
    const schema = JSON.parse(
      await readFile(schemaPath, { encoding: "utf-8" })
    );

    // TypeScript — via json-schema-to-typescript
    const tsSource = await compile(schema, baseName, {
      bannerComment: TS_HEADER,
      unknownAny: false,
      enableConstEnums: false,
      unreachableDefinitions: true,
      style: { singleQuote: false, semi: true },
      cwd: schemaDir,
    });
    await writeFile(join(tsOutDir, `${baseName}.ts`), tsSource, {
      encoding: "utf-8",
    });
    console.log(`  TS  → src/generated/${baseName}.ts`);

    // Python — via uvx datamodel-code-generator
    if (generatePython) {
      await mkdir(pyOutDir, { recursive: true });
      await execFileAsync("uvx", [
        "--from",
        "datamodel-code-generator",
        "datamodel-codegen",
        "--input",
        schemaPath,
        "--input-file-type",
        "jsonschema",
        "--output",
        join(pyOutDir, `${baseName}.py`),
        "--output-model-type",
        "typing.TypedDict",
        "--target-python-version",
        "3.12",
        "--custom-file-header",
        PY_HEADER,
      ]);
      console.log(
        `  Py  → packages/agent/src/grackle/_generated/${baseName}.py`
      );
    } else {
      console.log(`  Py  → skipped (packages/agent not yet created)`);
    }
  }
}

// Only run when executed directly — not when imported by verify-parity.mjs
if (process.argv[1] === fileURLToPath(import.meta.url)) {
  console.log("codegen: running...");
  main().catch((err) => {
    console.error("codegen failed:", err.message ?? err);
    process.exit(1);
  });
}
