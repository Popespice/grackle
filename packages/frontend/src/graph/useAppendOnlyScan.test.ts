import type { TraceEvent } from "@grackle/shared-types";
import { renderHook } from "@testing-library/react";
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
    // The array is hoisted deliberately: built inline in the render callback it
    // would be a NEW array of NEW event objects every render, so the identity
    // check would invalidate the cache before `cache.scan === scan` was ever
    // consulted and this test would pass with that check deleted.
    const events = [ev("hit"), ev("hit")];
    const { result, rerender } = renderHook(
      ({ s }) => useAppendOnlyScan(events, s, true),
      { initialProps: { s: scanHits } }
    );
    expect(result.current.items).toEqual(["hit@0", "hit@1"]);
    const onlyFirst: Scanner<string, number> = (evs, startIndex, carry) => {
      const step = scanHits(evs, startIndex, carry);
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

  it("does not re-invoke scan for an already-scanned array (StrictMode-safe)", () => {
    // The app renders under <StrictMode> (main.tsx), which double-invokes every
    // render, and a concurrent render can be discarded and replayed. Rather
    // than trusting each scanner to be a no-op over an empty tail, the hook
    // short-circuits before calling it at all — so even a scanner that
    // accumulated per CALL rather than per event cannot double-count.
    const scan = vi.fn(scanHits);
    const events = [ev("hit"), ev("miss")];
    const { result, rerender } = renderHook(
      ({ e }) => useAppendOnlyScan(e, scan),
      { initialProps: { e: events } }
    );
    expect(scan).toHaveBeenCalledTimes(1);
    rerender({ e: events });
    expect(scan).toHaveBeenCalledTimes(1);
    expect(result.current.items).toEqual(["hit@0"]);
    expect(result.current.carry).toBe(1);
  });

  it("keeps the cache warm across a disable/enable cycle", () => {
    // A collapsed panel that reopens on the same session must not pay for a
    // full rescan of the prefix it already walked.
    const scan = vi.fn(scanHits);
    const events = [ev("hit"), ev("miss")];
    const { result, rerender } = renderHook(
      ({ on }) => useAppendOnlyScan(events, scan, on),
      { initialProps: { on: true } }
    );
    expect(scan).toHaveBeenCalledTimes(1);

    rerender({ on: false });
    expect(result.current.items).toEqual([]);

    rerender({ on: true });
    expect(result.current.items).toEqual(["hit@0"]);
    expect(scan).toHaveBeenCalledTimes(1); // resumed, never rescanned
  });

  it("still rescans after a disable when the session's array changed", () => {
    const scan = vi.fn(scanHits);
    const first = [ev("hit"), ev("miss")];
    const { result, rerender } = renderHook(
      ({ e, on }) => useAppendOnlyScan(e, scan, on),
      { initialProps: { e: first, on: true } }
    );
    rerender({ e: first, on: false });
    // A different session hands us an unrelated array while collapsed.
    rerender({ e: [ev("miss"), ev("hit")], on: true });
    expect(result.current.items).toEqual(["hit@1"]);
  });
});
