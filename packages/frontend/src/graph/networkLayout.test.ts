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

describe("layoutNetwork — invariants across a canvas-size sweep", () => {
  const SIZES: [number, number][] = [
    [200, 300],
    [800, 400],
    [800, 600],
    [1400, 900],
    [400, 80],
    [400, 48], // exactly 2 * ROW_PADDING_Y — zero usable height
    [120, 1000],
    [2000, 120],
  ];
  const SPECS: [string, NetworkSpec][] = [
    ["toy", TOY_SPEC],
    ["demo", DEMO_SPEC],
    [
      "wide",
      {
        tokens: [{ kind: "linear", inDim: 512, outDim: 512 }],
        columns: [512, 512],
      },
    ],
    [
      "single-neuron",
      { tokens: [{ kind: "linear", inDim: 1, outDim: 1 }], columns: [1, 1] },
    ],
  ];

  for (const [specName, spec] of SPECS) {
    for (const [w, h] of SIZES) {
      it(`${specName} @ ${w}x${h}: neurons inside the canvas, columns ordered, counts capped`, () => {
        const layout = layoutNetwork(spec, w, h);
        expect(layout).not.toBeNull();
        if (!layout) return;

        const xs = layout.columns.map((c) => c.x);
        expect(xs).toEqual([...xs].sort((a, b) => a - b));

        for (const column of layout.columns) {
          expect(column.neurons.length).toBe(
            Math.min(column.count, MAX_NEURONS_PER_COLUMN)
          );
          expect(column.radius).toBeGreaterThan(0);
          for (const neuron of column.neurons) {
            // A neuron drawn outside the canvas is a neuron the user cannot
            // see or hover; its own radius has to fit too.
            expect(neuron.y - column.radius).toBeGreaterThanOrEqual(0);
            expect(neuron.y + column.radius).toBeLessThanOrEqual(h);
            expect(neuron.x).toBeGreaterThanOrEqual(0);
            expect(neuron.x).toBeLessThanOrEqual(w);
          }
        }

        for (const bundle of layout.bundles) {
          const src = layout.columns[bundle.srcColumnIndex];
          const dst = layout.columns[bundle.dstColumnIndex];
          expect(bundle.pairs).toHaveLength(
            (src?.neurons.length ?? 0) * (dst?.neurons.length ?? 0)
          );
          // The hover path walks every pair on every mousemove.
          expect(bundle.pairs.length).toBeLessThanOrEqual(
            MAX_NEURONS_PER_COLUMN * MAX_NEURONS_PER_COLUMN
          );
          expect(bundle.labelX).toBeGreaterThanOrEqual(src?.x ?? 0);
          expect(bundle.labelX).toBeLessThanOrEqual(dst?.x ?? w);
        }
      });
    }
  }
});

describe("layoutNetwork — column/bundle/glyph correspondence", () => {
  it("gives every linear token a bundle and every activation a glyph, in token order", () => {
    const layout = layoutNetwork(DEMO_SPEC, 800, 400);
    expect(layout?.bundles.map((b) => b.tokenIndex)).toEqual([0, 2, 4]);
    expect(layout?.glyphs.map((g) => g.tokenIndex)).toEqual([1, 3]);
    // Each bundle spans adjacent columns, left to right, with no gaps.
    expect(
      layout?.bundles.map((b) => [b.srcColumnIndex, b.dstColumnIndex])
    ).toEqual([
      [0, 1],
      [1, 2],
      [2, 3],
    ]);
  });

  it("a leading activation glyph sits on the input column", () => {
    const spec: NetworkSpec = {
      tokens: [
        { kind: "activation", name: "tanh" },
        { kind: "linear", inDim: 4, outDim: 2 },
      ],
      columns: [4, 2],
    };
    const layout = layoutNetwork(spec, 400, 300);
    expect(layout?.glyphs[0]?.columnIndex).toBe(0);
    expect(layout?.glyphs[0]?.x).toBe(layout?.columns[0]?.x);
  });

  it("a trailing activation glyph sits on the logits column", () => {
    const spec: NetworkSpec = {
      tokens: [
        { kind: "linear", inDim: 4, outDim: 2 },
        { kind: "activation", name: "softmax" },
      ],
      columns: [4, 2],
    };
    const layout = layoutNetwork(spec, 400, 300);
    expect(layout?.glyphs[0]?.columnIndex).toBe(1);
    expect(layout?.glyphs[0]?.x).toBe(layout?.columns[1]?.x);
  });

  it("shrinks the radius as a column gets denser at a fixed height", () => {
    const radiusFor = (count: number) =>
      layoutNetwork(
        {
          tokens: [{ kind: "linear", inDim: count, outDim: 1 }],
          columns: [count, 1],
        },
        400,
        600
      )?.columns[0]?.radius ?? 0;
    const radii = [2, 8, 32, 64].map(radiusFor);
    expect(radii).toEqual([...radii].sort((a, b) => b - a));
    expect(radii[0]).toBeGreaterThan(radii[3] ?? 0);
  });
});

describe("hitTest — every drawn element is reachable", () => {
  it("hits every neuron of a glyph-free column as that column", () => {
    const layout = layoutNetwork(DEMO_SPEC, 800, 400);
    if (!layout) throw new Error("expected a layout");
    const column = layout.columns[0]; // input column: no activation above it
    expect(column?.neurons.length).toBe(2);
    for (const neuron of column?.neurons ?? []) {
      expect(hitTest(layout, neuron.x, neuron.y)).toEqual({
        kind: "column",
        columnIndex: 0,
      });
    }
  });

  it("hits every activation glyph at its own center", () => {
    const layout = layoutNetwork(DEMO_SPEC, 800, 400);
    if (!layout) throw new Error("expected a layout");
    expect(layout.glyphs).toHaveLength(2);
    for (const glyph of layout.glyphs) {
      expect(hitTest(layout, glyph.x, glyph.y)).toEqual({
        kind: "glyph",
        tokenIndex: glyph.tokenIndex,
      });
    }
  });

  it("hits each bundle at the midpoint of its own first pair", () => {
    const layout = layoutNetwork(DEMO_SPEC, 800, 400);
    if (!layout) throw new Error("expected a layout");
    expect(layout.bundles).toHaveLength(3);
    for (const bundle of layout.bundles) {
      const pair = bundle.pairs[0];
      if (!pair) throw new Error("expected pairs");
      const midX = (pair.src.x + pair.dst.x) / 2;
      const midY = (pair.src.y + pair.dst.y) / 2;
      expect(hitTest(layout, midX, midY)).toEqual({
        kind: "bundle",
        tokenIndex: bundle.tokenIndex,
      });
    }
  });

  it("misses just outside a lone neuron's hit radius", () => {
    const spec: NetworkSpec = {
      tokens: [{ kind: "linear", inDim: 1, outDim: 1 }],
      columns: [1, 1],
    };
    const layout = layoutNetwork(spec, 600, 400);
    if (!layout) throw new Error("expected a layout");
    const neuron = layout.columns[0]?.neurons[0];
    if (!neuron) throw new Error("expected a neuron");
    // 10px is the documented hit radius; 9 hits, 11 does not (and is far from
    // the single bundle line, which runs horizontally through both neurons).
    expect(hitTest(layout, neuron.x, neuron.y - 9)?.kind).toBe("column");
    expect(hitTest(layout, neuron.x, neuron.y - 11)).toBeNull();
  });
});

/**
 * ─── KNOWN DEFECTS ────────────────────────────────────────────────────────
 *
 * `it.fails` asserts the test throws, so these stay green while the
 * expectation is on record; fixing the module makes them start failing, which
 * is the cue to drop the `.fails`. All three are the same root cause: GLYPH_Y
 * (16) and ROW_PADDING_Y (24) were chosen independently of the glyph's paint
 * radius and of HIT_RADIUS.
 */
describe("KNOWN DEFECT — activation glyphs collide with the first neuron row", () => {
  /** paintNetwork draws activation pills as `arc(x, y, 8, …)` — the layout
   *  module places them but does not know their size. */
  const GLYPH_PAINT_RADIUS = 8;

  it.fails("the top neuron of a glyph column is hittable as that column", () => {
    // A dense column's rows start at exactly ROW_PADDING_Y = 24, and the glyph
    // sits at GLYPH_Y = 16 — 8px away, inside the 10px HIT_RADIUS, and glyphs
    // are checked first. So the topmost neuron of every column that carries an
    // activation reports "relu" on hover instead of "hidden · 32". True at
    // every size the panel actually renders at (the card is inset 48px, so
    // ~1300x800 on a 1440 display; the demo net's hidden columns are dense at
    // any height).
    const layout = layoutNetwork(DEMO_SPEC, 800, 400);
    if (!layout) throw new Error("expected a layout");
    const column = layout.columns[1];
    const top = column?.neurons[0];
    if (!column || !top) throw new Error("expected neurons");
    expect(hitTest(layout, top.x, top.y)).toEqual({
      kind: "column",
      columnIndex: 1,
    }); // is { kind: "glyph", tokenIndex: 1 }
  });

  it.fails("the glyph pill does not overlap the first neuron it sits above", () => {
    // The module docstring claims GLYPH_Y is "clear of the neuron rows (which
    // start no higher than ROW_PADDING_Y) regardless of column density". It is
    // clear of the row's CENTER by exactly 8px and of the pill's own edge by
    // 0px — before either circle's radius is counted. At 800x400 the pill
    // (y 8..24) and the top neuron (y 20.4..27.6) overlap by ~3.6px.
    const layout = layoutNetwork(DEMO_SPEC, 800, 400);
    if (!layout) throw new Error("expected a layout");
    const glyph = layout.glyphs[0];
    const column = layout.columns[1];
    const top = column?.neurons[0];
    if (!glyph || !column || !top) throw new Error("expected a full layout");
    expect(glyph.y + GLYPH_PAINT_RADIUS).toBeLessThanOrEqual(
      top.y - column.radius
    );
  });
});

describe("KNOWN DEFECT — a canvas narrower than the horizontal padding inverts", () => {
  it.fails("keeps columns left-to-right below 2 * COLUMN_PADDING_X", () => {
    // columnX spreads over `width - 2 * COLUMN_PADDING_X`, which goes NEGATIVE
    // under 80px: the columns run backwards, so the input column is drawn on
    // the right and every bundle points the wrong way. Latent rather than
    // live — the card would need a viewport under ~210px — but the fix is a
    // Math.max(0, …) on `usable`, and a layout that silently mirrors itself is
    // a bad failure mode to leave armed.
    const layout = layoutNetwork(DEMO_SPEC, 60, 300);
    if (!layout) throw new Error("expected a layout");
    const xs = layout.columns.map((c) => c.x);
    expect(xs).toEqual([...xs].sort((a, b) => a - b)); // is [40, 33.3, 26.7, 20]
  });
});
