# Phase 1.5 + 1.6 Test Campaign

**Date**: 2026-05-13
**Under test**: PR #10 (commit `6eff832`, Phase 1.5 — demo-derived backport) + PR #11 (commit `f9048f1`, Phase 1.6 — NodeInspector + GraphLegend panels)
**Environment**: macOS 26.5 / arm64, Python 3.12.13, Node 22, pnpm 11.1.1 (CI matrix already green on Ubuntu + Windows)
**Branches probed**: `main` (primary) + `demo/end-product-preview` (cross-check)

## Method

Eleven tiers of probes. Tier 1–2 re-baseline static and unit tests on both branches. Tier 3–11 actively probe surfaces the suites don't cover: JSON Schema runtime validation behavior (AJV Draft 2020-12 conformance), React component prop/edge cases and accessibility, WS StrictMode regression robustness, design-token resolution, demo-branch type-extension safety, bundle-impact verification, font-loading chain, documentation cross-references, and cross-platform encoding discipline.

## Summary

**61 unit tests pass (31 agent + 30 frontend) on both branches. All baseline static checks clean. Twelve findings — one HIGH (schema validation footgun, only matters for future runtime validators), four MEDIUM, six LOW, one INCORRECT (agent-suggested fix would regress dot-matrix rendering).**

| Tier | Result |
|---|---|
| T1 — Static (Biome / tsc / ruff / ruff format / mypy --strict / parity) | ✅ clean both branches |
| T2 — Unit tests (agent 31, frontend 30) | ✅ 61/61 pass both branches |
| T3 — JSON Schema runtime validation (AJV Draft 2020-12 probes, 13 cases) | ⚠️ F-1 (top-level `$ref` missing; schema accepts anything if validated at runtime), F-2 (`messages.schema.json` uses Draft-07 `definitions` keyword) |
| T4 — React component probes (NodeInspector + GraphLegend) | ⚠️ F-3 (no `role`/`aria-modal` on inspector overlay), F-4 (no Escape-key dismiss), F-5 (unnecessary `as unknown as number` zIndex cast), F-6 (`zIndex: 5` literal in GraphLegend vs CSS var in NodeInspector) |
| T5 — WS StrictMode identity guards (regression + race probes) | ✅ guards present on all 4 listeners; stale-socket test passes |
| T6 — Design tokens (`--color-node-*`, `--color-edge-*`, `--font-display`) | ⚠️ F-7 (hex colors break the OKLCH convention used elsewhere in `tokens.css`) |
| T7 — Demo-branch type extension (`DemoGraph extends Graph`) | ✅ typecheck clean; types compose without override hazards |
| T8 — Bundle / dependency impact (sigma, graphology, doto) | ✅ deps installed, no imports on `main` (tree-shaken to zero), Doto symlinked into `packages/frontend/node_modules/` |
| T9 — Font loading + wordmark | ⚠️ F-8 (agent-suggested smoothing values would regress dot-grid rendering — INCORRECT FINDING, documented for the record) |
| T10 — Documentation cross-references | ⚠️ F-9 (ADR-0005 referenced in schema but file does not exist), F-10 (demo-branch `ws/client.test.ts` does not test demo-only store actions) |
| T11 — Cross-platform & encoding | ✅ POSIX path pattern enforced (when validated); no backslash leaks |

## Findings

### F-1 — `graph.schema.json` has no top-level `$ref`; AJV accepts arbitrary JSON (HIGH)

**Location**: [packages/shared-types/schema/graph.schema.json:1-101](../../packages/shared-types/schema/graph.schema.json)

**Reproducer**:
```js
import Ajv2020 from "ajv/dist/2020.js";
import schema from "packages/shared-types/schema/graph.schema.json" with { type: "json" };
const v = new Ajv2020({ strict: true }).compile(schema);
v({ frobnicate: "yes" });                                  // → true (should reject)
v({ version: 0, language: "x", nodes: [], edges: [] });    // → true (should reject; version min=1)
v({ version: 1, language: "x", nodes: [{ id: "a", kind: "f", name: "n", path: "src\\app.py" }], edges: [] }); // → true (should reject; backslash)
```
**8 of 13** probe cases that should fail validation are silently accepted.

**Observed**: The schema body is `{ "$schema": …, "$id": "graph", "$defs": { … } }`. Without a top-level `type` / `properties` / `$ref`, JSON Schema treats the root as an empty schema, which validates *any* JSON document. `additionalProperties: false`, `pattern`, `minimum`, etc. inside `$defs/Graph` are unreachable.

**Expected**: Either (a) a top-level `$ref: "#/$defs/Graph"` so AJV validates against the `Graph` definition, or (b) explicit documentation in `packages/shared-types/schema/README.md` that schemas are codegen-only manifests and runtime validation must use hand-rolled schemas (as `protocol.py` already does for `_ENVELOPE_SCHEMA`).

**Current impact**: Zero — no code path uses `graph.schema.json` for runtime validation. `protocol.py` validates only the envelope with an inline schema. The Phase 2 parser will produce a `graph.json` payload that's never validated against the published schema today; if Phase 3 or Phase 6 introduces wire-level validation, this lands as a security/correctness bug.

**Fix** (option A, ~5 LoC, preserves codegen behavior):
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "graph",
  "title": "grackle graph",
  "$ref": "#/$defs/Graph",
  "$defs": { … }
}
```
After this, `pnpm check-parity` must be re-run; the generated TS interface name changes from `GrackleGraph` (currently `{ [k: string]: any }`) to `Graph` (the real definition).

Same pattern applies to `adapters.schema.json` and `messages.schema.json` — fix all three for consistency or document the catalog-only convention.

**Why it matters**: This is a latent footgun. The generated TS `GrackleGraph` interface in `packages/shared-types/src/generated/graph.ts` is currently `{ [k: string]: any }` — the type system already knows the schema doesn't validate. Future contributors who try to validate incoming `graph.json` payloads against the published schema will get false positives and ship invalid data downstream.

**Severity**: HIGH (latent, not active — but the fix is small and the cost of *not* fixing scales with project age).

---

### F-2 — `messages.schema.json` uses Draft-07 `definitions` keyword; siblings use Draft-2020-12 `$defs` (LOW)

**Location**: [packages/shared-types/schema/messages.schema.json:6](../../packages/shared-types/schema/messages.schema.json)

**Observed**:
```bash
$ grep -n "definitions\|\$defs" packages/shared-types/schema/*.schema.json
adapters.schema.json:6:  "$defs": {
graph.schema.json:6:    "$defs": {
messages.schema.json:6:  "definitions": {       ← Draft 07 keyword
messages.schema.json:31:        { "$ref": "#/definitions/WsEnvelope" },
```

**Expected**: Draft 2020-12 (declared via `$schema`) renamed `definitions` → `$defs`. The old keyword still works for backward compatibility, but mixing them in one project is a maintainability hazard.

**Fix**: Rename `definitions` → `$defs` in `messages.schema.json` and update all `$ref: "#/definitions/…"` to `$ref: "#/$defs/…"`. Run `pnpm check-parity` after — the generator should produce byte-identical TS/Python output. Same `packages/shared-types/schema/README.md` line ("Add the definition to the relevant `*.schema.json` under `definitions`") needs updating to `$defs`.

**Why it matters**: Cosmetic, but inconsistency in a 3-file directory is louder than the same inconsistency in a larger codebase. New schemas will copy whichever convention they see first.

**Severity**: LOW (cosmetic; no runtime or codegen impact).

---

### F-3 — `NodeInspector` overlay lacks `role` and `aria-modal` (MEDIUM)

**Location**: [packages/frontend/src/graph/NodeInspector.tsx:22](../../packages/frontend/src/graph/NodeInspector.tsx)

**Observed**: The inspector renders as `<aside style={…}>` with `position: absolute` over the graph canvas. No `role`, no `aria-label`, no `aria-modal`. Screen reader users get no announcement when the inspector opens.

**Expected**:
```tsx
<aside
  role="complementary"
  aria-label="Node inspector"
  aria-modal="true"
  style={…}
>
```

Plus a focus trap (out of scope for this fix) once `App.tsx` mounts the component in Phase 3.

**Why it matters**: The component is now production code on `main`. Wiring it up in Phase 3 inherits this gap unless we fix it at the source.

**Severity**: MEDIUM (accessibility regression entering the public surface; one-line fix).

---

### F-4 — `NodeInspector` has no Escape-key dismiss (MEDIUM)

**Location**: [packages/frontend/src/graph/NodeInspector.tsx:22-75](../../packages/frontend/src/graph/NodeInspector.tsx)

**Observed**: Close button (×) works on click, but Escape does nothing.

**Expected**: Keyboard users press Escape to dismiss overlays. Standard pattern:
```tsx
<aside
  tabIndex={-1}
  onKeyDown={(e) => {
    if (e.key === "Escape" && onClose) onClose();
  }}
  style={…}
>
```

Note: `onKeyDown` needs the `<aside>` to be focusable (`tabIndex={-1}`) or attached to `document` via `useEffect`. The `document`-level handler is the correct choice for modal-style dismiss; defer until Phase 3 wires the component.

**Why it matters**: Keyboard-only users (a11y, power users) can't close the panel without reaching for the mouse. Same scope reasoning as F-3 — fix at the source so Phase 3 inherits it.

**Severity**: MEDIUM.

---

### F-5 — `NodeInspector` has an unnecessary `as unknown as number` zIndex cast (LOW)

**Location**: [packages/frontend/src/graph/NodeInspector.tsx:38](../../packages/frontend/src/graph/NodeInspector.tsx)

**Observed**:
```tsx
zIndex: "var(--z-overlay)" as unknown as number,
```

**Probe** (a scratch file at `packages/frontend/src/scratch.tsx`, then `tsc --noEmit`):
```tsx
import type { CSSProperties } from "react";
const a: CSSProperties = { zIndex: "var(--z-overlay)" };  // ← compiles clean
const b: CSSProperties = { zIndex: 100 };                  // ← also clean
```

`React.CSSProperties.zIndex` is typed `string | number`. The cast is unnecessary and hides intent. Likely a leftover from when React 18 types were stricter.

**Fix** (one line):
```tsx
zIndex: "var(--z-overlay)",
```

**Severity**: LOW (defensive code hygiene; the runtime behavior is correct).

---

### F-6 — `GraphLegend` uses literal `zIndex: 5` while `NodeInspector` uses CSS var (LOW)

**Location**: [packages/frontend/src/graph/GraphLegend.tsx:65](../../packages/frontend/src/graph/GraphLegend.tsx)

**Observed**: GraphLegend → `zIndex: 5,` (literal). NodeInspector → `zIndex: "var(--z-overlay)"` (token, which resolves to 100).

**Expected**: Consistent stacking. Today, the inspector (z=100) intentionally floats above the legend (z=5), which is correct — but the convention should be tokens both sides, e.g. `--z-floating: 5` for the legend.

**Fix**: Add to `tokens.css`:
```css
--z-floating: 5;
```
And in `GraphLegend.tsx`:
```tsx
zIndex: "var(--z-floating)",
```

**Severity**: LOW (cosmetic; tokens.css discipline).

---

### F-7 — Node/edge color tokens use hex; sibling tokens use OKLCH (LOW)

**Location**: [packages/frontend/src/styles/tokens.css:74-83](../../packages/frontend/src/styles/tokens.css)

**Observed**: `--color-bg`, `--color-accent`, etc. all use `oklch(…)`. The new kind tokens use 6-digit hex:
```css
--color-node-file:     #7c6cff;
--color-node-class:    #5eead4;
…
```

**Expected** (either):
- Convert to OKLCH (preserve perceptual lightness across themes), or
- Document the hex choice with a comment block explaining why (e.g., "hex for SVG/Canvas compatibility with renderers that don't grok OKLCH").

**Why it matters**: When Phase 3's renderer or Phase 9's polish work touches the kind palette, the inconsistent format invites either accidental drift (a designer in OKLCH space, output in hex) or partial migration.

**Severity**: LOW (cosmetic + future-maintenance hazard).

---

### F-8 — Agent suggested `WebkitFontSmoothing: "antialiased"` for BrandMark — INCORRECT (NO ACTION)

**Location**: [packages/frontend/src/components/BrandMark.tsx:15-16](../../packages/frontend/src/components/BrandMark.tsx)

**Background**: An automated review flagged `WebkitFontSmoothing: "auto"` and `MozOsxFontSmoothing: "auto"` as "non-standard" and recommended `"antialiased"` / `"grayscale"`.

**Verified against MDN**:
- `-webkit-font-smoothing`: valid values are `auto | none | antialiased | subpixel-antialiased`.
- `-moz-osx-font-smoothing`: valid values are `auto | grayscale`.

Both `"auto"` values are valid per spec. The original demo-branch commit also has a code comment (now stripped during the 1.5.C copy): *"Keep dots crisp — disable any sub-pixel smoothing that might make the dot-grid look fuzzy."* The choice is **intentional**: applying `antialiased` to a dot-matrix font like Doto would round the corners of the dots and blur the grid pattern. The agent's suggested fix would regress the visual.

**Action**: None. Documented here for the record so a future automated review doesn't churn on this.

**Severity**: NONE (intentional choice).

---

### F-9 — `graph.schema.json` references ADR-0005, which does not exist (MEDIUM)

**Location**: [packages/shared-types/schema/graph.schema.json:13](../../packages/shared-types/schema/graph.schema.json)

**Observed**:
```bash
$ grep -rln "ADR-0005" packages/shared-types/schema/ docs/adr/
packages/shared-types/schema/graph.schema.json
$ ls docs/adr/
0001-monorepo-structure.md  0002-trace-transport.md  0003-adapter-design.md  0004-extension-surface.md  README.md
```
The schema description says `"Stable POSIX-relative node ID. See ADR-0005 for the ID scheme."` but ADR-0005 does not exist.

**Expected** (either):
- Create `docs/adr/0005-node-id-scheme.md` (stub or full); content already settled in Phase 2.A plan — node IDs are `<posix-path>:<qualname>` per the parser plan.
- Or remove the reference until ADR-0005 lands.

**Fix** (option 1, takes ~15 minutes, lands the ID scheme as a real ADR before Phase 2):
```bash
cp docs/adr/0004-extension-surface.md docs/adr/0005-node-id-scheme.md
# rewrite body: stable POSIX-relative ID; format <posix-path>:<qualname>; rationale
```

**Fix** (option 2, one-line, defers ADR work):
```diff
- "description": "Stable POSIX-relative node ID. See ADR-0005 for the ID scheme."
+ "description": "Stable POSIX-relative node ID. ID scheme TBD in Phase 2.A."
```

**Why it matters**: The description ships in the generated `graph.ts` and `graph.py` files as docstrings. A future contributor clicking through to ADR-0005 hits 404. The Phase 2 parser will need to commit to an ID scheme; doing so as ADR-0005 now removes the dangling reference.

**Severity**: MEDIUM (documentation accuracy; blocks contributor onboarding for the Phase 2 chunk).

---

### F-10 — Demo branch's `ws/client.test.ts` doesn't cover demo-only store actions (LOW)

**Location**: `demo/end-product-preview` → [packages/frontend/src/ws/client.test.ts](../../packages/frontend/src/ws/client.test.ts)

**Observed**: The demo branch adds these actions to `useGrackleClient`:
- `loadFixture(name)` — sends a `load_fixture` envelope
- `toggleNodeKind(kind)` / `showAllNodeKinds()` — mutates `hiddenNodeKinds`
- `setPulseRate(intervalMs, nodesPerPulse?)` — sends `set_pulse_rate` envelope
- `observedPulseRate(windowMs?)` — derived computation
- Message handlers: `agent_hello`, `graph`, `pulse`

The test file (inherited from main) only covers ping/pong + identity guards. The demo-only surface has zero unit-test coverage.

**Why it matters**: The demo branch isn't in CI (per `DEMO_BRANCH.md` — by design, demos shouldn't gate CI minutes), but the WS client extensions are the largest delta between main and demo. A regression here breaks the demo silently until manually verified.

**Fix** (defer; not blocking): Add a `client-demo.test.ts` on the demo branch only, covering:
- `agent_hello` populates `availableFixtures` and `agentLive`
- `graph` envelope replaces `graph` state and clears `selectedNode` + `livePulses`
- `pulse` envelope populates `livePulses` and appends to `_pulseTimestamps`
- `toggleNodeKind` adds/removes from `hiddenNodeKinds` symmetrically
- `setPulseRate` sends the right envelope shape
- `observedPulseRate` correctly windows the ring buffer (incl. `windowMs > 5000` edge case)

**Severity**: LOW (demo branch; not production).

---

### F-11 — `observedPulseRate` undercounts when `windowMs > 5000` (LOW)

**Location**: `demo/end-product-preview` → [packages/frontend/src/ws/client.ts:162-166](../../packages/frontend/src/ws/client.ts)

**Observed**:
```ts
observedPulseRate: (windowMs = 1500) => {
  const cutoff = performance.now() - windowMs;
  const recent = get()._pulseTimestamps.filter((t) => t > cutoff);
  return (recent.length * 1000) / windowMs;
},
```
The ring buffer (`_pulseTimestamps`) is pruned to the last 5,000 ms. If a caller passes `windowMs = 10_000`, the calc divides by 10s but only finds timestamps from the last 5s → artificially low rate.

**Fix**:
```ts
observedPulseRate: (windowMs = 1500) => {
  const w = Math.min(windowMs, 5000);
  const cutoff = performance.now() - w;
  const recent = get()._pulseTimestamps.filter((t) => t > cutoff);
  return (recent.length * 1000) / w;
},
```

**Why it matters**: The default (1500ms) is well under the 5s buffer, so the current `PulseRateControl` consumer is correct. Any future consumer (a debug overlay, a stats panel) requesting longer windows gets wrong numbers without a runtime warning.

**Severity**: LOW (edge case; default usage is correct).

---

### F-12 — `huge.json` (2.6 MiB) triggers biome size warning on demo branch (LOW)

**Location**: `demo/end-product-preview` → `fixtures/demo-graph/huge.json`

**Observed**:
```bash
$ pnpm exec biome ci . 2>&1
fixtures/demo-graph/huge.json ci ━━━…
  ⚠ The size of the file is 2.6 MiB, which exceeds the configured maximum of 1.0 MiB for this project.
Found 1 warning.
```

**Expected**: The fixture is intentionally large (stress-test fixture, ~4,950 nodes / 6,723 edges per the README). Biome shouldn't try to lint a generated JSON dump.

**Fix**: Add to `biome.json` on the demo branch (NOT main — main doesn't have the fixture):
```json
{
  "files": {
    "includes": ["**", "!fixtures/demo-graph/*.json"]
  }
}
```

Note: per `DEMO_BRANCH.md`, the demo branch isn't in CI — so the warning is local-only noise. Still worth silencing to keep `pnpm exec biome ci .` clean.

**Severity**: LOW (warning, not error; demo only).

---

## Positive patterns

✅ **Prop-driven panels**. `NodeInspector` and `GraphLegend` are 100% prop-driven — no store coupling, no side effects. They render `null` when given `null`, fire callbacks for actions. Easy to test (15/15 tests for them), easy to reuse, easy to mount in any data-flow context (Phase 3 inherits a real `graph/` module instead of a blank canvas).

✅ **WS StrictMode identity guards**. All four listeners (`open`, `close`, `error`, `message`) check `if (get()._ws !== ws) return;`. The regression test (`late events from a stale socket are ignored`) reproduces the React 18 StrictMode double-mount and verifies the guards hold. This is genuine defensive coding.

✅ **Schema-driven codegen parity**. `pnpm check-parity` is non-negotiable in CI; the generated `_generated/graph.py` (Python TypedDicts) stays in sync with the TS interface and JSON Schema. Phase 1.5's schema addition was caught by parity in pre-commit.

✅ **Hand-rolled runtime validation vs. codegen catalog**. `protocol.py` validates the WS envelope with an inline `_ENVELOPE_SCHEMA` rather than importing the published schema. This decouples runtime validation from codegen and avoids the F-1 footgun *in practice*. The convention should be made explicit in `packages/shared-types/schema/README.md`.

✅ **Immutable Zustand updates**. Every action that mutates a Set or array creates a fresh one (`new Set(...)`, `[...arr]`). Shallow-equality selectors in components work as expected.

✅ **Demo-branch type extension**. `DemoGraph extends Graph` + `DemoGraphNode extends GraphNode` lets the demo carry fixture-only fields (`name`, `label`, `description`, `x`, `y`) without forking the shared types. Rebase cost stays low.

✅ **Lefthook hooks intercept format/lint failures pre-commit**. F-12-style noise (biome size warnings) and commitlint scope failures got caught at the developer machine, not in CI.

---

## Recommended actions

**Ship-blocking**: None. Phase 1.5 and 1.6 are production-ready.

**Land before Phase 2 (single follow-up PR, ~45 min)**:

| Finding | Action | LoC | Priority |
|---|---|---|---|
| F-1 | Add top-level `$ref: "#/$defs/Graph"` to graph.schema.json + same for adapters/messages | ~10 | HIGH |
| F-9 | Create `docs/adr/0005-node-id-scheme.md` (Phase 2 will need it regardless) | ~50 | MEDIUM |
| F-3 | Add `role="complementary"` + `aria-label` + `aria-modal` to NodeInspector | ~3 | MEDIUM |
| F-5 | Remove `as unknown as number` zIndex cast in NodeInspector | 1 | LOW |
| F-2 | Rename `definitions` → `$defs` in messages.schema.json | ~5 | LOW |

**Optional polish (defer to Phase 9)**:

| Finding | Action | LoC | Priority |
|---|---|---|---|
| F-4 | Document-level Escape handler for NodeInspector when mounted in Phase 3 | ~8 | MEDIUM |
| F-6 | Add `--z-floating: 5` token + use it in GraphLegend | ~3 | LOW |
| F-7 | Document the hex-vs-OKLCH choice or convert to OKLCH | ~10 | LOW |
| F-11 | Clamp `observedPulseRate` window to 5000 ms (demo branch only) | ~2 | LOW |
| F-12 | Add `fixtures/demo-graph/*.json` to biome ignore (demo branch only) | ~3 | LOW |
| F-10 | Add `client-demo.test.ts` covering demo-only store actions | ~80 | LOW |

**No action**: F-8 (intentional dot-grid rendering choice).

---

## Verification matrix

| Probe | Command | Result |
|---|---|---|
| TS typecheck (main) | `pnpm --filter @grackle/frontend exec tsc --noEmit` | ✅ clean |
| TS typecheck (demo) | (same on demo branch) | ✅ clean |
| Frontend tests (main) | `pnpm --filter @grackle/frontend test --run` | ✅ 30/30 pass |
| Frontend tests (demo) | (same on demo branch) | ✅ 30/30 pass |
| Biome (main) | `pnpm exec biome ci .` | ✅ clean |
| Biome (demo) | (same on demo branch) | ⚠️ 1 warning (F-12, `huge.json`) |
| Parity check | `pnpm check-parity` | ✅ all 6 generated files up to date |
| Ruff check + format | `cd packages/agent && uv run ruff check . && uv run ruff format --check .` | ✅ clean both branches |
| mypy --strict | `uv run mypy --strict src` | ✅ clean both branches (10 main / 11 demo files) |
| pytest | `uv run pytest -q` | ✅ 31/31 pass both branches |
| AJV schema probes | `node /tmp/test-schema.mjs` | ⚠️ 5/13 pass — F-1 |
| Sigma/graphology imports on main | `grep -rln "from .sigma" packages/frontend/src` | ✅ no imports (tree-shaken) |
| Doto symlink | `ls packages/frontend/node_modules/@fontsource-variable/doto/` | ✅ resolved |
| ADR cross-references | `grep -rln "ADR-0005" docs/adr/` | ⚠️ broken — F-9 |
| CSS var → number cast | scratch `tsc --noEmit` with `CSSProperties.zIndex = "var(...)"` | ✅ compiles clean (F-5 cast unnecessary) |

## Glossary of files reviewed

**On `main`** (post Phase 1.6 squash merge `f9048f1`):
- `packages/shared-types/schema/graph.schema.json` ⚠️ F-1, F-9
- `packages/shared-types/schema/messages.schema.json` ⚠️ F-2
- `packages/shared-types/src/graph.ts` ✅
- `packages/shared-types/src/generated/graph.ts` (codegen artifact; gitignored)
- `packages/frontend/src/components/BrandMark.tsx` ✅
- `packages/frontend/src/styles/tokens.css` ⚠️ F-6, F-7
- `packages/frontend/src/styles/index.css` ✅
- `packages/frontend/src/ws/client.ts` ✅ (StrictMode guards)
- `packages/frontend/src/ws/client.test.ts` ✅ (regression test added)
- `packages/frontend/src/graph/NodeInspector.tsx` ⚠️ F-3, F-4, F-5
- `packages/frontend/src/graph/NodeInspector.test.tsx` ✅ (8 tests)
- `packages/frontend/src/graph/GraphLegend.tsx` ⚠️ F-6
- `packages/frontend/src/graph/GraphLegend.test.tsx` ✅ (7 tests)
- `packages/frontend/src/graph/index.ts` ✅

**On `demo/end-product-preview`** (post-1.6.E rebase, commit `97c519d`):
- `packages/frontend/src/graph/demo-types.ts` ✅
- `packages/frontend/src/ws/client.ts` (demo extensions) ⚠️ F-10 (no coverage), F-11 (observedPulseRate edge)
- `packages/frontend/src/App.tsx` (prop-wired panels) ✅
- `README.md` (rebuilt for demo) ✅
- `DEMO_BRANCH.md` ✅
- `fixtures/demo-graph/huge.json` ⚠️ F-12
