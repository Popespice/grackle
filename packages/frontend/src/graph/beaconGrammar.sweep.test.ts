import type { TraceEvent } from "@grackle/shared-types";
import { describe, expect, it } from "vitest";
import {
  type EpochPoint,
  FLOAT,
  parseFloatToken,
  scanEpochCandidates,
} from "./epochSeries";
import { type LayerStatsPoint, scanLayerStatsCandidates } from "./layerStats";
import {
  extractNetworkSpec,
  type LayerToken,
  type NetworkSpec,
} from "./networkSpec";

/**
 * Campaign T8-6 (`docs/test-campaigns/phase-12.md`): seeded, generated sweeps
 * over the three hand-written beacon grammars — the shared `FLOAT` token and
 * `parseFloatToken` decoder, `record_epoch`'s tuple regex, the arity-built
 * `record_layer_stats` regex, and `record_architecture`'s token string.
 *
 * The 12.4 tests pin a hand-picked adversarial corpus. This file adds
 * generated coverage without adding a dependency: a small seeded PRNG
 * (mulberry32) and plain loops. Every sweep has a fixed seed and a bounded
 * iteration count, and reports up to five failing inputs verbatim.
 *
 * Three kinds of property:
 *
 * - Round-trip. Values are formatted exactly as CPython's `repr()` would
 *   print them, parsed, and must come back unchanged. `pyFloatRepr` is the
 *   formatter; it was cross-checked against CPython 3.12 on about 300k random
 *   doubles while this file was written, and the table in the first test
 *   keeps a representative slice of that check.
 * - Robustness. Valid payloads are mutated (characters deleted, duplicated,
 *   swapped, replaced; wrong separators, wrong arity, truncation, padding,
 *   unicode digits, very long digit runs, broken quotes). The parser must not
 *   throw, must never accept a non-finite value, and must agree with a
 *   character-level reference recognizer written independently of the regexes.
 * - Consistency. One payload parsed along every path that parses it gives one
 *   answer: `record_epoch` vs `record_layer_stats` at one layer (they share the
 *   float grammar), the bare `FLOAT` fragment vs both tuple parsers, and an
 *   incremental resume at every split point vs one scan of the whole array.
 *
 * Two defects found here are ledgered as `it.fails` (see the end of this file,
 * and `panels/NetworkViewPanel.specResume.test.tsx`).
 */

// ── Deterministic generation ────────────────────────────────────────────────

/** mulberry32: a tiny 32-bit-state PRNG, deterministic per seed. */
function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

class Rng {
  private readonly nextFloat: () => number;

  constructor(seed: number) {
    this.nextFloat = mulberry32(seed);
  }

  next(): number {
    return this.nextFloat();
  }

  /** Uniform integer in `[lo, hi]`, inclusive. */
  int(lo: number, hi: number): number {
    return lo + Math.floor(this.nextFloat() * (hi - lo + 1));
  }

  u32(): number {
    return Math.floor(this.nextFloat() * 4294967296) >>> 0;
  }

  chance(p: number): boolean {
    return this.nextFloat() < p;
  }

  pick<T>(xs: readonly T[]): T {
    const x = xs[this.int(0, xs.length - 1)];
    if (x === undefined) throw new Error("pick() from an empty list");
    return x;
  }

  digits(n: number): string {
    let s = "";
    for (let i = 0; i < n; i++) s += String(this.int(0, 9));
    return s;
  }
}

// ── Reporting ───────────────────────────────────────────────────────────────

const MAX_REPORTED = 5;

/** Printable form of a payload: non-ASCII and control characters escaped,
 *  very long payloads elided in the middle. */
function show(s: string): string {
  let out = "";
  for (const ch of s) {
    const cp = ch.codePointAt(0) ?? 0;
    out +=
      cp >= 0x20 && cp < 0x7f && ch !== "\\" ? ch : `\\u{${cp.toString(16)}}`;
  }
  return out.length > 160
    ? `${out.slice(0, 70)} …[${s.length} chars]… ${out.slice(-70)}`
    : `"${out}"`;
}

/** Run `check` over every input, catching throws, and collect the first few
 *  failures with the input that caused each. */
function failuresOf<T>(
  label: string,
  inputs: Iterable<T>,
  check: (input: T) => string | null,
  describeInput: (input: T) => string
): string[] {
  const failures: string[] = [];
  let i = 0;
  for (const input of inputs) {
    let problem: string | null;
    try {
      problem = check(input);
    } catch (err) {
      problem = `threw ${String(err)}`;
    }
    if (problem !== null) {
      failures.push(
        `${label} #${i}: ${problem}\n      input: ${describeInput(input)}`
      );
      if (failures.length >= MAX_REPORTED) break;
    }
    i++;
  }
  return failures;
}

function* generated<T>(
  seed: number,
  iterations: number,
  generate: (rng: Rng) => T
): Generator<T> {
  const rng = new Rng(seed);
  for (let i = 0; i < iterations; i++) yield generate(rng);
}

/** A seeded sweep: `iterations` generated inputs, all must pass `check`. */
function sweep<T>(
  label: string,
  seed: number,
  iterations: number,
  generate: (rng: Rng) => T,
  check: (input: T) => string | null,
  describeInput: (input: T) => string
): void {
  const tag = `${label} [seed 0x${seed.toString(16)}]`;
  expect(
    failuresOf(tag, generated(seed, iterations, generate), check, describeInput)
  ).toEqual([]);
}

// ── Python repr() formatting ────────────────────────────────────────────────

/**
 * CPython's `repr(float)`: the shortest string that round-trips (the same
 * digits as JS's shortest form), laid out by CPython's own rule — positional
 * when the decimal-point position is in (-4, 16], otherwise `d.ddde±XX` with
 * an exponent of at least two digits. Integral values keep a `.0`.
 */
function pyFloatRepr(x: number): string {
  if (Number.isNaN(x)) return "nan";
  if (x === Number.POSITIVE_INFINITY) return "inf";
  if (x === Number.NEGATIVE_INFINITY) return "-inf";
  const sign = x < 0 || Object.is(x, -0) ? "-" : "";
  const ax = Math.abs(x);
  if (ax === 0) return `${sign}0.0`;
  const [mantissa = "", exponent = "0"] = ax.toExponential().split("e");
  const digits = mantissa.replace(".", "");
  const decpt = Number(exponent) + 1;
  let body: string;
  if (decpt > -4 && decpt <= 16) {
    if (decpt <= 0) body = `0.${"0".repeat(-decpt)}${digits}`;
    else if (decpt >= digits.length)
      body = `${digits}${"0".repeat(decpt - digits.length)}.0`;
    else body = `${digits.slice(0, decpt)}.${digits.slice(decpt)}`;
  } else {
    const lead = digits.length > 1 ? `${digits[0]}.${digits.slice(1)}` : digits;
    const e = decpt - 1;
    body = `${lead}e${e < 0 ? "-" : "+"}${String(Math.abs(e)).padStart(2, "0")}`;
  }
  return sign + body;
}

/** `repr()` of a flat tuple of 2+ already-repr'd items. */
function pyTuple(items: readonly string[]): string {
  return `(${items.join(", ")})`;
}

/** `repr()` of a str with no quotes, backslashes or unprintables in it —
 *  which covers every string `record_architecture` can build. */
function pyStr(s: string): string {
  return `'${s}'`;
}

// ── Value generators ────────────────────────────────────────────────────────

const bits = new DataView(new ArrayBuffer(8));

/** A double drawn from the whole bit space: every exponent, subnormals,
 *  both signs. */
function anyFiniteDouble(rng: Rng): number {
  for (;;) {
    bits.setUint32(0, rng.u32());
    bits.setUint32(4, rng.u32());
    const x = bits.getFloat64(0);
    if (Number.isFinite(x)) return x;
  }
}

/** Uniform in [0, 1) at full 53-bit precision — record_epoch's loss/accuracy. */
function unitDouble(rng: Rng): number {
  return ((rng.u32() >>> 5) * 67108864 + (rng.u32() >>> 6)) / 9007199254740992;
}

const EDGE_DOUBLES: readonly number[] = [
  0,
  -0,
  1,
  -1,
  250,
  1e15,
  1e16,
  2 ** 53,
  1e22,
  1e-4,
  1e-5,
  5e-324,
  2.2250738585072014e-308,
  Number.MAX_VALUE,
  -Number.MAX_VALUE,
  0.30000000000000004,
];

const NON_FINITE: readonly number[] = [
  Number.POSITIVE_INFINITY,
  Number.NEGATIVE_INFINITY,
  Number.NaN,
];

function genDouble(rng: Rng, pNonFinite: number): number {
  if (rng.chance(pNonFinite)) return rng.pick(NON_FINITE);
  const r = rng.next();
  if (r < 0.35) return unitDouble(rng);
  if (r < 0.6) {
    // train.fit's layer stats: float(f"{x:.3g}"), three significant figures.
    return Number((unitDouble(rng) * 10 ** rng.int(-9, 9)).toPrecision(3));
  }
  if (r < 0.9) return anyFiniteDouble(rng);
  return rng.pick(EDGE_DOUBLES);
}

/** A Python int as repr() prints it. Kept under 309 digits: past that the
 *  parsers overflow to an Infinity epoch, which is ledgered separately below. */
function genEpochDigits(rng: Rng): string {
  const r = rng.next();
  if (r < 0.6) return String(rng.int(0, 200));
  if (r < 0.85) return String(rng.int(0, 2 ** 32));
  return `${rng.int(1, 9)}${rng.digits(rng.int(0, 300))}`;
}

function genEpochRet(rng: Rng): string {
  return pyTuple([
    genEpochDigits(rng),
    pyFloatRepr(genDouble(rng, 0.1)),
    pyFloatRepr(genDouble(rng, 0.1)),
  ]);
}

function genStatsRet(rng: Rng, linearCount: number): string {
  const values = Array.from({ length: 2 * linearCount }, () =>
    pyFloatRepr(genDouble(rng, 0.04))
  );
  return pyTuple([genEpochDigits(rng), ...values]);
}

// Non-param layer names are `type(layer).__name__.lower()`: any identifier,
// including one that happens to be spelled "linear" and a non-ASCII one.
const ACTIVATIONS: readonly string[] =
  "relu tanh sigmoid gelu softmax dropout identity layernorm linear ñorm act2".split(
    " "
  );

function genDim(rng: Rng): number {
  const r = rng.next();
  if (r < 0.7) return rng.int(1, 64);
  if (r < 0.95) return rng.int(65, 100_000);
  return rng.pick([0, 2 ** 31, Number.MAX_SAFE_INTEGER]);
}

function tokenText(t: LayerToken): string {
  return t.kind === "linear" ? `linear:${t.inDim}:${t.outDim}` : t.name;
}

interface GeneratedNet {
  ret: string;
  expected: NetworkSpec;
}

/** A coherent net, and the repr `record_architecture` would return for it. */
function genNet(rng: Rng): GeneratedNet {
  const tokens: LayerToken[] = [];
  const activations = (): void => {
    for (let n = rng.pick([0, 0, 1, 1, 1, 2]); n > 0; n--) {
      tokens.push({ kind: "activation", name: rng.pick(ACTIVATIONS) });
    }
  };
  const linearCount = rng.int(1, 6);
  const firstIn = genDim(rng);
  let dim = firstIn;
  const outs: number[] = [];
  if (rng.chance(0.2)) activations();
  for (let k = 0; k < linearCount; k++) {
    const out = genDim(rng);
    tokens.push({ kind: "linear", inDim: dim, outDim: out });
    outs.push(out);
    dim = out;
    if (k < linearCount - 1 || rng.chance(0.2)) activations();
  }
  return {
    ret: pyStr(tokens.map(tokenText).join(" ")),
    expected: { tokens, columns: [firstIn, ...outs] },
  };
}

/** A beacon that parses but is incoherent: no param layer, or a broken chain. */
function genIncoherentNetRet(rng: Rng): string {
  if (rng.chance(0.5)) {
    return pyStr(
      Array.from({ length: rng.int(1, 3) }, () =>
        rng.pick(["relu", "tanh", "gelu"])
      ).join(" ")
    );
  }
  const a = rng.int(1, 9);
  const b = rng.int(1, 9);
  return pyStr(
    `linear:${a}:${b} relu linear:${b + rng.int(1, 5)}:${rng.int(1, 9)}`
  );
}

// ── Mutation operators ──────────────────────────────────────────────────────

type Mutator = (rng: Rng, s: string) => string;

/** Single characters the mutators splice in: the grammars' own alphabet,
 *  near-misses, whitespace that is not U+0020, and non-ASCII digits. */
const ALPHABET: readonly string[] = [
  ..."0159.eE+-,()'\"infax:_ \t\n",
  "\u00a0",
  "\u2028",
  "\ufeff",
  "٣",
  "０",
  "é",
  "𝟑",
];

const UNICODE_DIGITS: readonly string[] = ["٣", "０", "߃", "३", "𝟑"];
const PADS: readonly string[] = [" ", "\n", "\t", "\u00a0", "\ufeff", "\r\n"];
const SEPARATORS: readonly string[] = [
  ",",
  ",  ",
  " , ",
  " ,",
  ";",
  ", \t",
  ",\u00a0",
  ",\n",
  "، ",
];
/** Tokens Python's float repr never prints, plus the three it does print
 *  that the grammar special-cases, plus the empty token. */
const FOREIGN_TOKENS: readonly string[] = [
  "",
  ...[
    "inf -inf nan -nan +inf +1.5 Infinity -Infinity NaN INF Inf",
    "1e999 -1e999 1e-999 0x10 0b1 1_000 1. -.5 .5e-3 5E+02 1e e5 1e+",
    "--1 . - 00 ٣ ０.５ 1,5 True None np.float64(0.5)",
  ]
    .join(" ")
    .split(" "),
];

function indicesOf(s: string, needle: string): number[] {
  const at: number[] = [];
  for (let i = s.indexOf(needle); i !== -1; i = s.indexOf(needle, i + 1)) {
    at.push(i);
  }
  return at;
}

function spliceAt(s: string, i: number, remove: number, insert: string) {
  return s.slice(0, i) + insert + s.slice(i + remove);
}

const CHAR_MUTATORS: readonly Mutator[] = [
  (rng, s) => (s ? spliceAt(s, rng.int(0, s.length - 1), 1, "") : s),
  (rng, s) => {
    if (!s) return s;
    const i = rng.int(0, s.length - 1);
    return spliceAt(s, i, 0, s[i] ?? "");
  },
  (rng, s) => {
    if (s.length < 2) return s;
    const i = rng.int(0, s.length - 2);
    return spliceAt(s, i, 2, `${s[i + 1]}${s[i]}`);
  },
  (rng, s) =>
    s ? spliceAt(s, rng.int(0, s.length - 1), 1, rng.pick(ALPHABET)) : s,
  (rng, s) => spliceAt(s, rng.int(0, s.length), 0, rng.pick(ALPHABET)),
  (rng, s) => s.slice(0, rng.int(0, s.length)),
  (rng, s) => (rng.chance(0.5) ? rng.pick(PADS) + s : s + rng.pick(PADS)),
  (rng, s) =>
    spliceAt(
      s,
      rng.int(0, s.length),
      0,
      rng.digits(rng.pick([17, 25, 64, 400, 3000]))
    ),
  (rng, s) => {
    const at = [...s].flatMap((ch, i) => (/[0-9]/.test(ch) ? [i] : []));
    if (at.length === 0) return s;
    const chars = [...s];
    chars[rng.pick(at)] = rng.pick(UNICODE_DIGITS);
    return chars.join("");
  },
  (rng, s) => spliceAt(s, rng.int(0, s.length), 0, rng.pick(["-", "+"])),
];

/** Apply `f` to the `", "`-separated elements of a parenthesised payload. */
function withElements(s: string, f: (parts: string[]) => string[]): string {
  if (!s.startsWith("(") || !s.endsWith(")") || s.length < 2) return s;
  return `(${f(s.slice(1, -1).split(", ")).join(", ")})`;
}

const TUPLE_MUTATORS: readonly Mutator[] = [
  (rng, s) => {
    const at = indicesOf(s, ", ");
    return at.length ? spliceAt(s, rng.pick(at), 2, rng.pick(SEPARATORS)) : s;
  },
  (rng, s) =>
    withElements(s, (parts) => {
      const i = rng.int(0, parts.length - 1);
      return rng.chance(0.5)
        ? [...parts.slice(0, i + 1), ...parts.slice(i)]
        : [...parts.slice(0, i), ...parts.slice(i + 1)];
    }),
  (rng, s) =>
    withElements(s, (parts) => {
      const i = rng.int(0, parts.length - 1);
      const j = rng.int(0, parts.length - 1);
      const out = [...parts];
      out[i] = parts[j] ?? "";
      out[j] = parts[i] ?? "";
      return out;
    }),
  (rng, s) =>
    withElements(s, (parts) => {
      const out = [...parts];
      out[rng.int(0, parts.length - 1)] = rng.pick(FOREIGN_TOKENS);
      return out;
    }),
  (rng, s) =>
    rng.pick([
      `(${s})`,
      `[${s.slice(1, -1)}]`,
      s.slice(1),
      s.slice(0, -1),
      `${s},`,
      `(${s.slice(1, -1)},)`,
      `${s}${s}`,
    ]),
];

const ARCH_MUTATORS: readonly Mutator[] = [
  (rng, s) =>
    rng.pick([
      `"${s.slice(1, -1)}"`,
      `'${s.slice(1, -1)}"`,
      `"${s.slice(1, -1)}'`,
      s.slice(1),
      s.slice(0, -1),
      `'${s}'`,
      `"${s}"`,
      s.slice(1, -1),
      `b${s}`,
    ]),
  (rng, s) => {
    const runs = [...s.matchAll(/[0-9]+/g)];
    if (runs.length === 0) return s;
    const run = rng.pick(runs);
    return spliceAt(
      s,
      run.index,
      run[0].length,
      rng.pick([
        "",
        "-1",
        "1.5",
        "1e3",
        "٣",
        "0x10",
        "9".repeat(20),
        "007",
        "0",
        " 4",
        "4 ",
      ])
    );
  },
  (rng, s) => {
    const at = indicesOf(s, " ");
    return at.length
      ? spliceAt(
          s,
          rng.pick(at),
          1,
          rng.pick(["  ", "\t", "\u00a0", ",", "\n", "", " , "])
        )
      : s;
  },
  (rng, s) =>
    s.replace(
      "linear",
      rng.pick(["Linear", "LINEAR", "linea", "linear_", "lin ear", "linéar"])
    ),
  (rng, s) => {
    // Drop or duplicate a whole token: the chain may break or survive.
    const body = s.slice(1, -1).split(" ");
    const i = rng.int(0, body.length - 1);
    const out = rng.chance(0.5)
      ? [...body.slice(0, i + 1), ...body.slice(i)]
      : [...body.slice(0, i), ...body.slice(i + 1)];
    return `${s[0] ?? ""}${out.join(" ")}${s[s.length - 1] ?? ""}`;
  },
];

function mutate(rng: Rng, s: string, extra: readonly Mutator[]): string {
  const pool = [...CHAR_MUTATORS, ...extra, ...extra];
  let out = s;
  for (let n = rng.int(1, 3); n > 0; n--) out = rng.pick(pool)(rng, out);
  return out;
}

// ── Events ──────────────────────────────────────────────────────────────────

const EPOCH_NODE = "grackle_nn/metrics.py:record_epoch";
const STATS_NODE = "grackle_nn/metrics.py:record_layer_stats";
const ARCH_NODE = "grackle_nn/metrics.py:record_architecture";

function retEvent(
  node_id: string,
  ret: string,
  ret_truncated?: boolean
): TraceEvent {
  const values: NonNullable<TraceEvent["values"]> =
    ret_truncated === undefined ? { ret } : { ret, ret_truncated };
  return {
    event: "return",
    node_id,
    ts_ns: 0,
    thread_id: 1,
    frame_depth: 3,
    values,
  };
}

function otherEvent(event: string, node_id: string): TraceEvent {
  return { event, node_id, ts_ns: 0, thread_id: 1, frame_depth: 3 };
}

// ── The reference recognizers (character-level, no regex) ──────────────────

function isAsciiDigit(ch: string | undefined): boolean {
  return ch !== undefined && ch >= "0" && ch <= "9";
}

function isDigitRun(s: string): boolean {
  if (s.length === 0) return false;
  for (let i = 0; i < s.length; i++) if (!isAsciiDigit(s[i])) return false;
  return true;
}

/** The documented token grammar: `-`? then digits with an optional fraction
 *  (or a bare fraction), then an optional exponent; or `inf`, `-inf`, `nan`. */
function isFloatToken(s: string): boolean {
  if (s === "inf" || s === "-inf" || s === "nan") return true;
  let i = 0;
  if (s[i] === "-") i++;
  let mantissaDigits = 0;
  while (isAsciiDigit(s[i])) {
    i++;
    mantissaDigits++;
  }
  if (s[i] === ".") {
    i++;
    while (isAsciiDigit(s[i])) {
      i++;
      mantissaDigits++;
    }
  }
  if (mantissaDigits === 0) return false;
  if (s[i] === "e" || s[i] === "E") {
    i++;
    if (s[i] === "+" || s[i] === "-") i++;
    let exponentDigits = 0;
    while (isAsciiDigit(s[i])) {
      i++;
      exponentDigits++;
    }
    if (exponentDigits === 0) return false;
  }
  return i === s.length;
}

function refDecode(token: string): number {
  if (token === "inf") return Number.POSITIVE_INFINITY;
  if (token === "-inf") return Number.NEGATIVE_INFINITY;
  if (token === "nan") return Number.NaN;
  return Number.parseFloat(token);
}

/** What a scanner makes of one beacon return: a point, a counted drop, or
 *  nothing at all (shape mismatch). */
type Verdict =
  | { kind: "ignored" }
  | { kind: "dropped" }
  | { kind: "point"; epoch: number; values: readonly number[] };

const IGNORED: Verdict = { kind: "ignored" };
const DROPPED: Verdict = { kind: "dropped" };

/** Reference verdict for a flat repr'd tuple of an int and `arity - 1`
 *  float tokens, separated by exactly `", "`. */
function refTuple(ret: string, arity: number): Verdict {
  if (ret.length < 2 || !ret.startsWith("(") || !ret.endsWith(")")) {
    return IGNORED;
  }
  const parts = ret.slice(1, -1).split(", ");
  if (parts.length !== arity) return IGNORED;
  const [epoch = "", ...floats] = parts;
  if (!isDigitRun(epoch) || !floats.every(isFloatToken)) return IGNORED;
  const values = floats.map(refDecode);
  if (!values.every(Number.isFinite)) return DROPPED;
  return { kind: "point", epoch: Number(epoch), values };
}

function refLinear(token: string): LayerToken | null {
  const prefix = "linear:";
  if (!token.startsWith(prefix)) return null;
  const rest = token.slice(prefix.length);
  const colon = rest.indexOf(":");
  if (colon === -1) return null;
  const inText = rest.slice(0, colon);
  const outText = rest.slice(colon + 1);
  if (!isDigitRun(inText) || !isDigitRun(outText)) return null;
  return { kind: "linear", inDim: Number(inText), outDim: Number(outText) };
}

type ArchVerdict =
  | { kind: "skip" }
  | { kind: "fatal" }
  | { kind: "spec"; spec: NetworkSpec };

/** Reference verdict for one `record_architecture` ret: unparseable payloads
 *  are skipped, parsed-but-incoherent ones end the search (fatal). */
function refArch(ret: string): ArchVerdict {
  const quote = ret[0];
  if (
    ret.length < 2 ||
    (quote !== "'" && quote !== '"') ||
    ret[ret.length - 1] !== quote
  ) {
    return { kind: "skip" };
  }
  const raw = ret
    .slice(1, -1)
    .split(" ")
    .filter((t) => t !== "");
  if (raw.length === 0) return { kind: "skip" };
  const tokens: LayerToken[] = raw.map(
    (t) => refLinear(t) ?? { kind: "activation", name: t }
  );
  const linears = tokens.flatMap((t) => (t.kind === "linear" ? [t] : []));
  const first = linears[0];
  if (!first) return { kind: "fatal" };
  for (let k = 1; k < linears.length; k++) {
    if (linears[k]?.inDim !== linears[k - 1]?.outDim) return { kind: "fatal" };
  }
  return {
    kind: "spec",
    spec: { tokens, columns: [first.inDim, ...linears.map((l) => l.outDim)] },
  };
}

function isBeaconReturn(ev: TraceEvent, suffix: string): string | null {
  if (ev.event !== "return") return null;
  if (ev.node_id !== suffix && !ev.node_id.endsWith(`/${suffix}`)) return null;
  const ret = ev.values?.ret;
  if (typeof ret !== "string" || ev.values?.ret_truncated === true) return null;
  return ret;
}

/** Reference one-shot `extractNetworkSpec`, plus where it decided. */
function refExtract(events: readonly TraceEvent[]): {
  spec: NetworkSpec | null;
  fatalAt: number | null;
} {
  for (let i = 0; i < events.length; i++) {
    const ev = events[i];
    if (!ev) continue;
    const ret = isBeaconReturn(ev, "metrics.py:record_architecture");
    if (ret === null) continue;
    const verdict = refArch(ret);
    if (verdict.kind === "fatal") return { spec: null, fatalAt: i };
    if (verdict.kind === "spec") return { spec: verdict.spec, fatalAt: null };
  }
  return { spec: null, fatalAt: null };
}

// ── The parsers under test, reduced to comparable verdicts ─────────────────

function epochVerdict(ret: string, nodeId = EPOCH_NODE): Verdict {
  const { candidates, dropped } = scanEpochCandidates([retEvent(nodeId, ret)]);
  const point = candidates[0];
  if (candidates.length === 1 && dropped === 0 && point) {
    return {
      kind: "point",
      epoch: point.epoch,
      values: [point.loss, point.accuracy],
    };
  }
  if (candidates.length === 0) return dropped === 1 ? DROPPED : IGNORED;
  throw new Error(`one event gave ${candidates.length} points`);
}

function statsVerdict(
  ret: string,
  linearCount: number,
  nodeId = STATS_NODE
): Verdict {
  const { items, carry } = scanLayerStatsCandidates(
    [retEvent(nodeId, ret)],
    linearCount
  );
  const point = items[0];
  if (items.length === 1 && carry === 0 && point) {
    return {
      kind: "point",
      epoch: point.epoch,
      values: point.perLinear.flatMap((p) => [p.wRms, p.dwRms]),
    };
  }
  if (items.length === 0) return carry === 1 ? DROPPED : IGNORED;
  throw new Error(`one event gave ${items.length} points`);
}

function fmtVerdict(v: Verdict): string {
  return v.kind === "point"
    ? `point(epoch ${v.epoch}; ${v.values.map(pyFloatRepr).join(", ")})`
    : v.kind;
}

/** Exact for every epoch up to 2^53; beyond that "within float precision",
 *  since the spec lets `parseInt` round differently from `Number` past 20
 *  significant digits. */
function sameEpoch(a: number, b: number): boolean {
  return Object.is(a, b) || Math.abs(a - b) <= Math.abs(b) * 2 ** -50;
}

function verdictMismatch(actual: Verdict, expected: Verdict): string | null {
  const message = `parser says ${fmtVerdict(actual)}, expected ${fmtVerdict(expected)}`;
  if (actual.kind !== expected.kind) return message;
  if (actual.kind !== "point" || expected.kind !== "point") return null;
  if (!sameEpoch(actual.epoch, expected.epoch)) return message;
  if (actual.values.length !== expected.values.length) return message;
  // Compare repr strings: exact, and it tells -0.0 from 0.0.
  const same = actual.values.every(
    (v, k) => pyFloatRepr(v) === pyFloatRepr(expected.values[k] ?? Number.NaN)
  );
  return same ? null : message;
}

/** What every accepted point must satisfy, whatever the oracle says. */
function pointInvariant(v: Verdict, arity: number): string | null {
  if (v.kind !== "point") return null;
  if (v.values.length !== arity - 1) {
    return `point carries ${v.values.length} values, the arity says ${arity - 1}`;
  }
  if (!v.values.every(Number.isFinite)) {
    return `accepted a non-finite value: ${fmtVerdict(v)}`;
  }
  // An Infinity epoch is the ledgered overflow (see the it.fails at the end);
  // anything else that is not a non-negative integer is new.
  const integral = Number.isInteger(v.epoch) && v.epoch >= 0;
  if (!integral && v.epoch !== Number.POSITIVE_INFINITY) {
    return `epoch is not a non-negative integer: ${fmtVerdict(v)}`;
  }
  return null;
}

function fmtSpec(spec: NetworkSpec | null): string {
  if (spec === null) return "null";
  const tokens = spec.tokens.map((t) =>
    t.kind === "linear" ? `linear:${t.inDim}:${t.outDim}` : `[${show(t.name)}]`
  );
  return `${tokens.join(" ")} | columns ${spec.columns.join("-")}`;
}

function specInvariant(spec: NetworkSpec | null): string | null {
  if (spec === null) return null;
  const linears = spec.tokens.flatMap((t) => (t.kind === "linear" ? [t] : []));
  const first = linears[0];
  if (!first) return `a spec with no linear layer: ${fmtSpec(spec)}`;
  const columns = [first.inDim, ...linears.map((l) => l.outDim)];
  if (columns.join("-") !== spec.columns.join("-")) {
    return `columns disagree with the linear tokens: ${fmtSpec(spec)}`;
  }
  for (let k = 1; k < linears.length; k++) {
    if (linears[k]?.inDim !== linears[k - 1]?.outDim) {
      return `linear chain does not connect: ${fmtSpec(spec)}`;
    }
  }
  for (const c of spec.columns) {
    const integral = Number.isInteger(c) && c >= 0;
    // Infinity: the ledgered overflow, as for epochs.
    if (!integral && c !== Number.POSITIVE_INFINITY) {
      return `column size ${c} is not a non-negative integer`;
    }
  }
  for (const t of spec.tokens) {
    if (t.kind === "activation" && (t.name === "" || t.name.includes(" "))) {
      return `activation glyph named ${show(t.name)}`;
    }
  }
  return null;
}

const FLOAT_TOKEN_RE = new RegExp(`^(?:${FLOAT})$`);

// ── The oracle's own footing ────────────────────────────────────────────────

describe("T8-6 oracle self-checks", () => {
  it("pyFloatRepr reproduces CPython's repr() (a slice of the offline check)", () => {
    // Left: the double. Right: CPython 3.12's repr() of it.
    const table: [number, string][] = [
      [0.7107649687606569, "0.7107649687606569"],
      [0.6041666666666666, "0.6041666666666666"],
      [1e-5, "1e-05"],
      [1e-4, "0.0001"],
      [1e16, "1e+16"],
      [1e15, "1000000000000000.0"],
      [2 ** 53, "9007199254740992.0"],
      [1e22, "1e+22"],
      [5e-324, "5e-324"],
      [Number.MAX_VALUE, "1.7976931348623157e+308"],
      [-0, "-0.0"],
      [250, "250.0"],
      [1.5e-100, "1.5e-100"],
      [0.1 + 0.2, "0.30000000000000004"],
      [3.333333333333333e-8, "3.333333333333333e-08"],
      [0.000123, "0.000123"],
      [Number.NEGATIVE_INFINITY, "-inf"],
      [Number.NaN, "nan"],
    ];
    expect(table.map(([x]) => pyFloatRepr(x))).toEqual(
      table.map(([, repr]) => repr)
    );
  });

  it("the reference recognizer accepts and rejects what the grammar documents", () => {
    // Pins the oracle to the pre-existing hand-picked corpus, so a sweep that
    // disagrees with a parser is not just a disagreement with a broken oracle.
    const accepted = ["0.5", "-0.5", "1e-05", "1.5E-3", ".25", "1.", "-.5"];
    const rejected = ["+1.5", ".", "-", "1e", "e5", "-nan", "Infinity", "٣"];
    expect(accepted.filter((t) => !isFloatToken(t))).toEqual([]);
    expect(rejected.filter(isFloatToken)).toEqual([]);
    expect(refTuple("(-1, 0.5, 0.5)", 3)).toEqual(IGNORED);
    expect(refTuple("(0, inf, 0.5)", 3)).toEqual(DROPPED);
    expect(refArch("'linear:2:32 linear:64:16'")).toEqual({ kind: "fatal" });
    expect(refArch("'linear:2:8\trelu'")).toEqual({ kind: "fatal" });
    expect(refArch("'linear:2:8\"")).toEqual({ kind: "skip" });
  });
});

// ── Round-trip: Python repr() in, the same values out ───────────────────────

describe("T8-6 round-trip sweeps", () => {
  it("FLOAT accepts, and parseFloatToken decodes, every repr(float)", () => {
    sweep(
      "FLOAT round-trip",
      0x7e860001,
      4000,
      (rng) => pyFloatRepr(genDouble(rng, 0.1)),
      (token) => {
        if (!FLOAT_TOKEN_RE.test(token)) return "FLOAT rejects a Python repr";
        const back = pyFloatRepr(parseFloatToken(token));
        return back === token ? null : `decodes to ${back}`;
      },
      show
    );
  });

  it("record_epoch: every (int, float, float) repr parses back exactly", () => {
    sweep(
      "record_epoch round-trip",
      0x7e860002,
      3000,
      (rng) => ({
        epoch: genEpochDigits(rng),
        loss: genDouble(rng, 0.1),
        accuracy: genDouble(rng, 0.1),
        node: rng.pick([EPOCH_NODE, "metrics.py:record_epoch"]),
      }),
      ({ epoch, loss, accuracy, node }) => {
        const ret = pyTuple([epoch, pyFloatRepr(loss), pyFloatRepr(accuracy)]);
        const expected: Verdict =
          Number.isFinite(loss) && Number.isFinite(accuracy)
            ? { kind: "point", epoch: Number(epoch), values: [loss, accuracy] }
            : DROPPED;
        const actual = epochVerdict(ret, node);
        return verdictMismatch(actual, expected) ?? pointInvariant(actual, 3);
      },
      ({ epoch, loss, accuracy }) =>
        show(pyTuple([epoch, pyFloatRepr(loss), pyFloatRepr(accuracy)]))
    );
  });

  it("record_layer_stats: every 1 + 2L repr parses back at L, and only at L", () => {
    sweep(
      "record_layer_stats round-trip",
      0x7e860003,
      2000,
      (rng) => {
        const linearCount = rng.int(1, 8);
        return {
          linearCount,
          epoch: genEpochDigits(rng),
          values: Array.from({ length: 2 * linearCount }, () =>
            genDouble(rng, 0.04)
          ),
        };
      },
      ({ linearCount, epoch, values }) => {
        const ret = pyTuple([epoch, ...values.map(pyFloatRepr)]);
        const expected: Verdict = values.every(Number.isFinite)
          ? { kind: "point", epoch: Number(epoch), values }
          : DROPPED;
        const actual = statsVerdict(ret, linearCount);
        const problem =
          verdictMismatch(actual, expected) ??
          pointInvariant(actual, 1 + 2 * linearCount);
        if (problem) return problem;
        // A different net's arity is a shape mismatch — ignored, not dropped.
        for (const other of [linearCount - 1, linearCount + 1]) {
          if (other < 1) continue;
          const wrong = statsVerdict(ret, other);
          if (wrong.kind !== "ignored") {
            return `parsed at linearCount ${other}: ${fmtVerdict(wrong)}`;
          }
        }
        return null;
      },
      ({ linearCount, epoch, values }) =>
        `L=${linearCount} ${show(pyTuple([epoch, ...values.map(pyFloatRepr)]))}`
    );
  });

  it("record_architecture: every generated net parses back token for token", () => {
    sweep(
      "record_architecture round-trip",
      0x7e860004,
      1500,
      (rng) => ({ net: genNet(rng), breakAt: rng.next() }),
      ({ net, breakAt }) => {
        const parsed = extractNetworkSpec([retEvent(ARCH_NODE, net.ret)]);
        if (fmtSpec(parsed) !== fmtSpec(net.expected)) {
          return `parsed ${fmtSpec(parsed)}, expected ${fmtSpec(net.expected)}`;
        }
        // The same net with one link of its chain broken must not parse.
        const linears = net.expected.tokens.flatMap((t) =>
          t.kind === "linear" ? [t] : []
        );
        if (linears.length < 2) return null;
        const k = 1 + Math.floor(breakAt * (linears.length - 1));
        const broken = net.expected.tokens.map((t) =>
          t === linears[k] && t.kind === "linear"
            ? `linear:${t.inDim + 1}:${t.outDim}`
            : tokenText(t)
        );
        const brokenSpec = extractNetworkSpec([
          retEvent(ARCH_NODE, pyStr(broken.join(" "))),
        ]);
        return brokenSpec === null
          ? null
          : `a broken chain parsed: ${fmtSpec(brokenSpec)}`;
      },
      ({ net }) => show(net.ret)
    );
  });
});

// ── Robustness: mutated payloads vs the reference recognizers ──────────────

function epochCheck(ret: string): string | null {
  const actual = epochVerdict(ret);
  return verdictMismatch(actual, refTuple(ret, 3)) ?? pointInvariant(actual, 3);
}

function statsCheck(ret: string, linearCount: number): string | null {
  for (const l of [linearCount - 1, linearCount, linearCount + 1]) {
    if (l < 1) continue;
    const arity = 1 + 2 * l;
    const actual = statsVerdict(ret, l);
    const problem =
      verdictMismatch(actual, refTuple(ret, arity)) ??
      pointInvariant(actual, arity);
    if (problem) return `at linearCount ${l}: ${problem}`;
  }
  return null;
}

function archCheck(events: readonly TraceEvent[]): string | null {
  const parsed = extractNetworkSpec(events);
  const reference = refExtract(events).spec;
  if (fmtSpec(parsed) !== fmtSpec(reference)) {
    return `parsed ${fmtSpec(parsed)}, reference ${fmtSpec(reference)}`;
  }
  return specInvariant(parsed);
}

function genArchRet(rng: Rng): string {
  const r = rng.next();
  if (r < 0.2) return genNet(rng).ret;
  if (r < 0.35) return genIncoherentNetRet(rng);
  return mutate(rng, genNet(rng).ret, ARCH_MUTATORS);
}

function showEvents(events: readonly TraceEvent[]): string {
  return events
    .map((ev) => {
      const ret = ev.values?.ret;
      const trunc = ev.values?.ret_truncated ? " (truncated)" : "";
      return `${ev.event} ${ev.node_id}${typeof ret === "string" ? ` ${show(ret)}` : ""}${trunc}`;
    })
    .join("\n        ");
}

const GOLDEN_EPOCH = "(0, 0.7107649687606569, 0.6041666666666666)";
const GOLDEN_STATS = "(9, 1.23, 0.000456, 0.789, 1.5e-05, 1.11, 0.222)";
const GOLDEN_ARCH = "'linear:2:32 relu linear:32:32 relu linear:32:3'";

/** Every prefix, every single deletion, every adjacent swap, and every
 *  single-character substitution from the mutation alphabet. */
function neighbours(s: string): string[] {
  const out: string[] = [];
  for (let i = 0; i <= s.length; i++) out.push(s.slice(0, i));
  for (let i = 0; i < s.length; i++) {
    out.push(spliceAt(s, i, 1, ""));
    if (i + 1 < s.length) out.push(spliceAt(s, i, 2, `${s[i + 1]}${s[i]}`));
    for (const ch of ALPHABET) out.push(spliceAt(s, i, 1, ch));
  }
  return out;
}

describe("T8-6 robustness sweeps (mutated payloads, differential)", () => {
  it("record_epoch: mutations never throw and agree with the reference", () => {
    sweep(
      "record_epoch mutations",
      0x7e860005,
      5000,
      (rng) => mutate(rng, genEpochRet(rng), TUPLE_MUTATORS),
      epochCheck,
      show
    );
  });

  it("record_layer_stats: mutations agree with the reference at L-1, L, L+1", () => {
    sweep(
      "record_layer_stats mutations",
      0x7e860006,
      4000,
      (rng) => {
        const linearCount = rng.int(1, 5);
        return {
          linearCount,
          ret: mutate(rng, genStatsRet(rng, linearCount), TUPLE_MUTATORS),
        };
      },
      ({ ret, linearCount }) => statsCheck(ret, linearCount),
      ({ ret, linearCount }) => `L=${linearCount} ${show(ret)}`
    );
  });

  it("record_architecture: mutated beacon streams agree with the reference", () => {
    sweep(
      "record_architecture mutations",
      0x7e860007,
      4000,
      (rng) =>
        Array.from({ length: rng.int(1, 3) }, () =>
          retEvent(
            ARCH_NODE,
            genArchRet(rng),
            rng.chance(0.1) ? true : undefined
          )
        ),
      archCheck,
      showEvents
    );
  });

  it("every one-edit neighbour of the golden payloads agrees with the reference", () => {
    const failures = [
      ...failuresOf(
        "golden record_epoch",
        neighbours(GOLDEN_EPOCH),
        epochCheck,
        show
      ),
      ...failuresOf(
        "golden record_layer_stats",
        neighbours(GOLDEN_STATS),
        (ret) => statsCheck(ret, 3),
        show
      ),
      ...failuresOf(
        "golden record_architecture",
        neighbours(GOLDEN_ARCH),
        (ret) => archCheck([retEvent(ARCH_NODE, ret)]),
        show
      ),
    ];
    expect(failures).toEqual([]);
  });
});

// ── Consistency: one payload, every path, one answer ────────────────────────

function genStreamEvent(rng: Rng, linearCount: number): TraceEvent {
  const truncated = rng.chance(0.08) ? true : undefined;
  const r = rng.next();
  if (r < 0.2) {
    return otherEvent(
      rng.pick(["call", "return", "line"]),
      rng.pick([
        "grackle_nn/train.py:fit",
        "grackle_nn/layers.py:Linear.forward",
        EPOCH_NODE,
        STATS_NODE,
        ARCH_NODE,
      ])
    );
  }
  if (r < 0.45) {
    const ret = rng.chance(0.4)
      ? mutate(rng, genEpochRet(rng), TUPLE_MUTATORS)
      : genEpochRet(rng);
    return retEvent(
      rng.pick([
        EPOCH_NODE,
        "metrics.py:record_epoch",
        "pkg/mymetrics.py:record_epoch",
      ]),
      ret,
      truncated
    );
  }
  if (r < 0.7) {
    const l = rng.chance(0.8) ? linearCount : rng.int(1, 4);
    const ret = rng.chance(0.4)
      ? mutate(rng, genStatsRet(rng, l), TUPLE_MUTATORS)
      : genStatsRet(rng, l);
    return retEvent(
      rng.pick([STATS_NODE, "metrics.py:record_layer_stats"]),
      ret,
      truncated
    );
  }
  return retEvent(
    rng.pick([
      ARCH_NODE,
      "metrics.py:record_architecture",
      "pkg/mymetrics.py:record_architecture",
    ]),
    genArchRet(rng),
    truncated
  );
}

function canonEpochPoints(points: readonly EpochPoint[]): string {
  return points
    .map(
      (p) =>
        `${p.eventIndex}:${p.epoch}:${pyFloatRepr(p.loss)}:${pyFloatRepr(p.accuracy)}`
    )
    .join(" ");
}

function canonStatsPoints(points: readonly LayerStatsPoint[]): string {
  return points
    .map(
      (p) =>
        `${p.eventIndex}:${p.epoch}:${p.perLinear.map((s) => `${pyFloatRepr(s.wRms)}/${pyFloatRepr(s.dwRms)}`).join(",")}`
    )
    .join(" ");
}

/** Reference: the per-event verdicts of a whole stream, in order. */
function refStream(
  events: readonly TraceEvent[],
  suffix: string,
  arity: number
): { points: string; dropped: number } {
  const points: string[] = [];
  let dropped = 0;
  events.forEach((ev, i) => {
    const ret = isBeaconReturn(ev, suffix);
    if (ret === null) return;
    const v = refTuple(ret, arity);
    if (v.kind === "dropped") dropped++;
    if (v.kind === "point") points.push(`${i}:${fmtVerdict(v)}`);
  });
  return { points: points.join(" "), dropped };
}

function epochStreamProblem(events: readonly TraceEvent[]): string | null {
  const full = scanEpochCandidates(events, 0);
  const reference = refStream(events, "metrics.py:record_epoch", 3);
  const got = full.candidates
    .map(
      (p) =>
        `${p.eventIndex}:${fmtVerdict({ kind: "point", epoch: p.epoch, values: [p.loss, p.accuracy] })}`
    )
    .join(" ");
  if (got !== reference.points || full.dropped !== reference.dropped) {
    return `one scan gives [${got}] dropped ${full.dropped}; reference [${reference.points}] dropped ${reference.dropped}`;
  }
  for (let split = 0; split <= events.length; split++) {
    const head = scanEpochCandidates(events.slice(0, split), 0);
    const tail = scanEpochCandidates(events, split);
    const resumed = canonEpochPoints([...head.candidates, ...tail.candidates]);
    if (
      resumed !== canonEpochPoints(full.candidates) ||
      head.dropped + tail.dropped !== full.dropped
    ) {
      return `resuming at ${split} disagrees with one scan`;
    }
  }
  return null;
}

function statsStreamProblem(
  events: readonly TraceEvent[],
  linearCount: number
): string | null {
  const full = scanLayerStatsCandidates(events, linearCount, 0, undefined);
  const reference = refStream(
    events,
    "metrics.py:record_layer_stats",
    1 + 2 * linearCount
  );
  const got = full.items
    .map(
      (p) =>
        `${p.eventIndex}:${fmtVerdict({ kind: "point", epoch: p.epoch, values: p.perLinear.flatMap((s) => [s.wRms, s.dwRms]) })}`
    )
    .join(" ");
  if (got !== reference.points || full.carry !== reference.dropped) {
    return `one scan gives [${got}] dropped ${full.carry}; reference [${reference.points}] dropped ${reference.dropped}`;
  }
  for (let split = 0; split <= events.length; split++) {
    const head = scanLayerStatsCandidates(
      events.slice(0, split),
      linearCount,
      0,
      undefined
    );
    const tail = scanLayerStatsCandidates(
      events,
      linearCount,
      split,
      head.carry
    );
    if (
      canonStatsPoints([...head.items, ...tail.items]) !==
        canonStatsPoints(full.items) ||
      tail.carry !== full.carry
    ) {
      return `resuming at ${split} disagrees with one scan`;
    }
  }
  return null;
}

function archStreamProblem(events: readonly TraceEvent[]): string | null {
  const full = extractNetworkSpec(events, 0);
  const reference = refExtract(events);
  if (fmtSpec(full) !== fmtSpec(reference.spec)) {
    return `one scan gives ${fmtSpec(full)}, reference ${fmtSpec(reference.spec)}`;
  }
  for (let split = 0; split <= events.length; split++) {
    // NetworkViewPanel's latch: keep a spec the prefix found, otherwise resume
    // the search at the append point.
    const resumed =
      extractNetworkSpec(events.slice(0, split), 0) ??
      extractNetworkSpec(events, split);
    if (fmtSpec(resumed) === fmtSpec(full)) continue;
    // Ledgered: a fatal beacon inside the prefix is forgotten by the resume
    // (panels/NetworkViewPanel.specResume.test.tsx). Anything else is new.
    if (reference.fatalAt !== null && reference.fatalAt < split) continue;
    return `resuming at ${split} gives ${fmtSpec(resumed)}, one scan gives ${fmtSpec(full)}`;
  }
  return null;
}

describe("T8-6 consistency sweeps (one payload, every path)", () => {
  it("record_epoch and record_layer_stats at L=1 agree on every 3-tuple", () => {
    // Both parse `(int, FLOAT, FLOAT)` from the same shared fragment, and a
    // bare node id must parse exactly like a path-prefixed one.
    sweep(
      "epoch vs stats(L=1)",
      0x7e860008,
      4000,
      (rng) => ({
        ret: rng.chance(0.6)
          ? mutate(rng, genEpochRet(rng), TUPLE_MUTATORS)
          : genEpochRet(rng),
        epochNode: rng.pick([EPOCH_NODE, "metrics.py:record_epoch"]),
        statsNode: rng.pick([STATS_NODE, "metrics.py:record_layer_stats"]),
      }),
      ({ ret, epochNode, statsNode }) =>
        verdictMismatch(
          epochVerdict(ret, epochNode),
          statsVerdict(ret, 1, statsNode)
        ),
      ({ ret, epochNode, statsNode }) =>
        `${show(ret)} via ${epochNode} / ${statsNode}`
    );
  });

  it("the bare FLOAT fragment and both tuple parsers accept the same tokens", () => {
    sweep(
      "FLOAT vs tuple slots",
      0x7e860009,
      4000,
      (rng) => {
        const r = rng.next();
        if (r < 0.3) return pyFloatRepr(genDouble(rng, 0.1));
        if (r < 0.5) return rng.pick(FOREIGN_TOKENS);
        return mutate(rng, pyFloatRepr(genDouble(rng, 0.1)), []);
      },
      (token) => {
        const fragment = FLOAT_TOKEN_RE.test(token);
        const reference = isFloatToken(token);
        const inEpoch = epochVerdict(`(0, ${token}, 0.5)`).kind !== "ignored";
        const inStats =
          statsVerdict(`(0, 0.5, ${token})`, 1).kind !== "ignored";
        return fragment === reference &&
          inEpoch === reference &&
          inStats === reference
          ? null
          : `reference ${reference}, FLOAT ${fragment}, record_epoch slot ${inEpoch}, record_layer_stats slot ${inStats}`;
      },
      show
    );
  });

  it("scanning a mixed stream in one pass, or resumed at any split, gives one answer", () => {
    sweep(
      "resume at every split",
      0x7e86000a,
      400,
      (rng) => {
        const linearCount = rng.int(1, 4);
        return {
          linearCount,
          events: Array.from({ length: rng.int(3, 24) }, () =>
            genStreamEvent(rng, linearCount)
          ),
        };
      },
      ({ events, linearCount }) => {
        const epoch = epochStreamProblem(events);
        if (epoch) return `record_epoch: ${epoch}`;
        const stats = statsStreamProblem(events, linearCount);
        if (stats) return `record_layer_stats: ${stats}`;
        const arch = archStreamProblem(events);
        return arch ? `record_architecture: ${arch}` : null;
      },
      ({ events, linearCount }) =>
        `L=${linearCount}\n        ${showEvents(events)}`
    );
  });
});

// ── Ledgered: unbounded digit runs overflow to Infinity ─────────────────────

// KNOWN DEFECT (T8-6, docs/test-campaigns/phase-12.md): every grammar takes an
// unbounded `\d+` and decodes it with parseInt, so a digit run past the
// double range becomes Infinity — an "epoch" or a column size that is not an
// integer. Downstream, lossCurveLayout's x-scale divides by an Infinity span
// (NaN geometry), and two different oversized dims both become Infinity, so
// the network chain check passes for a chain that does not connect.
// Low severity: unreachable from grackle_nn under the default capture limit
// (max_value_len=120 truncates the repr first, and a truncated ret is
// skipped); reachable from a raised --max-value-len or a hand-written trace.
// Remove `.fails` in the PR that bounds the digit runs.
const OVERFLOWING = `2${"0".repeat(308)}`; // 309 digits, 2e308 > Number.MAX_VALUE
const FITTING = `1${"0".repeat(308)}`; // 309 digits, 1e308: still finite

describe("T8-6 ledger: unbounded digit runs", () => {
  it("a 309-digit value that still fits a double parses to that integer", () => {
    // The boundary companion: the defect starts exactly at double overflow.
    const epoch = epochVerdict(`(${FITTING}, 0.5, 0.5)`);
    const stats = statsVerdict(`(${FITTING}, 0.5, 0.5)`, 1);
    const spec = extractNetworkSpec([
      retEvent(ARCH_NODE, `'linear:2:${FITTING}'`),
    ]);
    expect(epoch).toEqual({ kind: "point", epoch: 1e308, values: [0.5, 0.5] });
    expect(stats).toEqual({ kind: "point", epoch: 1e308, values: [0.5, 0.5] });
    expect(spec?.columns).toEqual([2, 1e308]);
  });

  it.fails("record_epoch: an epoch past the double range is not reported as Infinity", () => {
    const { candidates } = scanEpochCandidates([
      retEvent(EPOCH_NODE, `(${OVERFLOWING}, 0.5, 0.5)`),
    ]);
    // Today: [Infinity]. Skipping the point or counting it dropped both pass.
    const bad = candidates
      .map((p) => p.epoch)
      .filter((e) => !Number.isInteger(e));
    expect(bad).toEqual([]);
  });

  it.fails("record_layer_stats: an epoch past the double range is not reported as Infinity", () => {
    const { items } = scanLayerStatsCandidates(
      [retEvent(STATS_NODE, `(${OVERFLOWING}, 0.5, 0.5)`)],
      1
    );
    const bad = items.map((p) => p.epoch).filter((e) => !Number.isInteger(e));
    expect(bad).toEqual([]);
  });

  it.fails("record_architecture: a dim past the double range is not a column of Infinity", () => {
    const spec = extractNetworkSpec([
      retEvent(ARCH_NODE, `'linear:2:${OVERFLOWING}'`),
    ]);
    // Today: columns [2, Infinity]. Rejecting the beacon (null) also passes.
    const bad = (spec?.columns ?? []).filter((c) => !Number.isInteger(c));
    expect(bad).toEqual([]);
  });
});
