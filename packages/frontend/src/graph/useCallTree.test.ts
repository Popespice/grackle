/**
 * useCallTree — the flame graph's data model (Phase 8.2, ADR-0019) (campaign
 * T11-6, `docs/test-campaigns/phase-12.md`). `callTree.ts` is tested; this
 * wrapper — which events it reconstructs from, and its memo chain — was not.
 */
import type { TraceEvent } from "@grackle/shared-types";
import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { restoreInitialState } from "../test/storeReset";
import { useCallTree } from "./useCallTree";
import { useGraphStore } from "./useGraphStore";

function ev(
  event: "call" | "return",
  node: string,
  ts: number,
  depth: number
): TraceEvent {
  return {
    event,
    node_id: node,
    ts_ns: ts,
    thread_id: 1,
    frame_depth: depth,
  };
}

// main → work twice; the second `work` is the long one.
const STORE_EVENTS: TraceEvent[] = [
  ev("call", "m.py:main", 0, 0),
  ev("call", "m.py:work", 10, 1),
  ev("return", "m.py:work", 20, 1),
  ev("call", "m.py:work", 30, 1),
  ev("return", "m.py:work", 90, 1),
  ev("return", "m.py:main", 100, 0),
];

// A different run entirely: one frame, nothing in common with the store.
const OVERRIDE_EVENTS: TraceEvent[] = [
  ev("call", "o.py:other", 0, 0),
  ev("return", "o.py:other", 5, 0),
];

beforeEach(() => {
  restoreInitialState(useGraphStore);
  useGraphStore.setState({ traceEvents: STORE_EVENTS });
});

afterEach(cleanup);

describe("useCallTree", () => {
  it("reconstructs the store's events when given no override", () => {
    const { result } = renderHook(() => useCallTree());
    const { tree } = result.current;
    expect(tree.frameCount).toBe(3);
    expect(tree.roots.map((f) => f.nodeId)).toEqual(["m.py:main"]);
    expect(tree.roots[0]?.children.map((f) => f.totalNs)).toEqual([10, 60]);
  });

  it("a null override also means the store's events", () => {
    const { result } = renderHook(() => useCallTree(null));
    expect(result.current.tree.frameCount).toBe(3);
  });

  it("an override replaces the store's events (the paged full trace)", () => {
    const { result } = renderHook(() => useCallTree(OVERRIDE_EVENTS));
    expect(result.current.tree.roots.map((f) => f.nodeId)).toEqual([
      "o.py:other",
    ]);
    expect(result.current.tree.frameCount).toBe(1);
  });

  it("aggregates repeated calls of the same path into one frame", () => {
    const { result } = renderHook(() => useCallTree());
    const [main] = result.current.aggregated;
    expect(main?.children).toHaveLength(1);
    expect(main?.children[0]).toMatchObject({
      nodeId: "m.py:work",
      count: 2,
      totalNs: 70,
    });
  });

  it("marks the heaviest aggregated chain as the hot path", () => {
    const { result } = renderHook(() => useCallTree());
    const { aggregated, hot } = result.current;
    const main = aggregated[0];
    const work = main?.children[0];
    expect(hot).toEqual(new Set([main, work]));
  });

  it("keeps the same objects across an unrelated store change", () => {
    const { result } = renderHook(() => useCallTree());
    const first = result.current;
    act(() => {
      useGraphStore.getState().selectNode("m.py:main");
    });
    expect(result.current.tree).toBe(first.tree);
    expect(result.current.aggregated).toBe(first.aggregated);
    expect(result.current.hot).toBe(first.hot);
  });

  it("rebuilds when the store's events change", () => {
    const { result } = renderHook(() => useCallTree());
    const first = result.current.tree;
    act(() => {
      useGraphStore.getState().addTraceEvents(OVERRIDE_EVENTS);
    });
    expect(result.current.tree).not.toBe(first);
    expect(result.current.tree.frameCount).toBe(4);
  });

  it("switches source when an override arrives, and back when it goes", () => {
    const { result, rerender } = renderHook(
      ({ events }: { events: TraceEvent[] | null }) => useCallTree(events),
      { initialProps: { events: null as TraceEvent[] | null } }
    );
    expect(result.current.tree.frameCount).toBe(3);
    rerender({ events: OVERRIDE_EVENTS });
    expect(result.current.tree.frameCount).toBe(1);
    rerender({ events: null });
    expect(result.current.tree.frameCount).toBe(3);
  });
});
