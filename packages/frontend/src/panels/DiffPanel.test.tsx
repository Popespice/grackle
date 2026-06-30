import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { graphCacheKey } from "../graph/analysis/cacheKey";
import type { RuntimeCoverage } from "../graph/runtimeCoverage";
import { DiffPanel } from "./DiffPanel";

// ---------------------------------------------------------------------------
// Module mocks
// ---------------------------------------------------------------------------

// Stub useRuntimeCoverage — real implementation depends on Zustand store.
// Default return is set in beforeEach so each test starts from a known state.
vi.mock("../graph/useRuntimeCoverage", () => ({
  useRuntimeCoverage: vi.fn(),
}));

// ---------------------------------------------------------------------------
// Store helpers
// ---------------------------------------------------------------------------

import { useGraphStore } from "../graph/useGraphStore";
import { useRuntimeCoverage } from "../graph/useRuntimeCoverage";

const mockedCoverage = vi.mocked(useRuntimeCoverage);

function coverage(touched: string[], cold: string[]): RuntimeCoverage {
  return {
    touched: new Set(touched),
    cold: new Set(cold),
    hot: new Set<string>(),
    touchedCount: touched.length,
    coldCount: cold.length,
    hotCount: 0,
  };
}

function resetStore() {
  useGraphStore.setState({
    graph: null,
    traceSessionId: null,
    traceEvents: [],
    agentHeat: null,
    diffBaseline: null,
    diffOverlay: null,
  });
}

const SIMPLE_GRAPH = {
  version: 1 as const,
  language: "python",
  nodes: [
    { id: "a", kind: "function", name: "a", path: "a.py" },
    { id: "b", kind: "function", name: "b", path: "b.py" },
  ],
  edges: [] as never[],
};

const OTHER_GRAPH = {
  version: 1 as const,
  language: "python",
  nodes: [{ id: "z", kind: "function", name: "z", path: "z.py" }],
  edges: [] as never[],
};

beforeEach(() => {
  mockedCoverage.mockReturnValue(null);
  sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  resetStore();
  vi.clearAllMocks();
  sessionStorage.clear();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("DiffPanel", () => {
  it("shows placeholder when no trace session", () => {
    render(<DiffPanel />);
    expect(screen.getByText(/Load a trace session/i)).toBeTruthy();
  });

  it("shows placeholder when graph is null even with session", () => {
    useGraphStore.setState({ traceSessionId: "s1", graph: null });
    render(<DiffPanel />);
    expect(screen.getByText(/Load a trace session/i)).toBeTruthy();
  });

  it("shows diff panel when session and graph are set", () => {
    useGraphStore.setState({ traceSessionId: "s1", graph: SIMPLE_GRAPH });
    render(<DiffPanel />);
    // Should show mode label
    expect(screen.getByText(/trace-vs-static/i)).toBeTruthy();
  });

  it("shows 'Set as baseline' button by default", () => {
    useGraphStore.setState({ traceSessionId: "s1", graph: SIMPLE_GRAPH });
    render(<DiffPanel />);
    expect(
      screen.getByRole("button", { name: /set as baseline/i })
    ).toBeTruthy();
  });

  it("switches to 'Clear baseline' when baseline is set", () => {
    useGraphStore.setState({
      traceSessionId: "s1",
      graph: SIMPLE_GRAPH,
      diffBaseline: { a: 5 },
    });
    render(<DiffPanel />);
    expect(
      screen.getByRole("button", { name: /clear baseline/i })
    ).toBeTruthy();
  });

  it("shows trace-vs-trace mode label when baseline is set", () => {
    useGraphStore.setState({
      traceSessionId: "s1",
      graph: SIMPLE_GRAPH,
      diffBaseline: { a: 5 },
      agentHeat: { a: 10 },
    });
    render(<DiffPanel />);
    expect(screen.getByText(/trace-vs-trace/i)).toBeTruthy();
  });

  it("shows regression banner when hotter entries exist", () => {
    useGraphStore.setState({
      traceSessionId: "s1",
      graph: SIMPLE_GRAPH,
      // baseline: a=1, current: a=5 -> hotter
      diffBaseline: { a: 1 },
      agentHeat: { a: 5 },
    });
    render(<DiffPanel />);
    expect(screen.getByText(/regression detected/i)).toBeTruthy();
  });

  it("does not show regression banner when no regressions", () => {
    useGraphStore.setState({
      traceSessionId: "s1",
      graph: SIMPLE_GRAPH,
      diffBaseline: { a: 5 },
      agentHeat: { a: 3 }, // colder, not hotter
    });
    render(<DiffPanel />);
    expect(screen.queryByText(/regression detected/i)).toBeNull();
  });

  it("shows 'Show overlay' toggle (overlay off by default)", () => {
    useGraphStore.setState({ traceSessionId: "s1", graph: SIMPLE_GRAPH });
    render(<DiffPanel />);
    // Default off → button reads "Show overlay", and the store overlay is null
    // so the runtime heat-map is NOT suppressed.
    expect(screen.getByRole("button", { name: /show overlay/i })).toBeTruthy();
    expect(useGraphStore.getState().diffOverlay).toBeNull();
  });

  it("does not write a graph overlay until the user enables it", async () => {
    // Coverage present → trace-vs-static entries exist, but overlay must stay
    // off until the user opts in (otherwise the heat-map is hijacked).
    mockedCoverage.mockReturnValue(coverage(["a"], ["b"]));
    useGraphStore.setState({ traceSessionId: "s1", graph: SIMPLE_GRAPH });
    render(<DiffPanel />);
    // Mounting DiffPanel must not hijack the heat-map: overlay stays null.
    expect(useGraphStore.getState().diffOverlay).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /show overlay/i }));
    // After enabling, the debounced effect writes the overlay (150 ms).
    await waitFor(
      () => expect(useGraphStore.getState().diffOverlay).not.toBeNull(),
      { timeout: 1000 }
    );
  });

  it("'Set as baseline' auto-enables the overlay", async () => {
    useGraphStore.setState({
      traceSessionId: "s1",
      graph: SIMPLE_GRAPH,
      agentHeat: { a: 3 },
    });
    render(<DiffPanel />);
    fireEvent.click(screen.getByRole("button", { name: /set as baseline/i }));
    // Toggle flips to "Hide overlay" and the overlay is written (debounced).
    expect(screen.getByRole("button", { name: /hide overlay/i })).toBeTruthy();
    await waitFor(
      () => expect(useGraphStore.getState().diffOverlay).not.toBeNull(),
      { timeout: 1000 }
    );
  });
});

// ---------------------------------------------------------------------------
// Baseline sessionStorage persistence (Phase 9.3, ADR-0021 amendment)
// ---------------------------------------------------------------------------

describe("DiffPanel baseline persistence", () => {
  it("restores baseline on mount when storage has it and store baseline is null", async () => {
    const key = `grackle:diff-baseline:${await graphCacheKey(SIMPLE_GRAPH)}`;
    sessionStorage.setItem(key, JSON.stringify({ a: 7 }));

    useGraphStore.setState({
      traceSessionId: "s1",
      graph: SIMPLE_GRAPH,
      agentHeat: { a: 7 },
    });
    render(<DiffPanel />);

    await waitFor(() =>
      expect(useGraphStore.getState().diffBaseline).toEqual({ a: 7 })
    );
    expect(screen.getByText(/trace-vs-trace/i)).toBeTruthy();
  });

  it("does not clobber a freshly-set non-null baseline", async () => {
    const key = `grackle:diff-baseline:${await graphCacheKey(SIMPLE_GRAPH)}`;
    sessionStorage.setItem(key, JSON.stringify({ a: 99 }));

    useGraphStore.setState({
      traceSessionId: "s1",
      graph: SIMPLE_GRAPH,
      diffBaseline: { a: 1 }, // already set before mount
    });
    render(<DiffPanel />);

    // Give the restore effect's microtask a chance to run; the === null
    // guard must prevent it from overwriting the existing baseline.
    await new Promise((r) => setTimeout(r, 10));
    expect(useGraphStore.getState().diffBaseline).toEqual({ a: 1 });
  });

  it("setGraph(sameGraph) clears the store baseline but restore re-applies it", async () => {
    // Non-empty baseline (agentHeat present) so it survives the empty-object
    // rejection in restoreBaseline.
    useGraphStore.setState({
      traceSessionId: "s1",
      graph: SIMPLE_GRAPH,
      agentHeat: { a: 5 },
    });
    render(<DiffPanel />);

    fireEvent.click(screen.getByRole("button", { name: /set as baseline/i }));
    await waitFor(() =>
      expect(useGraphStore.getState().diffBaseline).toEqual({ a: 5 })
    );

    // A reload re-pushes the same graph content via setGraph, which clears
    // diffBaseline to null (graph-scoped invariant) before the restore
    // effect can re-fire on the new graph object.
    useGraphStore.getState().setGraph({ ...SIMPLE_GRAPH });

    await waitFor(() =>
      expect(useGraphStore.getState().diffBaseline).toEqual({ a: 5 })
    );
  });

  it("setGraph(differentGraph) does not restore the old graph's baseline", async () => {
    useGraphStore.setState({ traceSessionId: "s1", graph: SIMPLE_GRAPH });
    render(<DiffPanel />);

    fireEvent.click(screen.getByRole("button", { name: /set as baseline/i }));
    await waitFor(() =>
      expect(useGraphStore.getState().diffBaseline).not.toBeNull()
    );

    useGraphStore.getState().setGraph(OTHER_GRAPH);

    // diffBaseline must stay null — OTHER_GRAPH's content hash has no
    // persisted entry, so restore is a no-op (hash miss).
    await new Promise((r) => setTimeout(r, 10));
    expect(useGraphStore.getState().diffBaseline).toBeNull();
  });

  it("clicking 'Set as baseline' persists; clicking 'Clear baseline' removes it", async () => {
    useGraphStore.setState({
      traceSessionId: "s1",
      graph: SIMPLE_GRAPH,
      agentHeat: { a: 4 },
    });
    render(<DiffPanel />);
    const key = `grackle:diff-baseline:${await graphCacheKey(SIMPLE_GRAPH)}`;

    fireEvent.click(screen.getByRole("button", { name: /set as baseline/i }));
    await waitFor(() =>
      expect(sessionStorage.getItem(key)).toBe(JSON.stringify({ a: 4 }))
    );

    fireEvent.click(screen.getByRole("button", { name: /clear baseline/i }));
    await waitFor(() => expect(sessionStorage.getItem(key)).toBeNull());
  });

  it("a stale async restore from a previous graph does not land on the current graph", async () => {
    const persistence = await import("../graph/diffBaselinePersistence");
    let resolveSlowRestore: (v: Record<string, number> | null) => void =
      () => {};
    const slowRestore = new Promise<Record<string, number> | null>(
      (resolve) => {
        resolveSlowRestore = resolve;
      }
    );
    const spy = vi
      .spyOn(persistence, "restoreBaseline")
      .mockImplementationOnce(() => slowRestore) // SIMPLE_GRAPH's restore call — never resolves yet
      .mockImplementationOnce(async () => null); // OTHER_GRAPH's restore call — resolves immediately

    useGraphStore.setState({ traceSessionId: "s1", graph: SIMPLE_GRAPH });
    const { rerender } = render(<DiffPanel />);

    // Switch to a different graph before SIMPLE_GRAPH's restore resolves.
    useGraphStore.getState().setGraph(OTHER_GRAPH);
    rerender(<DiffPanel />);
    await new Promise((r) => setTimeout(r, 0));

    // Now let the stale SIMPLE_GRAPH restore resolve with a baseline. The
    // identity guard (getState().graph === graph) must reject it because
    // the current graph is no longer SIMPLE_GRAPH.
    resolveSlowRestore({ a: 123 });
    await new Promise((r) => setTimeout(r, 10));

    expect(useGraphStore.getState().diffBaseline).toBeNull();
    spy.mockRestore();
  });

  it("restoring a baseline also enables the overlay (mirrors 'Set as baseline')", async () => {
    const key = `grackle:diff-baseline:${await graphCacheKey(SIMPLE_GRAPH)}`;
    sessionStorage.setItem(key, JSON.stringify({ a: 7 }));

    useGraphStore.setState({
      traceSessionId: "s1",
      graph: SIMPLE_GRAPH,
      agentHeat: { a: 7 },
    });
    render(<DiffPanel />);

    await waitFor(() =>
      expect(useGraphStore.getState().diffBaseline).toEqual({ a: 7 })
    );
    // The "Set as baseline" handler flips this to "Hide overlay" and the
    // restore path must do the same -- otherwise a baseline restored after
    // F5 leaves the graph unpainted even though the panel says trace-vs-trace.
    expect(screen.getByRole("button", { name: /hide overlay/i })).toBeTruthy();
    await waitFor(() =>
      expect(useGraphStore.getState().diffOverlay).not.toBeNull()
    );
  });

  it("rapid Set-then-Clear does not leave a stale baseline in sessionStorage", async () => {
    useGraphStore.setState({
      traceSessionId: "s1",
      graph: SIMPLE_GRAPH,
      agentHeat: { a: 4 },
    });
    render(<DiffPanel />);
    const key = `grackle:diff-baseline:${await graphCacheKey(SIMPLE_GRAPH)}`;

    // Click Set then immediately Clear, with no await between them, so the
    // two persistBaseline calls race unless the panel serializes them.
    fireEvent.click(screen.getByRole("button", { name: /set as baseline/i }));
    fireEvent.click(screen.getByRole("button", { name: /clear baseline/i }));

    await waitFor(() =>
      expect(useGraphStore.getState().diffBaseline).toBeNull()
    );
    // Give both queued persist writes time to settle, then assert the key
    // reflects the LAST click (cleared), not whichever write happened to
    // resolve its hash computation last.
    await new Promise((r) => setTimeout(r, 20));
    expect(sessionStorage.getItem(key)).toBeNull();
  });

  it("serializes Set/Clear persist calls in click order (the queue is load-bearing)", async () => {
    // Spy so the Set persist hangs until we release it; the Clear persist must
    // NOT run until the Set one resolves. Without the persistQueueRef chain
    // both would fire immediately and this ordering would not hold.
    const persistence = await import("../graph/diffBaselinePersistence");
    const calls: string[] = [];
    let resolveFirst: () => void = () => {};
    const spy = vi
      .spyOn(persistence, "persistBaseline")
      .mockImplementationOnce(() => {
        calls.push("set");
        return new Promise<void>((r) => {
          resolveFirst = r;
        });
      })
      .mockImplementationOnce(() => {
        calls.push("clear");
        return Promise.resolve();
      });

    useGraphStore.setState({
      traceSessionId: "s1",
      graph: SIMPLE_GRAPH,
      agentHeat: { a: 4 },
    });
    render(<DiffPanel />);

    fireEvent.click(screen.getByRole("button", { name: /set as baseline/i }));
    fireEvent.click(screen.getByRole("button", { name: /clear baseline/i }));

    // Flush microtasks: the Set persist has run (and is hanging); the Clear
    // persist is queued behind it and must not have run yet.
    await new Promise((r) => setTimeout(r, 10));
    expect(calls).toEqual(["set"]);

    resolveFirst();
    await waitFor(() => expect(calls).toEqual(["set", "clear"]));
    spy.mockRestore();
  });

  it("auto-enables the overlay only on the first restore, not on a later graph re-push", async () => {
    const key = `grackle:diff-baseline:${await graphCacheKey(SIMPLE_GRAPH)}`;
    sessionStorage.setItem(key, JSON.stringify({ a: 7 }));
    useGraphStore.setState({
      traceSessionId: "s1",
      graph: SIMPLE_GRAPH,
      agentHeat: { a: 7 },
    });
    render(<DiffPanel />);

    // First restore enables the overlay.
    await waitFor(() =>
      expect(screen.getByRole("button", { name: /hide overlay/i })).toBeTruthy()
    );

    // User explicitly hides the overlay.
    fireEvent.click(screen.getByRole("button", { name: /hide overlay/i }));
    expect(screen.getByRole("button", { name: /show overlay/i })).toBeTruthy();

    // A routine static_graph re-push clears + re-restores the baseline, but must
    // NOT re-enable the overlay against the user's just-expressed Hide choice.
    useGraphStore.getState().setGraph({ ...SIMPLE_GRAPH });
    await waitFor(() =>
      expect(useGraphStore.getState().diffBaseline).toEqual({ a: 7 })
    );
    await new Promise((r) => setTimeout(r, 10));
    expect(screen.getByRole("button", { name: /show overlay/i })).toBeTruthy();
  });
});
