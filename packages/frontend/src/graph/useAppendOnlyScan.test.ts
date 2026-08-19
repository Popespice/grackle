import type { TraceEvent } from "@grackle/shared-types";
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { type Scanner, useAppendOnlyScan } from "./useAppendOnlyScan";

function ev(node_id: string): TraceEvent {
  return { event: "call", node_id, ts_ns: 0, thread_id: 1, frame_depth: 0 };
}

/** Collects the node_ids of every "hit" event, and carries a running count of
 *  everything else — a stand-in for the real scanners' dropped counters and
 *  open-frame state. */
const scanHits: Scanner<string, number> = (events, startIndex, carry) => {
  const items: string[] = [];
  let others = carry ?? 0;
  for (let i = startIndex; i < events.length; i++) {
    const e = events[i];
    if (!e) continue;
    if (e.node_id === "hit") items.push(`hit@${i}`);
    else others += 1;
  }
  return { items, carry: others };
};

describe("useAppendOnlyScan", () => {
  it("scans only the appended tail and accumulates", () => {
    const scan = vi.fn(scanHits);
    const first = [ev("hit"), ev("miss")];
    const { result, rerender } = renderHook(
      ({ events }) => useAppendOnlyScan(events, scan),
      { initialProps: { events: first } }
    );
    expect(result.current.items).toEqual(["hit@0"]);
    expect(result.current.carry).toBe(1);

    const grown = first.concat([ev("hit")]);
    rerender({ events: grown });
    expect(result.current.items).toEqual(["hit@0", "hit@2"]);
    expect(result.current.carry).toBe(1);
    // Second call resumed at index 2 rather than rescanning from 0.
    expect(scan.mock.calls[1]?.[1]).toBe(2);
  });

  it("preserves array identity when the tail held nothing new", () => {
    const events = [ev("hit")];
    const grown = events.concat([ev("miss")]);
    const { result, rerender } = renderHook(
      ({ e }) => useAppendOnlyScan(e, scanHits),
      { initialProps: { e: events } }
    );
    const before = result.current.items;
    rerender({ e: grown });
    // A fresh [].concat() every batch would invalidate every downstream memo.
    expect(result.current.items).toBe(before);
  });

  it("rescans from 0 when the array is not a pure append", () => {
    const scan = vi.fn(scanHits);
    const { result, rerender } = renderHook(
      ({ e }) => useAppendOnlyScan(e, scan),
      { initialProps: { e: [ev("hit"), ev("miss")] } }
    );
    // A different session hands us an unrelated array of the same length.
    rerender({ e: [ev("miss"), ev("hit")] });
    expect(result.current.items).toEqual(["hit@1"]);
    expect(scan.mock.calls[1]?.[1]).toBe(0);
  });

  it("rescans from 0 when the scan closure changes", () => {
    const { result, rerender } = renderHook(
      ({ s }) => useAppendOnlyScan([ev("hit"), ev("hit")], s, true),
      { initialProps: { s: scanHits } }
    );
    expect(result.current.items).toEqual(["hit@0", "hit@1"]);
    const onlyFirst: Scanner<string, number> = (events, startIndex, carry) => {
      const step = scanHits(events, startIndex, carry);
      return { items: step.items.slice(0, 1), carry: step.carry };
    };
    rerender({ s: onlyFirst });
    expect(result.current.items).toEqual(["hit@0"]);
  });

  it("does no work and returns nothing while disabled, then scans from 0", () => {
    const scan = vi.fn(scanHits);
    const events = [ev("hit")];
    const { result, rerender } = renderHook(
      ({ on }) => useAppendOnlyScan(events, scan, on),
      { initialProps: { on: false } }
    );
    expect(result.current.items).toEqual([]);
    expect(scan).not.toHaveBeenCalled();

    rerender({ on: true });
    expect(result.current.items).toEqual(["hit@0"]);
    expect(scan.mock.calls[0]?.[1]).toBe(0);
  });

  it("is idempotent across a re-render with the same array (StrictMode-safe)", () => {
    const events = [ev("hit"), ev("hit")];
    const { result, rerender } = renderHook(() =>
      useAppendOnlyScan(events, scanHits)
    );
    rerender();
    act(() => {});
    expect(result.current.items).toEqual(["hit@0", "hit@1"]);
  });
});
