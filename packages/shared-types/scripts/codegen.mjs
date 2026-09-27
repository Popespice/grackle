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

/** Whether codegen emits Python output at all (it skips it without the agent package). */
export function generatesPython() {
  return existsSync(AGENT_DIR);
}

const TS_HEADER =
  "// GENERATED — do not edit by hand. Run `pnpm codegen` to regenerate.\n" +
  "// Source: packages/shared-types/schema/\n";

const PY_HEADER =
  "# GENERATED — do not edit by hand. Run `pnpm codegen` to regenerate.\n" +
  "# Source: packages/shared-types/schema/";

// Resolved once per process: the generator version this process pins every
// datamodel-codegen call to. `uvx --from datamodel-code-generator` is otherwise
// unpinned, so two calls could resolve different releases; pinning each run to
// the version it logs makes the logged version the one that produced the output.
// The probe is not an extra cost in practice: the first unpinned resolution
// (~1.5s cold) is paid by whichever call runs first, and pinned calls after it
// hit uv's cache (~0.06s).
let pinnedGenerator = null;

/**
 * Resolve, log, and return the `--from` requirement for datamodel-code-generator.
 *
 * Never fatal. This is diagnostics plus pinning, not generation — if the probe
 * fails, generation falls back to the unpinned requirement and reports its own
 * real error, and the TypeScript half of codegen (which needs no Python
 * toolchain at all) still completes.
 */
async function resolveGenerator() {
  if (pinnedGenerator === null) {
    try {
      const { stdout } = await execFileAsync("uvx", [
        "--from",
        "datamodel-code-generator",
        "datamodel-codegen",
        "--version",
      ]);
      const version = stdout.trim().match(/(\d+\.\d+\.\d+\S*)\s*$/)?.[1];
      pinnedGenerator = version
        ? { from: `datamodel-code-generator==${version}`, label: version }
        : {
            from: "datamodel-code-generator",
            label: `unparsed (${stdout.trim()}) — generation unpinned`,
          };
    } catch (err) {
      pinnedGenerator = {
        from: "datamodel-code-generator",
        label: `unavailable (${err.message ?? err}) — generation unpinned`,
      };
    }
  }
  console.log(
    `  Py  \u24d8 datamodel-code-generator pinned for this run: ${pinnedGenerator.label}`
  );
  return pinnedGenerator.from;
}

/**
 * Run codegen. Accepts alternate output dirs so verify-parity can use a tmp dir,
 * and an alternate schema dir so tests can point codegen at a single schema (or
 * a temp copy of the schema dir) without touching the real one.
 * @param {{ tsOutDir?: string; pyOutDir?: string; schemaDir?: string }} [opts]
 */
export async function main(opts = {}) {
  const tsOutDir = opts.tsOutDir ?? DEFAULT_TS_OUT;
  const pyOutDir = opts.pyOutDir ?? DEFAULT_PY_OUT;
  const schemaDir = opts.schemaDir ?? SCHEMA_DIR;
  const generatePython = generatesPython();

  await mkdir(tsOutDir, { recursive: true });

  const schemaFiles = (await readdir(schemaDir))
    .filter((f) => f.endsWith(".schema.json"))
    .sort();

  const generatorFrom =
    generatePython && schemaFiles.length > 0 ? await resolveGenerator() : null;

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
        generatorFrom,
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
