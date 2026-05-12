#!/usr/bin/env node
/**
 * Root-level parity check entry point.
 * Delegates to packages/shared-types/scripts/verify-parity.mjs.
 *
 * Usage: node tools/check-parity.mjs
 *   (also invoked via `pnpm check-parity`)
 */

import { main } from "../packages/shared-types/scripts/verify-parity.mjs";

main().catch((err) => {
  console.error(err.message ?? err);
  process.exit(1);
});
