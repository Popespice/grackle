// Entry point traced by `grackle trace src/main.ts`. Top-level evaluation does
// real CPU work (deep recursion + a tight loop) so the sampling profiler and the
// precise-coverage poller both observe the project functions.

import { busy, fib } from "./math.ts";

function run(): number {
  const a: number = fib(30);
  const b: number = busy(2_000_000);
  return a + b;
}

const result: number = run();
if (result < 0) {
  // Unreachable for these inputs; keeps `result` observably used.
  console.log(result);
}
