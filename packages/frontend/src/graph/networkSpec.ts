import type { TraceEvent } from "@grackle/shared-types";

/**
 * Network architecture extraction from a raw trace-event array (Phase 12.4).
 *
 * `packages/nn`'s `record_architecture` beacon
 * (`grackle_nn/metrics.py:record_architecture`) fires exactly once per run,
 * before training starts, and returns a space-separated token string
 * describing the model's layer stack in forward order — e.g.
 * `"linear:2:32 relu linear:32:32 relu linear:32:3"`. Under
 * `--capture-values` that shows up as a `"return"` `TraceEvent` whose
 * `values.ret` is the Python `repr()` of that string, i.e. the token string
 * wrapped in an extra layer of quotes (`"'linear:2:32 ...'"`).
 *
 * This module is a pure function over an already-received event array — no
 * wire messages, mirroring `epochSeries.ts` / `callTree.ts`. Non-matching
 * events are silently skipped; a raw trace can contain any mix of events for
 * any node, and only the FIRST return on this one-shot beacon node matters.
 */

export type LayerToken =
  | { kind: "linear"; inDim: number; outDim: number }
  | { kind: "activation"; name: string };

export interface NetworkSpec {
  /** Layer tokens in forward-call order — this order disambiguates the
   *  multiple `Linear`/`ReLU` instances that share one trace node_id (see
   *  layerActivity.ts, which walks trace events in this same token order). */
  tokens: LayerToken[];
  /** Neuron-column sizes: `[firstLinear.inDim, ...linears.map(l => l.outDim)]`
   *  — one more entry than the number of linear tokens (e.g. the demo net's
   *  three Linear layers produce four columns: `[2, 32, 32, 3]`). */
  columns: number[];
}

// A param-carrying layer token: `linear:<in>:<out>`, both non-negative
// integers. Anchored full-match — a partial/embedded match is not this
// beacon's grammar (record_architecture.py's docstring).
const LINEAR_TOKEN_RE = /^linear:(\d+):(\d+)$/;

function parseToken(token: string): LayerToken {
  const match = LINEAR_TOKEN_RE.exec(token);
  if (match) {
    const inStr = match[1];
    const outStr = match[2];
    if (inStr !== undefined && outStr !== undefined) {
      return {
        kind: "linear",
        inDim: Number.parseInt(inStr, 10),
        outDim: Number.parseInt(outStr, 10),
      };
    }
  }
  // Any non-linear token (relu, tanh, a future layer type, ...) becomes an
  // activation glyph named after the raw token — open-string spirit
  // (ADR-0004): unknown values are rendered, not rejected.
  return { kind: "activation", name: token };
}

/** Strip exactly one layer of Python repr() quoting (`'...'` or `"..."`) off
 *  a string, or return null if `s` isn't quoted that way. `record_architecture`
 *  always returns a plain token string with no embedded quotes/backslashes, so
 *  Python's repr() always wraps it in a single unescaped pair. */
function stripReprQuotes(s: string): string | null {
  if (s.length < 2) return null;
  const first = s[0];
  const last = s[s.length - 1];
  if ((first === "'" || first === '"') && first === last) {
    return s.slice(1, -1);
  }
  return null;
}

/**
 * Extract the network architecture from a raw trace-event array (must be a
 * from-index-0 prefix, same contract as `extractEpochSeries`).
 *
 * Returns `null` when: no matching return event exists; the ret isn't a
 * quoted string; it parses to zero tokens; or the parsed linear layers don't
 * chain (`linear[k].inDim !== linear[k-1].outDim`) — a corrupt/foreign beacon
 * value, safer to render nothing than a nonsensical network.
 */
export function extractNetworkSpec(
  events: readonly TraceEvent[]
): NetworkSpec | null {
  for (const ev of events) {
    if (ev.event !== "return") continue;

    // Exact-or-slash-preceded match — same false-positive guard as
    // epochSeries.ts (a bare endsWith would also match "pkg/mymetrics.py:...").
    const nodeId = ev.node_id;
    const isRecordArchitecture =
      nodeId === "metrics.py:record_architecture" ||
      nodeId.endsWith("/metrics.py:record_architecture");
    if (!isRecordArchitecture) continue;

    const ret = ev.values?.ret;
    if (typeof ret !== "string") continue;
    if (ev.values?.ret_truncated === true) continue;

    const unquoted = stripReprQuotes(ret);
    if (unquoted === null) continue;

    const rawTokens = unquoted.split(" ").filter((t) => t.length > 0);
    if (rawTokens.length === 0) continue;

    const tokens = rawTokens.map(parseToken);

    const linears = tokens.filter(
      (t): t is Extract<LayerToken, { kind: "linear" }> => t.kind === "linear"
    );
    for (let i = 1; i < linears.length; i++) {
      const prev = linears[i - 1];
      const cur = linears[i];
      if (!prev || !cur || cur.inDim !== prev.outDim) return null;
    }

    const columns =
      linears.length > 0
        ? [linears[0]?.inDim as number, ...linears.map((l) => l.outDim)]
        : [];

    // First match wins — record_architecture is a one-shot beacon (fires
    // once, before the training loop); a later duplicate return would only
    // arise from a second traced run concatenated into the same file, which
    // is not a case this parser needs to reconcile.
    return { tokens, columns };
  }
  return null;
}
