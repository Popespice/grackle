/**
 * `panels/init.ts` — the app's panel layout (campaign T11-6,
 * `docs/test-campaigns/phase-12.md`). It had no test.
 *
 * The module's whole job is a side effect: 18 `panels.register` calls into
 * the singleton registry. Slots are open strings (ADR-0004) and an unknown
 * slot is a silent no-op in `SlotContainer`, so a typo, or a panel moved to
 * a slot `App.tsx` never renders, ships an invisible panel with no error
 * anywhere. This file pins the layout table and cross-checks every slot used
 * here against the slots `App.tsx` actually renders.
 *
 * The registry is re-imported fresh (`vi.resetModules()`) and its `register`
 * spied BEFORE `init` is evaluated, so the test sees every registration —
 * including one into a slot none of the six known slots would list.
 */
import type { ComponentType } from "react";
import { beforeAll, describe, expect, it, vi } from "vitest";
import appSource from "../App.tsx?raw";
import type { PanelEntry, PanelRegistry } from "./registry";

// init imports GraphCanvas, and Sigma reads WebGL2RenderingContext at module
// load — absent in jsdom. Nothing here renders, so inert stand-ins suffice.
vi.mock("sigma", () => ({ default: class {} }));
vi.mock("graphology-layout-forceatlas2/worker", () => ({ default: class {} }));

let registry: PanelRegistry;
let registered: PanelEntry[];

// id → [module path relative to this directory, exported component name]
const COMPONENT_SOURCES: Record<string, [string, string]> = {
  "header-chrome": ["./HeaderChrome", "HeaderChrome"],
  "search-filter": ["./SearchFilterPanel", "SearchFilterPanel"],
  "session-library-panel": ["./SessionLibraryPanel", "SessionLibraryPanel"],
  "graph-canvas": ["../graph/GraphCanvas", "GraphCanvas"],
  "network-view-panel": ["./NetworkViewPanel", "NetworkViewPanel"],
  "source-viewer": ["./SourceViewer", "SourceViewer"],
  "node-inspector": ["./NodeInspectorPanel", "NodeInspectorPanel"],
  "value-inspector": ["./ValueInspectorPanel", "ValueInspectorPanel"],
  "graph-legend": ["./GraphLegendPanel", "GraphLegendPanel"],
  "edge-evidence": ["./EdgeEvidencePanel", "EdgeEvidencePanel"],
  "causal-path": ["./CausalPathPanel", "CausalPathPanel"],
  "cycles-panel": ["./CyclesPanel", "CyclesPanel"],
  "predicted-heat-panel": ["./PredictedHeatPanel", "PredictedHeatPanel"],
  "diff-panel": ["./DiffPanel", "DiffPanel"],
  "timeline-panel": ["./TimelinePanel", "TimelinePanel"],
  "loss-curve-panel": ["./LossCurvePanel", "LossCurvePanel"],
  "flame-graph-panel": ["./FlameGraphPanel", "FlameGraphPanel"],
  "stats-panel": ["./StatsPanel", "StatsPanel"],
};

// The layout, slot by slot, in render order (registry sorts by `order`).
const LAYOUT: Record<string, [id: string, order: number][]> = {
  "top-bar": [["header-chrome", 0]],
  "left-sidebar": [
    ["search-filter", 0],
    ["session-library-panel", 5],
  ],
  "floating-overlay": [
    ["graph-canvas", 0],
    ["network-view-panel", 10],
  ],
  "right-sidebar": [
    ["source-viewer", 5],
    ["node-inspector", 10],
    ["value-inspector", 15],
    ["graph-legend", 20],
    ["edge-evidence", 25],
    ["causal-path", 27],
    ["cycles-panel", 30],
    ["predicted-heat-panel", 32],
    ["diff-panel", 35],
  ],
  "bottom-dock": [
    ["timeline-panel", 0],
    ["loss-curve-panel", 5],
    ["flame-graph-panel", 10],
  ],
  "bottom-status": [["stats-panel", 0]],
};

/** Every `<SlotContainer slot="…" />` in App.tsx, in source order. */
function slotsRenderedByApp(): string[] {
  return [...appSource.matchAll(/<SlotContainer\s+slot="([^"]+)"/g)].map(
    (m) => m[1] as string
  );
}

beforeAll(async () => {
  vi.resetModules();
  const mod = await import("./registry");
  registry = mod.panels;
  const spy = vi.spyOn(registry, "register");
  await import("./init");
  registered = spy.mock.calls.map(([entry]) => entry);
});

describe("panels/init — registration", () => {
  it("registers 18 panels, each id once", () => {
    const ids = registered.map((e) => e.id);
    expect(ids).toHaveLength(18);
    expect(new Set(ids).size).toBe(18);
  });

  it("lays out every slot exactly as pinned, in order", () => {
    for (const [slot, expected] of Object.entries(LAYOUT)) {
      const actual = registry
        .getForSlot(slot)
        .map((e) => [e.id, e.order] as [string, number]);
      expect(actual, slot).toEqual(expected);
    }
  });

  it("puts every registered panel in one of the pinned slots", () => {
    const pinned = new Set(Object.keys(LAYOUT));
    const stray = registered.filter((e) => !pinned.has(e.slot));
    expect(stray.map((e) => `${e.id} → ${e.slot}`)).toEqual([]);
  });

  it("wires each id to its own component", async () => {
    // Loaded after init, so these are the same module instances it imported.
    for (const entry of registered) {
      const source = COMPONENT_SOURCES[entry.id];
      expect(source, entry.id).toBeDefined();
      const [path, name] = source as [string, string];
      const mod = (await import(/* @vite-ignore */ path)) as Record<
        string,
        ComponentType
      >;
      expect(entry.component, entry.id).toBe(mod[name]);
    }
  });

  it("sets no hideWhen: every panel owns its own empty state (ADR-0007)", () => {
    expect(registered.filter((e) => e.hideWhen !== undefined)).toEqual([]);
  });
});

describe("panels/init — the slots App.tsx renders", () => {
  it("App.tsx renders exactly the six layout slots, each once", () => {
    const rendered = slotsRenderedByApp();
    expect(rendered).toHaveLength(6);
    expect(new Set(rendered)).toEqual(new Set(Object.keys(LAYOUT)));
  });

  it("every slot a panel is registered into is one App.tsx renders", () => {
    // An unknown slot is a silent no-op in SlotContainer (ADR-0004): a panel
    // registered anywhere else would never appear, and nothing would say so.
    const rendered = new Set(slotsRenderedByApp());
    const invisible = registered.filter((e) => !rendered.has(e.slot));
    expect(invisible.map((e) => `${e.id} → ${e.slot}`)).toEqual([]);
  });

  it("App.tsx loads the panel layout", () => {
    expect(appSource).toMatch(/^import "\.\/panels\/init";$/m);
  });
});
