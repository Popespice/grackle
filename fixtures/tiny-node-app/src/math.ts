// Erasable-only TypeScript (type annotations / return types only) so Node's
// `--experimental-strip-types` runs it directly — no enums, namespaces, or
// parameter properties (those are non-erasable and out of scope for Phase 8.5).

export function add(a: number, b: number): number {
  return a + b;
}

export function fib(n: number): number {
  if (n < 2) {
    return n;
  }
  return fib(n - 1) + fib(n - 2);
}

export function busy(rounds: number): number {
  let total: number = 0;
  for (let i = 0; i < rounds; i++) {
    total = add(total, i % 7);
  }
  return total;
}

// Note on trace.golden.jsonl: `add` completes in nanoseconds and V8's TurboFan
// JIT inlines it into `busy` after enough calls, so grackle's CPU-sampling
// Node tracer (~250us interval) essentially never catches a standalone `add`
// frame regardless of call count — the golden trace correctly shows 0 events
// for it even though `busy` calls it millions of times. This is a real,
// expected characteristic of sampling profilers (matches Chrome DevTools /
// clinic.js behavior on the same workload), not a bug in the fixture or the
// adapter — see the demo branch's DEMO_BRANCH.md for the caveat surfaced to
// visitors.
