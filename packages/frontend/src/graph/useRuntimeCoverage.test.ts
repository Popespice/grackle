/**
 * useRuntimeCoverage — session coverage for StatsPanel and DiffPanel
 * (ADR-0015) (campaign T11-6, `docs/test-campaigns/phase-12.md`).
 * `runtimeCoverage.ts` is tested; this wrapper was not. ADR-0015 made it a
 * hook precisely so it would recompute as trace events arrive (an
 * AnalysisRegistry entry would have cached the first, empty result), so that
 * is what this file pins hardest.
 */
import type { Graph, TraceEvent } from "@grackle/shared-types";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { restoreInitialState } from "../test/storeReset";
import { useGraphStore } from "./useGraphStore";
import { useRuntimeCoverage } from "./useRuntimeCoverage";

const GRAPH: Graph = {
  version: 1,
  language: "python",
  nodes: [
    { id: "m.py:a", kind: "function", name: "a", path: "m.py" },
    { id: "m.py:b", kind: "function", name: "b", path: "m.py" },
    { id: "m.py:c", kind: "function", name: "c", path: "m.py" },
  ],
  edges: [],
};

function calls(node: string, n: number): TraceEvent[] {
  return Array.from({ length: n }, (_, i) => ({
    event: "call",
    node_id: node,
    ts_ns: i,
    thread_id: 1,
    frame_depth: 0,
  }));
}

beforeEach(() => {
  restoreInitialState(useGraphStore);
});

afterEach(cleanup);

describe("useRuntimeCoverage", () => {
  it("is null while no graph is loaded, even with events", () => {
    useGraphStore.setState({ traceEvents: calls("m.py:a", 3) });
    const { result } = renderHook(() => useRuntimeCoverage());
    expect(result.current).toBeNull();
  });

  it("reports every node cold before any event", () => {
    useGraphStore.setState({ graph: GRAPH });
    const { result } = renderHook(() => useRuntimeCoverage());
    expect(result.current?.touchedCount).toBe(0);
    expect(result.current?.cold).toEqual(
      new Set(["m.py:a", "m.py:b", "m.py:c"])
    );
  });

  it("splits touched, cold and hot, ignoring events for nodes outside the graph", () => {
    useGraphStore.setState({
      graph: GRAPH,
      traceEvents: [
        ...calls("m.py:a", 5),
        ...calls("m.py:b", 1),
        ...calls("<stdlib>:len", 50),
      ],
    });
    const { result } = renderHook(() => useRuntimeCoverage());
    expect(result.current?.touched).toEqual(new Set(["m.py:a", "m.py:b"]));
    expect(result.current?.cold).toEqual(new Set(["m.py:c"]));
    expect(result.current?.hot).toEqual(new Set(["m.py:a"]));
  });

  it("recomputes as trace events arrive", () => {
    useGraphStore.setState({ graph: GRAPH });
    const { result } = renderHook(() => useRuntimeCoverage());
    expect(result.current?.touchedCount).toBe(0);

    act(() => {
      useGraphStore.getState().addTraceEvents(calls("m.py:c", 2));
    });
    expect(result.current?.touched).toEqual(new Set(["m.py:c"]));
    expect(result.current?.coldCount).toBe(2);
  });

  it("recomputes when the graph is replaced", () => {
    useGraphStore.setState({ graph: GRAPH, traceEvents: calls("m.py:c", 2) });
    const { result } = renderHook(() => useRuntimeCoverage());
    expect(result.current?.touchedCount).toBe(1);

    act(() => {
      useGraphStore.getState().setGraph({
        ...GRAPH,
        nodes: GRAPH.nodes.filter((n) => n.id !== "m.py:c"),
      });
    });
    expect(result.current?.touchedCount).toBe(0);
    expect(result.current?.coldCount).toBe(2);
  });

  it("returns the same object across an unrelated store change", () => {
    useGraphStore.setState({ graph: GRAPH, traceEvents: calls("m.py:a", 1) });
    const { result } = renderHook(() => useRuntimeCoverage());
    const first = result.current;
    act(() => {
      useGraphStore.getState().setPlayhead(1);
    });
    expect(result.current).toBe(first);
  });
});
