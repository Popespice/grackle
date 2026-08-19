import { describe, expect, it } from "vitest";
import {
  hitTest,
  layoutNetwork,
  MAX_NEURONS_PER_COLUMN,
} from "./networkLayout";
import type { NetworkSpec } from "./networkSpec";

const TOY_SPEC: NetworkSpec = {
  tokens: [{ kind: "linear", inDim: 2, outDim: 3 }],
  columns: [2, 3],
};

const DEMO_SPEC: NetworkSpec = {
  tokens: [
    { kind: "linear", inDim: 2, outDim: 32 },
    { kind: "activation", name: "relu" },
    { kind: "linear", inDim: 32, outDim: 32 },
    { kind: "activation", name: "relu" },
    { kind: "linear", inDim: 32, outDim: 3 },
  ],
  columns: [2, 32, 32, 3],
};

describe("layoutNetwork — exact positions for a 2-col toy", () => {
  it("places columns at fixed x fractions and neurons centered vertically", () => {
    const layout = layoutNetwork(TOY_SPEC, 200, 300);
    expect(layout).not.toBeNull();
    const [col0, col1] = layout?.columns ?? [];

    expect(col0?.x).toBe(40);
    expect(col0?.neurons.map((n) => n.y)).toEqual([139, 161]);
    expect(col0?.caption).toBe("input · 2");
    expect(col0?.overflowLabel).toBeNull();

    expect(col1?.x).toBe(160);
    expect(col1?.neurons.map((n) => n.y)).toEqual([128, 150, 172]);
    expect(col1?.caption).toBe("logits · 3");
  });

  it("builds one bundle spanning both columns with the cartesian-product pair count", () => {
    const layout = layoutNetwork(TOY_SPEC, 200, 300);
    expect(layout?.bundles).toHaveLength(1);
    const bundle = layout?.bundles[0];
    expect(bundle?.inDim).toBe(2);
    expect(bundle?.outDim).toBe(3);
    expect(bundle?.label).toBe("linear 2→3");
    expect(bundle?.pairs).toHaveLength(6); // 2 x 3
    expect(bundle?.labelX).toBe(100);
    expect(bundle?.labelY).toBe(150);
  });

  it("emits no glyphs for a spec with no activation tokens", () => {
    const layout = layoutNetwork(TOY_SPEC, 200, 300);
    expect(layout?.glyphs).toEqual([]);
  });
});

describe("layoutNetwork — the demo net (D-N3's 1184-line figure)", () => {
  it("produces bundle pair counts 2x32, 32x32, 32x3 = 64 + 1024 + 96", () => {
    const layout = layoutNetwork(DEMO_SPEC, 800, 600);
    const counts = layout?.bundles.map((b) => b.pairs.length);
    expect(counts).toEqual([64, 1024, 96]);
    const total = counts?.reduce((a, b) => a + b, 0);
    expect(total).toBe(1184);
  });

  it("anchors each activation glyph at the shared column between its bundles", () => {
    const layout = layoutNetwork(DEMO_SPEC, 800, 600);
    expect(layout?.glyphs).toHaveLength(2);
    expect(layout?.glyphs[0]).toMatchObject({
      tokenIndex: 1,
      name: "relu",
      columnIndex: 1,
    });
    expect(layout?.glyphs[1]).toMatchObject({
      tokenIndex: 3,
      name: "relu",
      columnIndex: 2,
    });
    // Glyph x matches its column's x exactly.
    expect(layout?.glyphs[0]?.x).toBe(layout?.columns[1]?.x);
    expect(layout?.glyphs[1]?.x).toBe(layout?.columns[2]?.x);
  });

  it("four columns, one more than the number of linear tokens", () => {
    const layout = layoutNetwork(DEMO_SPEC, 800, 600);
    expect(layout?.columns.map((c) => c.count)).toEqual([2, 32, 32, 3]);
    expect(layout?.columns.map((c) => c.caption)).toEqual([
      "input · 2",
      "hidden · 32",
      "hidden · 32",
      "logits · 3",
    ]);
  });
});

describe("layoutNetwork — the 64-neuron cap", () => {
  it("caps rendered neurons at 64 and sets an overflow label", () => {
    const spec: NetworkSpec = {
      tokens: [{ kind: "linear", inDim: 100, outDim: 5 }],
      columns: [100, 5],
    };
    const layout = layoutNetwork(spec, 400, 800);
    const col0 = layout?.columns[0];
    expect(col0?.count).toBe(100);
    expect(col0?.neurons).toHaveLength(MAX_NEURONS_PER_COLUMN);
    // States what IS drawn: a bare "×100" read as "×100 not drawn" in the
    // tooltip, claiming the whole column was omitted when 64 of it is shown.
    expect(col0?.overflowLabel).toBe("64 of 100 shown");
  });

  it("does not cap a column at or under the limit", () => {
    const spec: NetworkSpec = {
      tokens: [{ kind: "linear", inDim: 64, outDim: 1 }],
      columns: [64, 1],
    };
    const layout = layoutNetwork(spec, 400, 800);
    expect(layout?.columns[0]?.neurons).toHaveLength(64);
    expect(layout?.columns[0]?.overflowLabel).toBeNull();
  });

  it("bundle pairs use the RENDERED (capped) neuron count, not the full count", () => {
    const spec: NetworkSpec = {
      tokens: [{ kind: "linear", inDim: 200, outDim: 2 }],
      columns: [200, 2],
    };
    const layout = layoutNetwork(spec, 400, 800);
    expect(layout?.bundles[0]?.pairs).toHaveLength(MAX_NEURONS_PER_COLUMN * 2);
  });
});

describe("layoutNetwork — neuron radius", () => {
  it("gives a lone neuron the full radius", () => {
    const spec: NetworkSpec = {
      tokens: [{ kind: "linear", inDim: 1, outDim: 1 }],
      columns: [1, 1],
    };
    const layout = layoutNetwork(spec, 200, 300);
    expect(layout?.columns[0]?.radius).toBe(7);
  });

  it("shrinks a multi-neuron column whose rows collapsed for want of height", () => {
    // height < 2 * ROW_PADDING_Y leaves zero usable vertical room, so every
    // neuron lands on the same y. Drawing those at the MAXIMUM radius (the
    // lone-neuron case) would fuse the column into one solid blob.
    const layout = layoutNetwork(TOY_SPEC, 200, 40);
    expect(layout?.columns[0]?.neurons.map((n) => n.y)).toEqual([20, 20]);
    expect(layout?.columns[0]?.radius).toBe(2);
    expect(layout?.columns[1]?.radius).toBe(2);
  });
});

describe("layoutNetwork — degenerate inputs", () => {
  it("returns null for non-positive width or height", () => {
    expect(layoutNetwork(TOY_SPEC, 0, 300)).toBeNull();
    expect(layoutNetwork(TOY_SPEC, 200, 0)).toBeNull();
    expect(layoutNetwork(TOY_SPEC, -10, 300)).toBeNull();
  });

  it("returns null for a spec with no columns", () => {
    expect(layoutNetwork({ tokens: [], columns: [] }, 200, 300)).toBeNull();
  });
});

describe("hitTest", () => {
  const layout = layoutNetwork(TOY_SPEC, 200, 300);

  it("hits a neuron and returns its column", () => {
    expect(layout && hitTest(layout, 40, 139)).toEqual({
      kind: "column",
      columnIndex: 0,
    });
  });

  it("hits a bundle line at its midpoint", () => {
    // Midpoint between (40, 139) and (160, 128) — the first src/dst pair.
    const midX = (40 + 160) / 2;
    const midY = (139 + 128) / 2;
    expect(layout && hitTest(layout, midX, midY)).toEqual({
      kind: "bundle",
      tokenIndex: 0,
    });
  });

  it("returns null far from everything", () => {
    expect(layout && hitTest(layout, -1000, -1000)).toBeNull();
  });

  it("hits a glyph before a coincidentally nearby neuron", () => {
    const demoLayout = layoutNetwork(DEMO_SPEC, 800, 600);
    const glyph = demoLayout?.glyphs[0];
    expect(glyph).toBeDefined();
    if (!glyph) return;
    expect(demoLayout && hitTest(demoLayout, glyph.x, glyph.y)).toEqual({
      kind: "glyph",
      tokenIndex: glyph.tokenIndex,
    });
  });
});
