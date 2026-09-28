/**
 * GraphCanvas lifecycle harness — campaign T11-1
 * (`docs/test-campaigns/phase-12.md`, tier T11).
 *
 * GraphCanvas is the sole owner of the Sigma / ForceAtlas2 lifecycle and had
 * no tests at all. Its collaborators (buildGraphology, applyGraphDiff,
 * graphAnimation, the store) are each tested alone; this suite tests their
 * COMPOSITION and every cleanup path:
 *
 *  - mount builds exactly one Sigma + one FA2 layout with the documented
 *    settings; unmount kills both exactly once and leaves no timer or rAF
 *    callback pending (fake timers count both);
 *  - the `[graph]` effect's rebuild-vs-apply decision table (the table is
 *    spelled out above that describe block), including the bounded-reheat
 *    sub-table: the initial-settle guard, pin/unpin, and timer supersession;
 *  - the three Sigma handlers drive the store, and die with the instance that
 *    registered them;
 *  - theme and filter changes repaint (setSetting + refresh) without a
 *    rebuild;
 *  - StrictMode's double-invoked effects and rapid remounts leave exactly one
 *    live Sigma and nothing pending.
 *
 * `sigma` and the FA2 worker supervisor are replaced by recording fakes (jsdom
 * has neither WebGL nor Web Workers). graphology is REAL: hasSurvivor and
 * applyGraphDiff operate on it, and position preservation is asserted on it.
 * The fakes model the two library behaviors the component relies on:
 *  - `Sigma.kill()` drops every listener (sigma 3.x calls
 *    `removeAllListeners()`), which is the component's ONLY handler teardown;
 *  - FA2's `start()` is a no-op while already running, and is the only place
 *    the supervisor reads the `fixed` pins (graphToByteArrays). Each real
 *    start is therefore snapshotted with the pins it saw (`matrixBuilds`).
 * Any call on an instance after its `kill()` is recorded in `usedAfterKill`
 * (a timer or rAF callback that outlived its owner).
 */
import type { Graph, GraphEdge, GraphNode } from "@grackle/shared-types";
import { act, cleanup, render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import type { EdgeDisplayData, NodeDisplayData } from "sigma/types";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  REDUCED_MOTION_QUERY,
  setMatchingMediaQueries,
} from "../test/matchMedia";
import { restoreInitialState } from "../test/storeReset";
import { useTheme } from "../theme/useTheme";
import type {
  EdgeAttributes,
  GrackleMultiGraph,
  NodeAttributes,
} from "./buildGraphology";
import { GraphCanvas } from "./GraphCanvas";
import { ENTER_DURATION_MS, EXIT_DURATION_MS } from "./graphAnimation";
import { useGraphStore } from "./useGraphStore";

const h = vi.hoisted(() => {
  type Payload = { node?: string; edge?: string };
  type Listener = (payload: Payload) => void;

  const registry = {
    sigmas: [] as FakeSigma[],
    layouts: [] as FakeLayout[],
  };

  class FakeSigma {
    readonly graph: GrackleMultiGraph;
    readonly container: HTMLElement;
    readonly constructorSettings: Record<string, unknown>;
    /** Live settings: constructor settings, then every setSetting applied. */
    readonly settings: Record<string, unknown>;
    readonly listeners = new Map<string, Listener[]>();
    /** Every `on()` registration, in order. */
    readonly registrations: string[] = [];
    readonly setSettingCalls: [string, unknown][] = [];
    readonly usedAfterKill: string[] = [];
    refreshCount = 0;
    killCount = 0;

    constructor(
      graph: GrackleMultiGraph,
      container: HTMLElement,
      settings: Record<string, unknown>
    ) {
      this.graph = graph;
      this.container = container;
      this.constructorSettings = settings;
      this.settings = { ...settings };
      registry.sigmas.push(this);
    }

    on(event: string, fn: Listener): this {
      if (this.killCount > 0) this.usedAfterKill.push(`on:${event}`);
      this.registrations.push(event);
      this.listeners.set(event, [...(this.listeners.get(event) ?? []), fn]);
      return this;
    }

    /** Test-side: deliver a Sigma event to whatever is still listening. */
    emit(event: string, payload: Payload = {}): void {
      for (const fn of this.listeners.get(event) ?? []) fn(payload);
    }

    kill(): void {
      this.killCount += 1;
      this.listeners.clear();
    }

    refresh(): this {
      if (this.killCount > 0) this.usedAfterKill.push("refresh");
      this.refreshCount += 1;
      return this;
    }

    setSetting(key: string, value: unknown): this {
      if (this.killCount > 0) this.usedAfterKill.push(`setSetting:${key}`);
      this.settings[key] = value;
      this.setSettingCalls.push([key, value]);
      return this;
    }
  }

  class FakeLayout {
    readonly graph: GrackleMultiGraph;
    readonly params: unknown;
    /** start / stop / kill, in call order. */
    readonly calls: string[] = [];
    /** The `fixed` pins each real (non-no-op) start() read, by node id. */
    readonly matrixBuilds: Record<string, boolean>[] = [];
    readonly usedAfterKill: string[] = [];
    running = false;
    killCount = 0;

    constructor(graph: GrackleMultiGraph, params: unknown) {
      this.graph = graph;
      this.params = params;
      registry.layouts.push(this);
    }

    start(): this {
      if (this.killCount > 0) this.usedAfterKill.push("start");
      this.calls.push("start");
      if (this.running) return this; // the real supervisor no-ops here
      const pins: Record<string, boolean> = {};
      this.graph.forEachNode((id, attrs) => {
        pins[id] = attrs.fixed === true;
      });
      this.matrixBuilds.push(pins);
      this.running = true;
      return this;
    }

    stop(): this {
      if (this.killCount > 0) this.usedAfterKill.push("stop");
      this.calls.push("stop");
      this.running = false;
      return this;
    }

    kill(): this {
      this.killCount += 1;
      this.calls.push("kill");
      this.running = false;
      return this;
    }

    isRunning(): boolean {
      return this.running;
    }
  }

  return { registry, FakeSigma, FakeLayout };
});

vi.mock("sigma", () => ({ default: h.FakeSigma }));
vi.mock("graphology-layout-forceatlas2/worker", () => ({
  default: h.FakeLayout,
}));

type FakeSigma = InstanceType<typeof h.FakeSigma>;
type FakeLayout = InstanceType<typeof h.FakeLayout>;
type NodeReducer = (
  node: string,
  data: NodeAttributes
) => Partial<NodeDisplayData>;
type EdgeReducer = (
  edge: string,
  data: EdgeAttributes
) => Partial<EdgeDisplayData>;

// Mirrors of GraphCanvas's private constants (not exported): the one-time
// full-graph settle after a scratch build, and the bounded per-re-push reheat.
const INITIAL_SETTLE_MS = 5000;
const REHEAT_MS = 1500;
const LABEL_DARK = "#ffffff";
const LABEL_LIGHT = "#0f172a";
const DIMMED = "#cbd5e1";

// ---------------------------------------------------------------------------
// Fixtures
// ---------------------------------------------------------------------------

const FILE_A = "pkg/a.py";
const RUN = "pkg/a.py:run";
const HELPER = "pkg/b.py:helper";
/** An empty package file: no imports, imported by nothing — isolated. */
const INIT = "pkg/__init__.py";
const EXTRA = "pkg/b.py:extra";
const MORE = "pkg/b.py:more";

const n = {
  fileA: { id: FILE_A, kind: "file", name: "a.py", path: "pkg/a.py" },
  run: { id: RUN, kind: "function", name: "run", path: "pkg/a.py", line: 3 },
  helper: {
    id: HELPER,
    kind: "function",
    name: "helper",
    path: "pkg/b.py",
    line: 1,
  },
  init: {
    id: INIT,
    kind: "file",
    name: "__init__.py",
    path: "pkg/__init__.py",
  },
  extra: {
    id: EXTRA,
    kind: "function",
    name: "extra",
    path: "pkg/b.py",
    line: 9,
  },
  more: {
    id: MORE,
    kind: "function",
    name: "more",
    path: "pkg/b.py",
    line: 12,
  },
} satisfies Record<string, GraphNode>;

const e = {
  // Source and target live in DIFFERENT files, so a jump to the wrong
  // endpoint's path is observable.
  runToHelper: {
    source: RUN,
    target: HELPER,
    kind: "call",
    metadata: { line: 7 },
  },
  // No evidence line (like a stale-cache cross-language edge).
  fileToHelper: { source: FILE_A, target: HELPER, kind: "import" },
  helperToExtra: {
    source: HELPER,
    target: EXTRA,
    kind: "call",
    metadata: { line: 4 },
  },
  extraToMore: {
    source: EXTRA,
    target: MORE,
    kind: "call",
    metadata: { line: 10 },
  },
  helperToRun: {
    source: HELPER,
    target: RUN,
    kind: "call",
    metadata: { line: 2 },
  },
} satisfies Record<string, GraphEdge>;

function graphOf(nodes: GraphNode[], edges: GraphEdge[]): Graph {
  return { version: 1, language: "python", nodes, edges };
}

const BASE = graphOf(
  [n.fileA, n.run, n.helper, n.init],
  [e.runToHelper, e.fileToHelper]
);
const BASE_IDS = [FILE_A, RUN, HELPER, INIT];
/** BASE + a new node wired to a survivor. */
const WITH_EXTRA = graphOf(
  [...BASE.nodes, n.extra],
  [...BASE.edges, e.helperToExtra]
);
/** WITH_EXTRA + one more new node. */
const WITH_EXTRA_MORE = graphOf(
  [...WITH_EXTRA.nodes, n.more],
  [...WITH_EXTRA.edges, e.extraToMore]
);
/** BASE minus the isolated node: a node removal and nothing else. */
const WITHOUT_INIT = graphOf([n.fileA, n.run, n.helper], BASE.edges);
/** BASE minus HELPER — which also takes both of BASE's edges with it. */
const WITHOUT_HELPER = graphOf([n.fileA, n.run, n.init], []);
/** BASE with RUN moved to another file: an attribute-only change. */
const RUN_MOVED = graphOf(
  [n.fileA, { ...n.run, path: "pkg/a_moved.py" }, n.helper, n.init],
  BASE.edges
);
const EDGE_ADDED = graphOf(BASE.nodes, [...BASE.edges, e.helperToRun]);
const EDGE_REMOVED = graphOf(BASE.nodes, [e.runToHelper]);
/** A different project: no node id in common with BASE. */
const OTHER = graphOf(
  [
    { id: "svc/x.py:x", kind: "function", name: "x", path: "svc/x.py" },
    { id: "svc/y.py:y", kind: "function", name: "y", path: "svc/y.py" },
  ],
  [
    {
      source: "svc/x.py:x",
      target: "svc/y.py:y",
      kind: "call",
      metadata: { line: 2 },
    },
  ]
);
const EMPTY = graphOf([], []);

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function pushGraph(graph: Graph): void {
  act(() => {
    useGraphStore.getState().setGraph(graph);
  });
}

function advance(ms: number): void {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

function canvas({ strict = false } = {}) {
  return strict ? (
    <StrictMode>
      <GraphCanvas />
    </StrictMode>
  ) : (
    <GraphCanvas />
  );
}

/** Put `graph` in the store, then mount — the first-load path. */
function mount(graph: Graph | null = BASE, { strict = false } = {}) {
  if (graph !== null) pushGraph(graph);
  return render(canvas({ strict }));
}

const liveSigmas = () => h.registry.sigmas.filter((s) => s.killCount === 0);
const liveLayouts = () => h.registry.layouts.filter((l) => l.killCount === 0);

function theSigma(): FakeSigma {
  const live = liveSigmas();
  expect(live).toHaveLength(1);
  return live[0] as FakeSigma;
}

function theLayout(): FakeLayout {
  const live = liveLayouts();
  expect(live).toHaveLength(1);
  return live[0] as FakeLayout;
}

function positions(g: GrackleMultiGraph, ids: string[]) {
  return ids.map((id) => [
    id,
    g.getNodeAttribute(id, "x"),
    g.getNodeAttribute(id, "y"),
  ]);
}

function pinnedIds(g: GrackleMultiGraph): string[] {
  return g.filterNodes((_id, attrs) => attrs.fixed === true).sort();
}

function edgeBetween(
  g: GrackleMultiGraph,
  source: string,
  target: string
): string {
  // outEdges: `edges(a, b)` also matches a b -> a edge.
  const [key] = g.outEdges(source, target);
  if (key === undefined) throw new Error(`no edge ${source} -> ${target}`);
  return key;
}

function lastLabelColor(s: FakeSigma): unknown {
  return s.setSettingCalls.filter(([key]) => key === "labelColor").at(-1)?.[1];
}

/** Every instance ever built was killed exactly once, and never used after. */
function expectAllRetiredCleanly(): void {
  for (const s of h.registry.sigmas) {
    expect(s.killCount).toBe(1);
    expect(s.usedAfterKill).toEqual([]);
  }
  for (const l of h.registry.layouts) {
    expect(l.killCount).toBe(1);
    expect(l.usedAfterKill).toEqual([]);
  }
}

beforeEach(() => {
  h.registry.sigmas.length = 0;
  h.registry.layouts.length = 0;
  restoreInitialState(useGraphStore);
  restoreInitialState(useTheme);
  useTheme.setState({ theme: "dark" });
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// Mount
// ---------------------------------------------------------------------------

describe("GraphCanvas — mount", () => {
  it("builds nothing until a graph arrives, then builds exactly one pair", () => {
    mount(null);
    expect(screen.getByRole("img", { name: "Code graph" })).toBeInTheDocument();
    expect(h.registry.sigmas).toHaveLength(0);
    expect(h.registry.layouts).toHaveLength(0);
    expect(vi.getTimerCount()).toBe(0);

    pushGraph(BASE);
    expect(h.registry.sigmas).toHaveLength(1);
    expect(h.registry.layouts).toHaveLength(1);
    expect(theSigma().graph.nodes().sort()).toEqual([...BASE_IDS].sort());
  });

  it("builds one Sigma on the canvas container with the documented settings", () => {
    mount();
    const sigma = theSigma();

    expect(sigma.container).toBe(
      screen.getByRole("img", { name: "Code graph" })
    );
    expect(sigma.graph.nodes().sort()).toEqual([...BASE_IDS].sort());
    expect(sigma.graph.size).toBe(BASE.edges.length);
    expect(sigma.constructorSettings.allowInvalidContainer).toBe(true);
    expect(sigma.constructorSettings.labelColor).toEqual({ color: LABEL_DARK });
    expect(typeof sigma.constructorSettings.nodeReducer).toBe("function");
    expect(typeof sigma.constructorSettings.edgeReducer).toBe("function");
    // Exactly the three handlers, each registered once.
    expect(sigma.registrations).toEqual([
      "clickNode",
      "clickEdge",
      "clickStage",
    ]);
  });

  it("builds one FA2 layout on the same graph, started, with the settle timer pending", () => {
    mount();
    const layout = theLayout();

    expect(layout.graph).toBe(theSigma().graph);
    expect(layout.params).toEqual({
      settings: { barnesHutOptimize: true, gravity: 1, slowDown: 10 },
    });
    expect(layout.calls).toEqual(["start"]);
    expect(layout.running).toBe(true);
    expect(vi.getTimerCount()).toBe(1);
  });

  it("takes the label color from the theme at build time", () => {
    useTheme.setState({ theme: "light" });
    mount();
    expect(theSigma().constructorSettings.labelColor).toEqual({
      color: LABEL_LIGHT,
    });
  });

  it("stops the initial layout at 5 s, not before, and leaves nothing pending", () => {
    mount();
    const layout = theLayout();

    advance(INITIAL_SETTLE_MS - 1);
    expect(layout.calls).toEqual(["start"]);

    advance(1);
    expect(layout.calls).toEqual(["start", "stop"]);
    expect(layout.running).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// Unmount teardown
// ---------------------------------------------------------------------------

describe("GraphCanvas — unmount teardown", () => {
  it("kills the Sigma and the layout exactly once and leaves nothing pending", () => {
    const { unmount } = mount();
    expect(vi.getTimerCount()).toBe(1); // the initial settle

    unmount();
    expect(liveSigmas()).toHaveLength(0);
    expect(liveLayouts()).toHaveLength(0);
    expect(vi.getTimerCount()).toBe(0);

    advance(60_000);
    expectAllRetiredCleanly();
  });

  it("no handler can fire after unmount", () => {
    const { unmount } = mount();
    const sigma = theSigma();
    act(() => {
      useGraphStore.getState().selectNode(FILE_A);
      useGraphStore.getState().setHighlightedNodes([FILE_A, RUN]);
    });

    unmount();
    sigma.emit("clickNode", { node: RUN });
    sigma.emit("clickEdge", {
      edge: edgeBetween(sigma.graph, RUN, HELPER),
    });
    sigma.emit("clickStage");

    const state = useGraphStore.getState();
    expect(state.selectedNodeId).toBe(FILE_A);
    expect(state.selectedEdge).toBeNull();
    expect(state.sourceViewerTarget).toBeNull();
    expect([...(state.highlightedNodeIds ?? [])].sort()).toEqual(
      [FILE_A, RUN].sort()
    );
  });

  it("cancels an in-flight reheat timer", () => {
    const { unmount } = mount();
    advance(INITIAL_SETTLE_MS);
    pushGraph(WITH_EXTRA); // pins survivors + arms the 1.5 s reheat timer
    expect(pinnedIds(theSigma().graph)).toEqual([...BASE_IDS].sort());

    unmount();
    expect(vi.getTimerCount()).toBe(0);

    advance(REHEAT_MS * 2);
    expectAllRetiredCleanly();
  });

  it("cancels an in-flight animation frame", () => {
    const { unmount } = mount();
    advance(INITIAL_SETTLE_MS);
    pushGraph(WITHOUT_INIT); // INIT starts fading over the rAF loop
    const sigma = theSigma();
    advance(100);
    const refreshes = sigma.refreshCount;

    unmount();
    expect(vi.getTimerCount()).toBe(0);

    advance(EXIT_DURATION_MS * 2);
    expect(sigma.refreshCount).toBe(refreshes);
    // The tick that would drop the faded ghost never ran.
    expect(sigma.graph.hasNode(INIT)).toBe(true);
    expectAllRetiredCleanly();
  });

  it("is a no-op when no graph ever arrived", () => {
    const { unmount } = mount(null);
    unmount();
    expect(h.registry.sigmas).toHaveLength(0);
    expect(vi.getTimerCount()).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// The [graph] effect: rebuild vs apply
//
//  live Sigma | incoming shares a live node id? | diff               | outcome
//  -----------+---------------------------------+--------------------+-----------------------------
//  no         | -                               | -                  | scratch build (see "mount")
//  yes        | no  (a different project)       | -                  | scratch rebuild: kill + new pair
//  yes        | no  (an empty incoming graph)   | -                  | scratch rebuild
//  yes        | yes                             | empty              | nothing at all
//  yes        | yes                             | attributes only    | merged in place; no loop, no reheat
//  yes        | yes                             | + edges only       | pulse via rAF; no reheat
//  yes        | yes                             | - edges only       | no rAF, no reheat
//  yes        | yes                             | + nodes            | rAF + reheat
//  yes        | yes                             | - nodes            | fade via rAF, then drop; reheat
//  yes        | yes                             | - nodes, reduced   | drop now, no rAF; reheat
//
//  reheat, when a node was added or removed:
//  initial settle still pending -> no-op (FA2 is still settling everything)
//  settled                      -> pin survivors, stop()+start(), unpin + stop after 1.5 s
//  a reheat already pending     -> supersede: one timer, pins re-read, 1.5 s from the latest
// ---------------------------------------------------------------------------

describe("GraphCanvas — rebuild vs apply", () => {
  it("rebuilds on a disjoint re-push: old pair killed, fresh pair on a fresh graph", () => {
    mount();
    const oldSigma = theSigma();
    const oldLayout = theLayout();

    pushGraph(OTHER);

    expect(oldSigma.killCount).toBe(1);
    expect(oldLayout.killCount).toBe(1);
    const sigma = theSigma();
    const layout = theLayout();
    expect(sigma).not.toBe(oldSigma);
    expect(sigma.graph).not.toBe(oldSigma.graph);
    expect(sigma.graph.nodes().sort()).toEqual(["svc/x.py:x", "svc/y.py:y"]);
    expect(sigma.registrations).toEqual([
      "clickNode",
      "clickEdge",
      "clickStage",
    ]);
    expect(layout.graph).toBe(sigma.graph);
    expect(layout.calls).toEqual(["start"]);
  });

  it("a rebuild restarts the 5 s settle; the superseded timer cannot stop the new layout", () => {
    mount();
    advance(3000);
    pushGraph(OTHER); // t = 3000
    const layout = theLayout();
    expect(vi.getTimerCount()).toBe(1);

    advance(2000); // t = 5000: the FIRST build's deadline
    expect(layout.calls).toEqual(["start"]);

    advance(3000); // t = 8000: the rebuild's own deadline
    expect(layout.calls).toEqual(["start", "stop"]);
    expectAllRetiredCleanlyExceptLive();
  });

  it("a rebuild cancels an in-flight reheat and animation of the old graph", () => {
    mount();
    advance(INITIAL_SETTLE_MS);
    pushGraph(WITH_EXTRA); // reheat timer + rAF pending on the old pair
    expect(vi.getTimerCount()).toBe(2);

    pushGraph(OTHER);
    expect(vi.getTimerCount()).toBe(1); // only the new initial settle
    const layout = theLayout();

    advance(REHEAT_MS);
    expect(layout.calls).toEqual(["start"]);
    expectAllRetiredCleanlyExceptLive();
  });

  it("an empty incoming graph has no survivors, so it rebuilds — and so does the next one", () => {
    mount();
    pushGraph(EMPTY);
    expect(h.registry.sigmas).toHaveLength(2);
    expect(theSigma().graph.order).toBe(0);

    pushGraph(BASE);
    expect(h.registry.sigmas).toHaveLength(3);
    expect(theSigma().graph.nodes().sort()).toEqual([...BASE_IDS].sort());
  });

  it("an identical re-push changes nothing at all", () => {
    mount();
    advance(INITIAL_SETTLE_MS);
    const sigma = theSigma();
    const layout = theLayout();
    const before = positions(sigma.graph, BASE_IDS);
    const refreshes = sigma.refreshCount;
    const calls = [...layout.calls];

    pushGraph(structuredClone(BASE));

    expect(theSigma()).toBe(sigma);
    expect(h.registry.sigmas).toHaveLength(1);
    expect(sigma.refreshCount).toBe(refreshes);
    expect(layout.calls).toEqual(calls);
    expect(vi.getTimerCount()).toBe(0);
    expect(positions(sigma.graph, BASE_IDS)).toEqual(before);
  });

  it("an attribute-only re-push is merged into the live graph with no loop and no reheat", () => {
    mount();
    advance(INITIAL_SETTLE_MS);
    const sigma = theSigma();
    const calls = [...theLayout().calls];

    pushGraph(RUN_MOVED);

    expect(theSigma()).toBe(sigma);
    expect(sigma.graph.getNodeAttribute(RUN, "path")).toBe("pkg/a_moved.py");
    expect(theLayout().calls).toEqual(calls);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("an added node applies in place: survivors keep positions and are pinned for the reheat", () => {
    mount();
    advance(INITIAL_SETTLE_MS);
    const sigma = theSigma();
    const layout = theLayout();
    const before = positions(sigma.graph, BASE_IDS);

    pushGraph(WITH_EXTRA);

    expect(h.registry.sigmas).toHaveLength(1);
    expect(h.registry.layouts).toHaveLength(1);
    expect(theSigma().graph).toBe(sigma.graph);
    expect(sigma.graph.hasNode(EXTRA)).toBe(true);
    expect(positions(sigma.graph, BASE_IDS)).toEqual(before);
    // stop()+start() rebuilt the layout's matrix with survivors pinned and
    // the newcomer free.
    expect(layout.calls).toEqual(["start", "stop", "stop", "start"]);
    expect(layout.matrixBuilds.at(-1)).toEqual({
      [FILE_A]: true,
      [RUN]: true,
      [HELPER]: true,
      [INIT]: true,
      [EXTRA]: false,
    });

    advance(REHEAT_MS - 1);
    expect(layout.running).toBe(true);
    expect(pinnedIds(sigma.graph)).toEqual([...BASE_IDS].sort());

    advance(1);
    expect(layout.calls.at(-1)).toBe("stop");
    expect(pinnedIds(sigma.graph)).toEqual([]);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("a re-push during the initial settle neither pins nor restarts the layout", () => {
    mount();
    advance(1000);
    const sigma = theSigma();
    const layout = theLayout();

    pushGraph(WITH_EXTRA);

    expect(sigma.graph.hasNode(EXTRA)).toBe(true);
    expect(layout.calls).toEqual(["start"]);
    expect(layout.matrixBuilds).toHaveLength(1);
    expect(pinnedIds(sigma.graph)).toEqual([]);
    expect(vi.getTimerCount()).toBe(2); // initial settle + the pop-in rAF

    advance(INITIAL_SETTLE_MS - 1000);
    expect(layout.calls).toEqual(["start", "stop"]);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("back-to-back reheats supersede: one timer, pins re-read, window restarts", () => {
    mount();
    advance(INITIAL_SETTLE_MS);
    const sigma = theSigma();
    const layout = theLayout();

    pushGraph(WITH_EXTRA); // t = 5000
    advance(1000);
    pushGraph(WITH_EXTRA_MORE); // t = 6000, first reheat still running

    // The stop() before start() is what makes the new pins take effect: on a
    // running layout a bare start() is a no-op and reads nothing.
    expect(layout.matrixBuilds).toHaveLength(3);
    expect(layout.matrixBuilds.at(-1)).toEqual({
      [FILE_A]: true,
      [RUN]: true,
      [HELPER]: true,
      [INIT]: true,
      [EXTRA]: true,
      [MORE]: false,
    });

    advance(500); // t = 6500: the FIRST reheat's deadline
    expect(layout.running).toBe(true);
    expect(pinnedIds(sigma.graph)).toContain(EXTRA);

    advance(ENTER_DURATION_MS - 500 + 100); // pop-in over; only the reheat left
    expect(vi.getTimerCount()).toBe(1);

    advance(REHEAT_MS); // well past the second deadline (t = 7500)
    expect(layout.running).toBe(false);
    expect(pinnedIds(sigma.graph)).toEqual([]);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("an edge-only addition pulses over the rAF loop without a reheat", () => {
    mount();
    advance(INITIAL_SETTLE_MS);
    const sigma = theSigma();
    const calls = [...theLayout().calls];

    pushGraph(EDGE_ADDED);
    const added = edgeBetween(sigma.graph, HELPER, RUN);
    expect(vi.getTimerCount()).toBe(1); // the rAF, no reheat timer
    expect(theLayout().calls).toEqual(calls);
    expect(pinnedIds(sigma.graph)).toEqual([]);

    const refreshes = sigma.refreshCount;
    advance(ENTER_DURATION_MS / 2);
    // Mid-pulse: the edge reducer shares the loop's animation state.
    const edgeReducer = sigma.settings.edgeReducer as EdgeReducer;
    const mid = edgeReducer(added, sigma.graph.getEdgeAttributes(added));
    expect(mid.size).toBeGreaterThan(2.5);
    expect(sigma.refreshCount).toBeGreaterThan(refreshes);

    advance(ENTER_DURATION_MS);
    expect(vi.getTimerCount()).toBe(0); // the loop stops once settled
    expect(
      edgeReducer(added, sigma.graph.getEdgeAttributes(added)).size
    ).toBeUndefined();
  });

  it("an edge-only removal needs neither a loop nor a reheat", () => {
    mount();
    advance(INITIAL_SETTLE_MS);
    const sigma = theSigma();
    const calls = [...theLayout().calls];

    pushGraph(EDGE_REMOVED);

    expect(sigma.graph.edges(FILE_A, HELPER)).toEqual([]);
    expect(theLayout().calls).toEqual(calls);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("a removed node fades over the rAF loop, is then dropped, and the loop stops", () => {
    mount();
    advance(INITIAL_SETTLE_MS);
    const sigma = theSigma();
    const layout = theLayout();

    pushGraph(WITHOUT_INIT);
    // Still present as a fading ghost, left unpinned by the reheat.
    expect(sigma.graph.hasNode(INIT)).toBe(true);
    expect(layout.matrixBuilds.at(-1)?.[INIT]).toBe(false);
    expect(layout.matrixBuilds.at(-1)?.[RUN]).toBe(true);

    advance(EXIT_DURATION_MS / 2);
    const nodeReducer = sigma.settings.nodeReducer as NodeReducer;
    const ghost = nodeReducer(INIT, sigma.graph.getNodeAttributes(INIT));
    expect(ghost.size).toBeLessThan(6);
    expect(sigma.graph.hasNode(INIT)).toBe(true);

    advance(EXIT_DURATION_MS / 2 + 32);
    expect(sigma.graph.hasNode(INIT)).toBe(false);
    expect(vi.getTimerCount()).toBe(1); // loop stopped; only the reheat left

    advance(REHEAT_MS);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("under reduced motion a removed node is dropped at once and no loop starts", () => {
    setMatchingMediaQueries(REDUCED_MOTION_QUERY);
    mount();
    advance(INITIAL_SETTLE_MS);
    const sigma = theSigma();

    pushGraph(WITHOUT_INIT);

    expect(sigma.graph.hasNode(INIT)).toBe(false);
    expect(vi.getTimerCount()).toBe(1); // the reheat timer only
    expect(theLayout().calls).toEqual(["start", "stop", "stop", "start"]);
  });

  it("a removed node restored along with its edges before its fade ends survives", () => {
    mount();
    advance(INITIAL_SETTLE_MS);
    const sigma = theSigma();

    pushGraph(WITHOUT_HELPER); // HELPER fades; both its edges go
    advance(100);
    pushGraph(BASE); // restored — its edges come back, so the diff is non-empty

    advance(EXIT_DURATION_MS * 2);
    expect(sigma.graph.hasNode(HELPER)).toBe(true);
    expect(sigma.graph.size).toBe(BASE.edges.length);
  });

  // KNOWN DEFECT — docs/test-campaigns/phase-12.md T11-1. A removed node that
  // is restored before its 400 ms fade ends is still a ghost in the live
  // graph, so the restoring re-push has a survivor, and applyGraphDiff finds
  // nothing structural to do: isEmptyDiff is true, and the effect returns
  // BEFORE recordDiffAnimations, the only thing that cancels a fade. The rAF
  // tick then drops the node when its stale fade completes: the store graph
  // has it, the canvas does not, until some later structural re-push adds it
  // back. Reached whenever the restoring re-push changes nothing else
  // structurally — no edge comes back with the node. That is the case for an
  // isolated node: an empty `__init__.py`, a module that imports nothing, an
  // uncalled function (after a transient file absence, or a syntax error
  // fixed within one fade). The companion test above shows the same restore
  // surviving when the node's edges come back with it.
  it.fails("a removed isolated node restored before its fade ends survives", () => {
    mount();
    advance(INITIAL_SETTLE_MS);
    const sigma = theSigma();

    pushGraph(WITHOUT_INIT); // INIT starts fading
    advance(100);
    pushGraph(BASE); // INIT is back in the project

    advance(EXIT_DURATION_MS * 2);
    expect(useGraphStore.getState().graph?.nodes.map((x) => x.id)).toContain(
      INIT
    );
    expect(sigma.graph.hasNode(INIT)).toBe(true);
  });
});

/** As expectAllRetiredCleanly, but for everything except the one live pair. */
function expectAllRetiredCleanlyExceptLive(): void {
  const liveS = theSigma();
  const liveL = theLayout();
  for (const s of h.registry.sigmas) {
    if (s === liveS) continue;
    expect(s.killCount).toBe(1);
    expect(s.usedAfterKill).toEqual([]);
  }
  for (const l of h.registry.layouts) {
    if (l === liveL) continue;
    expect(l.killCount).toBe(1);
    expect(l.usedAfterKill).toEqual([]);
  }
}

// ---------------------------------------------------------------------------
// Handlers
// ---------------------------------------------------------------------------

describe("GraphCanvas — Sigma handlers", () => {
  it("clickNode selects the node", () => {
    mount();
    act(() => theSigma().emit("clickNode", { node: RUN }));
    expect(useGraphStore.getState().selectedNodeId).toBe(RUN);
  });

  it("clickEdge selects the edge and jumps to its evidence line in the SOURCE's file", () => {
    mount();
    act(() => useGraphStore.getState().selectNode(FILE_A));
    const sigma = theSigma();

    act(() =>
      sigma.emit("clickEdge", { edge: edgeBetween(sigma.graph, RUN, HELPER) })
    );

    const state = useGraphStore.getState();
    expect(state.selectedEdge).toEqual({ source: RUN, target: HELPER });
    expect(state.selectedNodeId).toBeNull();
    expect(state.sourceViewerTarget).toEqual({ path: "pkg/a.py", line: 7 });
  });

  it("clickEdge on a line-less edge selects it and clears a stale jump target", () => {
    mount();
    act(() => useGraphStore.getState().jumpToSourceLine("pkg/old.py", 99));
    const sigma = theSigma();

    act(() =>
      sigma.emit("clickEdge", {
        edge: edgeBetween(sigma.graph, FILE_A, HELPER),
      })
    );

    const state = useGraphStore.getState();
    expect(state.selectedEdge).toEqual({ source: FILE_A, target: HELPER });
    expect(state.sourceViewerTarget).toBeNull();
  });

  it("clickEdge reads the LIVE graph, so it sees an in-place re-push's new path", () => {
    mount();
    const sigma = theSigma();
    pushGraph(RUN_MOVED); // same Sigma, RUN's file changed

    act(() =>
      sigma.emit("clickEdge", { edge: edgeBetween(sigma.graph, RUN, HELPER) })
    );

    expect(useGraphStore.getState().sourceViewerTarget).toEqual({
      path: "pkg/a_moved.py",
      line: 7,
    });
  });

  it("clickStage clears the selection and the highlight", () => {
    mount();
    act(() => {
      useGraphStore.getState().selectNode(RUN);
      useGraphStore.getState().setHighlightedNodes([RUN, HELPER]);
    });

    act(() => theSigma().emit("clickStage"));

    const state = useGraphStore.getState();
    expect(state.selectedNodeId).toBeNull();
    expect(state.highlightedNodeIds).toBeNull();
  });

  it("a rebuild retires the old instance's handlers; the new ones read the new graph", () => {
    mount();
    const oldSigma = theSigma();
    pushGraph(OTHER);
    const sigma = theSigma();

    oldSigma.emit("clickNode", { node: RUN });
    expect(useGraphStore.getState().selectedNodeId).toBeNull();

    act(() =>
      sigma.emit("clickEdge", {
        edge: edgeBetween(sigma.graph, "svc/x.py:x", "svc/y.py:y"),
      })
    );
    expect(useGraphStore.getState().sourceViewerTarget).toEqual({
      path: "svc/x.py",
      line: 2,
    });
  });
});

// ---------------------------------------------------------------------------
// Theme and filter repaints
// ---------------------------------------------------------------------------

describe("GraphCanvas — repaint without rebuild", () => {
  it("a theme change repaints the labels without rebuilding anything", () => {
    mount();
    advance(INITIAL_SETTLE_MS);
    const sigma = theSigma();
    const graph = sigma.graph;
    const calls = [...theLayout().calls];
    const refreshes = sigma.refreshCount;

    // Drive the theme slice directly: setTheme also persists to
    // localStorage, and jsdom's setItem queues a storage-event timer that
    // would pollute the pending-timer count below (the persistence is
    // useTheme's own concern, covered in useTheme.test.ts).
    act(() => useTheme.setState({ theme: "light" }));

    expect(lastLabelColor(sigma)).toEqual({ color: LABEL_LIGHT });
    expect(sigma.settings.labelColor).toEqual({ color: LABEL_LIGHT });
    expect(sigma.refreshCount).toBe(refreshes + 1);
    expect(h.registry.sigmas).toHaveLength(1);
    expect(h.registry.layouts).toHaveLength(1);
    expect(sigma.graph).toBe(graph);
    expect(theLayout().calls).toEqual(calls);
    expect(vi.getTimerCount()).toBe(0);

    act(() => useTheme.setState({ theme: "dark" }));
    expect(lastLabelColor(sigma)).toEqual({ color: LABEL_DARK });
    expect(sigma.refreshCount).toBe(refreshes + 2);
  });

  it("a selection change swaps in a node reducer that reflects it, without rebuilding", () => {
    mount();
    const sigma = theSigma();
    const reducerBefore = sigma.settings.nodeReducer;
    const refreshes = sigma.refreshCount;

    act(() => useGraphStore.getState().selectNode(RUN));

    const reducer = sigma.settings.nodeReducer as NodeReducer;
    expect(reducer).not.toBe(reducerBefore);
    expect(sigma.refreshCount).toBe(refreshes + 1);
    expect(h.registry.sigmas).toHaveLength(1);
    expect(reducer(HELPER, sigma.graph.getNodeAttributes(HELPER)).color).toBe(
      DIMMED
    );
    expect(reducer(RUN, sigma.graph.getNodeAttributes(RUN)).color).not.toBe(
      DIMMED
    );
  });
});

// ---------------------------------------------------------------------------
// StrictMode and remounts
// ---------------------------------------------------------------------------

describe("GraphCanvas — StrictMode and remounts", () => {
  it("StrictMode's double-invoked effects leave exactly one live pair", () => {
    const { unmount } = mount(BASE, { strict: true });

    const sigma = theSigma();
    const layout = theLayout();
    expect(layout.running).toBe(true);
    expect(vi.getTimerCount()).toBe(1);
    expectAllRetiredCleanlyExceptLive();

    // Only the survivor owns handlers.
    for (const s of h.registry.sigmas) {
      if (s !== sigma) s.emit("clickNode", { node: RUN });
    }
    expect(useGraphStore.getState().selectedNodeId).toBeNull();
    act(() => sigma.emit("clickNode", { node: HELPER }));
    expect(useGraphStore.getState().selectedNodeId).toBe(HELPER);

    advance(INITIAL_SETTLE_MS);
    expect(layout.calls).toEqual(["start", "stop"]);

    unmount();
    expect(vi.getTimerCount()).toBe(0);
    expectAllRetiredCleanly();
  });

  it("rapid mount/unmount cycles, interrupted mid-flight, never leak", () => {
    const interrupts: (() => void)[] = [
      () => {}, // initial settle pending
      () => {
        advance(INITIAL_SETTLE_MS);
        pushGraph(WITH_EXTRA); // reheat + pop-in pending
      },
      () => {
        advance(INITIAL_SETTLE_MS);
        pushGraph(WITHOUT_INIT); // fade pending
        advance(100);
      },
      () => pushGraph(OTHER), // just rebuilt
      () => advance(2000),
    ];

    for (const interrupt of interrupts) {
      const { unmount } = mount(BASE, { strict: true });
      interrupt();
      unmount();
      expect(liveSigmas()).toHaveLength(0);
      expect(liveLayouts()).toHaveLength(0);
      expect(vi.getTimerCount()).toBe(0);
    }
    advance(60_000);
    expectAllRetiredCleanly();

    mount(BASE, { strict: true });
    theSigma();
    theLayout();
    expect(vi.getTimerCount()).toBe(1);
  });

  it("a key-change remount retires the old instance", () => {
    pushGraph(BASE);
    const { rerender } = render(<GraphCanvas key="one" />);
    const first = theSigma();

    rerender(<GraphCanvas key="two" />);

    expect(first.killCount).toBe(1);
    expect(theSigma()).not.toBe(first);
    expect(vi.getTimerCount()).toBe(1);
    expectAllRetiredCleanlyExceptLive();
  });
});
