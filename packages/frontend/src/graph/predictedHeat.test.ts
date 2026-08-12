import { describe, expect, it } from "vitest";
import {
  PREDICTION_STATUS_COLORS,
  PREDICTION_THRESHOLD,
  type PredictedHeat,
  parsePredictedHeat,
  predictionStatuses,
} from "./predictedHeat";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function makeMetadataGraph(predictedHeat: unknown): { metadata: unknown } {
  return { metadata: { predicted_heat: predictedHeat } };
}

function makePredicted(
  entries: Record<string, number>,
  modelVersion = 1
): PredictedHeat {
  const scores = new Map(Object.entries(entries));
  const max = Math.max(...scores.values());
  return { modelVersion, scores, max };
}

// ---------------------------------------------------------------------------
// parsePredictedHeat
// ---------------------------------------------------------------------------

describe("parsePredictedHeat", () => {
  it("returns null for undefined graph", () => {
    expect(parsePredictedHeat(undefined)).toBeNull();
  });

  it("returns null for null graph", () => {
    expect(parsePredictedHeat(null)).toBeNull();
  });

  it("returns null when metadata is missing", () => {
    expect(parsePredictedHeat({})).toBeNull();
  });

  it("returns null when metadata is not an object", () => {
    expect(parsePredictedHeat({ metadata: "nope" } as never)).toBeNull();
    expect(parsePredictedHeat({ metadata: 42 } as never)).toBeNull();
    expect(parsePredictedHeat({ metadata: null } as never)).toBeNull();
  });

  it("returns null when metadata.predicted_heat is missing", () => {
    expect(parsePredictedHeat({ metadata: {} })).toBeNull();
  });

  it("returns null when predicted_heat is not an object", () => {
    expect(parsePredictedHeat(makeMetadataGraph("nope"))).toBeNull();
    expect(parsePredictedHeat(makeMetadataGraph(42))).toBeNull();
    expect(parsePredictedHeat(makeMetadataGraph(null))).toBeNull();
  });

  it("returns null when scores is not an array", () => {
    expect(
      parsePredictedHeat(
        makeMetadataGraph({ model_version: 1, scores: "nope" })
      )
    ).toBeNull();
    expect(
      parsePredictedHeat(makeMetadataGraph({ model_version: 1, scores: {} }))
    ).toBeNull();
  });

  it("skips a non-object entry but keeps valid ones", () => {
    const result = parsePredictedHeat(
      makeMetadataGraph({
        model_version: 1,
        scores: ["not-an-object", { node_id: "a", score: 0.5 }],
      })
    );
    expect(result?.scores.size).toBe(1);
    expect(result?.scores.get("a")).toBe(0.5);
  });

  it("skips an entry missing node_id but keeps valid ones", () => {
    const result = parsePredictedHeat(
      makeMetadataGraph({
        model_version: 1,
        scores: [{ score: 0.5 }, { node_id: "a", score: 0.5 }],
      })
    );
    expect(result?.scores.size).toBe(1);
    expect(result?.scores.has("a")).toBe(true);
  });

  it("skips an entry with a non-string node_id but keeps valid ones", () => {
    const result = parsePredictedHeat(
      makeMetadataGraph({
        model_version: 1,
        scores: [
          { node_id: 123, score: 0.5 },
          { node_id: "a", score: 0.5 },
        ],
      })
    );
    expect(result?.scores.size).toBe(1);
    expect(result?.scores.has("a")).toBe(true);
  });

  it("skips an entry missing score but keeps valid ones", () => {
    const result = parsePredictedHeat(
      makeMetadataGraph({
        model_version: 1,
        scores: [{ node_id: "b" }, { node_id: "a", score: 0.5 }],
      })
    );
    expect(result?.scores.size).toBe(1);
    expect(result?.scores.has("a")).toBe(true);
  });

  it("skips an entry with a non-finite score but keeps valid ones", () => {
    const result = parsePredictedHeat(
      makeMetadataGraph({
        model_version: 1,
        scores: [
          { node_id: "b", score: Number.NaN },
          { node_id: "c", score: Number.POSITIVE_INFINITY },
          { node_id: "a", score: 0.5 },
        ],
      })
    );
    expect(result?.scores.size).toBe(1);
    expect(result?.scores.has("a")).toBe(true);
  });

  it("returns null for an empty scores array", () => {
    expect(
      parsePredictedHeat(makeMetadataGraph({ model_version: 1, scores: [] }))
    ).toBeNull();
  });

  it("returns null when every entry is invalid", () => {
    const result = parsePredictedHeat(
      makeMetadataGraph({
        model_version: 1,
        scores: [
          "nope",
          { score: 0.5 },
          { node_id: 1, score: 0.5 },
          { node_id: "a", score: Number.NaN },
        ],
      })
    );
    expect(result).toBeNull();
  });

  it("parses a valid single entry", () => {
    const result = parsePredictedHeat(
      makeMetadataGraph({
        model_version: 3,
        scores: [{ node_id: "a", score: 0.75 }],
      })
    );
    expect(result).not.toBeNull();
    expect(result?.modelVersion).toBe(3);
    expect(result?.scores.get("a")).toBe(0.75);
    expect(result?.max).toBe(0.75);
  });

  it("computes max correctly across multiple entries", () => {
    const result = parsePredictedHeat(
      makeMetadataGraph({
        model_version: 1,
        scores: [
          { node_id: "a", score: 0.1 },
          { node_id: "b", score: 0.9 },
          { node_id: "c", score: 0.4 },
        ],
      })
    );
    expect(result?.max).toBe(0.9);
  });

  it("max is determined by the single highest score, regardless of order", () => {
    const result = parsePredictedHeat(
      makeMetadataGraph({
        model_version: 1,
        scores: [
          { node_id: "a", score: 0.3 },
          { node_id: "b", score: 0.99 },
          { node_id: "c", score: 0.5 },
          { node_id: "d", score: 0.02 },
        ],
      })
    );
    expect(result?.max).toBe(0.99);
  });
});

// ---------------------------------------------------------------------------
// PREDICTION_STATUS_COLORS
// ---------------------------------------------------------------------------

describe("PREDICTION_STATUS_COLORS", () => {
  it("over and under are hex, on-target is empty", () => {
    expect(PREDICTION_STATUS_COLORS.over).toMatch(/^#[0-9a-f]{6}$/i);
    expect(PREDICTION_STATUS_COLORS.under).toMatch(/^#[0-9a-f]{6}$/i);
    expect(PREDICTION_STATUS_COLORS["on-target"]).toBe("");
  });
});

// ---------------------------------------------------------------------------
// predictionStatuses
// ---------------------------------------------------------------------------

describe("predictionStatuses", () => {
  it("classifies exactly at the 0.25 boundary as on-target (uses <=, not <)", () => {
    // maxActualCount = 10 -> log1p(10) is the denominator.
    // Pick an actual count whose anorm we can compute, then pick a
    // predicted score whose pnorm is exactly anorm + PREDICTION_THRESHOLD.
    const maxActual = 10;
    const actualCount = 3;
    const anorm = Math.log1p(actualCount) / Math.log1p(maxActual);
    const pnorm = anorm + PREDICTION_THRESHOLD;

    // predicted.max is 1, so predictedScore === pnorm directly.
    const predicted = makePredicted({ target: pnorm, other: 1 });
    const actual: Record<string, number> = {
      target: actualCount,
      hottest: maxActual,
    };

    const statuses = predictionStatuses(predicted, actual);
    expect(statuses.get("target")).toBe("on-target");
  });

  it("classifies an under-detected surprise hotspot: zero/absent prediction, high actual count", () => {
    const predicted = makePredicted({ known: 0.8 });
    const actual: Record<string, number> = {
      known: 1,
      surprise: 50, // absent from predicted.scores, high actual hits
    };

    const statuses = predictionStatuses(predicted, actual);
    expect(statuses.get("surprise")).toBe("under");
  });

  it("classifies an over-predicted node: high predicted score, zero actual count", () => {
    const predicted = makePredicted({ hot_guess: 1.0, other: 0.1 });
    const actual: Record<string, number> = {
      other: 5, // gives maxActualCount > 0 so anorm is meaningful
      // hot_guess absent from actual -> actual count 0
    };

    const statuses = predictionStatuses(predicted, actual);
    expect(statuses.get("hot_guess")).toBe("over");
  });

  it("maxActualCount <= 0: predicted-hot nodes are over, low-predicted nodes are on-target, never under", () => {
    const predicted = makePredicted({ a: 1.0, b: 0.1 });
    const actual: Record<string, number> = { a: 0, b: 0 };

    const statuses = predictionStatuses(predicted, actual);
    // a: pnorm = 1.0, anorm = 0, diff = 1.0 > 0.25 -> over
    expect(statuses.get("a")).toBe("over");
    // b: pnorm = 0.1, anorm = 0, diff = 0.1 <= 0.25 -> on-target
    expect(statuses.get("b")).toBe("on-target");
    expect([...statuses.values()]).not.toContain("under");
  });

  it("maxActualCount <= 0 also holds when actual is empty entirely", () => {
    const predicted = makePredicted({ a: 1.0, b: 0.05 });
    const statuses = predictionStatuses(predicted, {});
    expect(statuses.get("a")).toBe("over");
    expect(statuses.get("b")).toBe("on-target");
  });

  it("universe includes all predicted node_ids plus all actual node_ids with count > 0", () => {
    const predicted = makePredicted({ p1: 0.5 });
    const actual: Record<string, number> = { a1: 3, a2: 0 };
    const statuses = predictionStatuses(predicted, actual);
    const ids = new Set(statuses.keys());
    expect(ids.has("p1")).toBe(true);
    expect(ids.has("a1")).toBe(true);
    // a2 has count 0 and is absent from predicted -> excluded from universe.
    expect(ids.has("a2")).toBe(false);
  });
});
