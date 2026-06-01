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
