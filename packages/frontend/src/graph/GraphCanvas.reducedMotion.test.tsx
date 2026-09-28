/**
 * Phase 10.7 watch-mode animation under `prefers-reduced-motion` — the
 * direction the suppression exists for (campaign T11-7,
 * `docs/test-campaigns/phase-12.md`).
 *
 * `graphAnimation.test.ts` pins the pure half (`recordDiffAnimations` returns
 * false and records nothing under reduced motion). What had never run is the
 * caller's half in `GraphCanvas`: with nothing recorded, the canvas must drop
 * removed nodes in the same commit and start no animation loop, and new nodes
 * and edges must paint at their final size and colour. Before T11-7 the setup
 * stub answered `matches: false` to every query, so no test could get here.
 *
 * Deliberately narrow: this file mounts `GraphCanvas` against a minimal fake
 * Sigma/FA2 pair only to reach the reduced-motion branch. The full lifecycle
 * harness is T11-1's (`GraphCanvas.test.tsx`, a separate workstream). Every
 * reduced-motion assertion has a default-motion control beside it that
 * asserts the opposite, so each pair discriminates.
 */
import type { Graph } from "@grackle/shared-types";
import { act, cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  REDUCED_MOTION_QUERY,
  setMatchingMediaQueries,
} from "../test/matchMedia";
import { restoreInitialState } from "../test/storeReset";
import type { GrackleMultiGraph } from "./buildGraphology";
import { GraphCanvas } from "./GraphCanvas";
import { ENTER_FLASH_COLOR, EXIT_DURATION_MS } from "./graphAnimation";
import { useGraphStore } from "./useGraphStore";

type Reducer = (key: string, data: unknown) => Record<string, unknown>;

interface FakeSigma {
  graph: GrackleMultiGraph;
  settings: Record<string, unknown>;
}

const fakes = vi.hoisted(() => ({ sigmas: [] as unknown[] }));

vi.mock("sigma", () => ({
  default: class {
    graph: unknown;
    settings: Record<string, unknown>;
    constructor(
      graph: unknown,
      _container: unknown,
      settings: Record<string, unknown>
    ) {
      this.graph = graph;
      this.settings = { ...settings };
      fakes.sigmas.push(this);
    }
    on() {}
    kill() {}
    refresh() {}
    setSetting(key: string, value: unknown) {
      this.settings[key] = value;
    }
  },
}));

vi.mock("graphology-layout-forceatlas2/worker", () => ({
  default: class {
    start() {}
    stop() {}
    kill() {}
  },
}));

const INITIAL_SETTLE_MS = 5000;
const DEFAULT_EDGE_COLOR = "#94a3b8";
const BASE_NODE_SIZE = 6;

// a → b, plus c. The re-push drops c and adds d with a new edge d → a, so one
// apply exercises removal, node entry and edge entry together.
const BEFORE: Graph = {
  version: 1,
  language: "python",
  nodes: [
    { id: "m.py:a", kind: "function", name: "a", path: "m.py" },
    { id: "m.py:b", kind: "function", name: "b", path: "m.py" },
    { id: "m.py:c", kind: "function", name: "c", path: "m.py" },
  ],
  edges: [{ source: "m.py:a", target: "m.py:b", kind: "call" }],
};

const AFTER: Graph = {
  version: 1,
  language: "python",
  nodes: [
    { id: "m.py:a", kind: "function", name: "a", path: "m.py" },
    { id: "m.py:b", kind: "function", name: "b", path: "m.py" },
    { id: "m.py:d", kind: "function", name: "d", path: "m.py" },
  ],
  edges: [
    { source: "m.py:a", target: "m.py:b", kind: "call" },
    { source: "m.py:d", target: "m.py:a", kind: "call" },
  ],
};

function sigma(): FakeSigma {
  expect(fakes.sigmas).toHaveLength(1); // one scratch build, then applies
  return fakes.sigmas[0] as FakeSigma;
}

function nodeReducer(): Reducer {
  return sigma().settings.nodeReducer as Reducer;
}

function edgeReducer(): Reducer {
  return sigma().settings.edgeReducer as Reducer;
}

function paintNode(id: string): Record<string, unknown> {
  const live = sigma().graph;
  return nodeReducer()(id, live.getNodeAttributes(id));
}

function paintEdge(source: string, target: string): Record<string, unknown> {
  const live = sigma().graph;
  const [key] = live.edges(source, target);
  expect(key).toBeDefined();
  return edgeReducer()(key as string, live.getEdgeAttributes(key as string));
}

/** Mount, load BEFORE, let the initial FA2 settle finish, then re-push AFTER. */
function mountAndRepush(): { rafCalls: () => number } {
  render(<GraphCanvas />);
  act(() => {
    useGraphStore.getState().setGraph(BEFORE);
  });
  act(() => {
    vi.advanceTimersByTime(INITIAL_SETTLE_MS);
  });
  const raf = vi.spyOn(globalThis, "requestAnimationFrame");
  act(() => {
    useGraphStore.getState().setGraph(AFTER);
  });
  return { rafCalls: () => raf.mock.calls.length };
}

beforeEach(() => {
  vi.useFakeTimers();
  fakes.sigmas.length = 0;
  restoreInitialState(useGraphStore);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("GraphCanvas watch-mode re-push — default motion (control)", () => {
  it("keeps a removed node as a fading ghost and starts the animation loop", () => {
    const { rafCalls } = mountAndRepush();
    const live = sigma().graph;

    expect(live.hasNode("m.py:c")).toBe(true);
    expect(rafCalls()).toBe(1);

    // The ghost is dropped once the fade has run its course.
    act(() => {
      vi.advanceTimersByTime(EXIT_DURATION_MS + 50);
    });
    expect(live.hasNode("m.py:c")).toBe(false);
  });

  it("pops a new node in from zero size, and flashes a new edge", () => {
    mountAndRepush();

    expect(paintNode("m.py:d").size).toBe(0);
    expect(paintEdge("m.py:d", "m.py:a").color).toBe(ENTER_FLASH_COLOR);
  });
});

describe("GraphCanvas watch-mode re-push — prefers-reduced-motion", () => {
  beforeEach(() => {
    setMatchingMediaQueries(REDUCED_MOTION_QUERY);
  });

  it("drops a removed node in the same commit and starts no animation loop", () => {
    const { rafCalls } = mountAndRepush();
    const live = sigma().graph;

    expect(live.hasNode("m.py:c")).toBe(false);
    expect(rafCalls()).toBe(0);
    // Survivors and the new node are all there.
    expect(live.nodes().sort()).toEqual(["m.py:a", "m.py:b", "m.py:d"]);
  });

  it("paints a new node at its final size and colour immediately", () => {
    mountAndRepush();

    const d = paintNode("m.py:d");
    // GraphCanvas's BASE_SIZE: d has in-degree 0, so no log boost.
    expect(d.size).toBe(BASE_NODE_SIZE);
    // The plain kind colour a surviving function node gets — no enter flash.
    expect(d.color).toBe(paintNode("m.py:b").color);
    expect(d.color).not.toBe(ENTER_FLASH_COLOR);

    // And it stays there: nothing is animating toward a different end state.
    act(() => {
      vi.advanceTimersByTime(1000);
    });
    expect(paintNode("m.py:d")).toMatchObject({
      size: BASE_NODE_SIZE,
      color: d.color,
    });
  });

  it("paints a new edge at its base colour with no pulse", () => {
    mountAndRepush();

    const e = paintEdge("m.py:d", "m.py:a");
    expect(e.color).toBe(DEFAULT_EDGE_COLOR);
    expect(e).not.toHaveProperty("size");
  });
});
