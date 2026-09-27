/**
 * useTracePlayback — the rAF loop that advances the trace playhead while
 * playing (campaign T11-6, `docs/test-campaigns/phase-12.md`; ADR-0015
 * "rAF playback loop"). It had no test before this file.
 *
 * Driven by vitest's fake timers, which fake `requestAnimationFrame` too: a
 * frame fires on every 16 ms boundary with the fake clock as its timestamp,
 * and `vi.getTimerCount()` counts a pending frame — which is how "no frame
 * survives unmount / pause" is asserted. The 120 Hz probe needs frames at a
 * different interval, so it drives a hand-rolled rAF queue instead.
 *
 * Arithmetic the assertions rely on (EVENTS_PER_MS = 0.05): a frame advances
 * `max(1, round(deltaMs * 0.05 * speed))`, and the first frame after `play`
 * has no previous timestamp, so it advances exactly 1. At a 16 ms interval
 * that is 1 event/frame at 1×, 2 at 2×, 3 at 4×.
 */
import type { TraceEvent } from "@grackle/shared-types";
import { act, cleanup, renderHook } from "@testing-library/react";
import { createElement, type ReactNode, StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { restoreInitialState } from "../test/storeReset";
import { useGraphStore } from "./useGraphStore";
import { useTracePlayback } from "./useTracePlayback";

const FRAME_MS = 16;

function mkEvents(n: number): TraceEvent[] {
  return Array.from({ length: n }, (_, i) => ({
    event: "call",
    node_id: `m.py:f${i % 7}`,
    ts_ns: i,
    thread_id: 1,
    frame_depth: 0,
  }));
}

function seedBuffered(n: number, playhead = 0): void {
  useGraphStore.setState({
    traceSessionId: "s1",
    traceEvents: mkEvents(n),
    tracePlayhead: playhead,
    traceSeekable: false,
  });
}

function playhead(): number {
  return useGraphStore.getState().tracePlayhead;
}

function playing(): boolean {
  return useGraphStore.getState().tracePlaying;
}

function play(): void {
  act(() => {
    useGraphStore.getState().play();
  });
}

function pause(): void {
  act(() => {
    useGraphStore.getState().pause();
  });
}

/** Advance the fake clock by `n` frame intervals (one rAF callback each). */
function frames(n = 1): void {
  act(() => {
    vi.advanceTimersByTime(FRAME_MS * n);
  });
}

function mount() {
  return renderHook(() => useTracePlayback());
}

beforeEach(() => {
  vi.useFakeTimers();
  restoreInitialState(useGraphStore);
});

afterEach(() => {
  // Unmount first: a hook left mounted keeps its loop running into the next
  // test (RTL's auto-cleanup is not registered without vitest globals).
  cleanup();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("useTracePlayback — idle", () => {
  it("schedules no frame while not playing", () => {
    seedBuffered(10);
    mount();
    expect(vi.getTimerCount()).toBe(0);
    frames(5);
    expect(playhead()).toBe(0);
  });

  it("is a no-op when requestAnimationFrame is unavailable", () => {
    vi.stubGlobal("requestAnimationFrame", undefined);
    seedBuffered(10);
    mount();
    play();
    frames(5);
    expect(playhead()).toBe(0);
    expect(playing()).toBe(true);
  });
});

describe("useTracePlayback — advancing", () => {
  it("advances exactly 1 on the first frame, then by elapsed time × speed", () => {
    seedBuffered(100);
    mount();
    play();
    expect(vi.getTimerCount()).toBe(1); // one frame requested, none fired yet
    expect(playhead()).toBe(0);

    frames(1);
    expect(playhead()).toBe(1); // no previous timestamp: the floor of 1
    frames(1);
    expect(playhead()).toBe(2); // 16 ms × 0.05 × 1 = 0.8 → 1
    frames(3);
    expect(playhead()).toBe(5);
    expect(playing()).toBe(true);
  });

  it("scales the per-frame advance with the playback speed", () => {
    seedBuffered(100);
    useGraphStore.setState({ tracePlaybackSpeed: 2 });
    mount();
    play();
    frames(1);
    expect(playhead()).toBe(1);
    frames(2);
    expect(playhead()).toBe(5); // 16 × 0.05 × 2 = 1.6 → 2 per frame
  });

  it("picks up a speed change mid-play on the next frame, without restarting the loop", () => {
    seedBuffered(100);
    mount();
    play();
    frames(2);
    expect(playhead()).toBe(2);

    act(() => {
      useGraphStore.getState().setSpeed(2);
    });
    expect(vi.getTimerCount()).toBe(1); // still exactly one pending frame
    frames(1);
    expect(playhead()).toBe(4);
  });

  it("follows a buffer that grows during playback (the bound is read every frame)", () => {
    seedBuffered(3);
    mount();
    play();
    frames(2);
    expect(playhead()).toBe(2);

    act(() => {
      useGraphStore.getState().addTraceEvents(mkEvents(10));
    });
    frames(5);
    expect(playhead()).toBe(7);
    expect(playing()).toBe(true);
  });

  it("runs one loop under StrictMode's double-invoked effect, not two", () => {
    seedBuffered(100);
    renderHook(() => useTracePlayback(), {
      wrapper: ({ children }: { children: ReactNode }) =>
        createElement(StrictMode, null, children),
    });
    play();
    expect(vi.getTimerCount()).toBe(1);
    frames(3);
    expect(playhead()).toBe(3);
  });
});

describe("useTracePlayback — end of trace", () => {
  it("stops on the frame that reaches the end, with the playhead exactly at the end", () => {
    seedBuffered(4);
    mount();
    play();
    frames(3);
    expect(playhead()).toBe(3);
    expect(playing()).toBe(true);

    frames(1); // 3 + 1 = 4 = the end
    expect(playhead()).toBe(4);
    expect(playing()).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("clamps an overshooting final frame to the end instead of passing it", () => {
    seedBuffered(10, 8);
    useGraphStore.setState({ tracePlaybackSpeed: 4 });
    mount();
    play();
    frames(1);
    expect(playhead()).toBe(9);
    frames(1); // 9 + 3 = 12 → clamped to 10
    expect(playhead()).toBe(10);
    expect(playing()).toBe(false);
  });

  it("in seekable mode runs to traceTotal, not to the end of the loaded window", () => {
    useGraphStore.setState({
      traceSessionId: "s1",
      traceSeekable: true,
      traceEvents: mkEvents(5), // the in-memory window
      traceWindowStart: 40,
      traceTotal: 60,
      tracePlayhead: 50,
    });
    mount();
    play();
    frames(5);
    expect(playhead()).toBe(55); // past the 5-event window length
    expect(playing()).toBe(true);

    frames(10);
    expect(playhead()).toBe(60);
    expect(playing()).toBe(false);
  });
});

describe("useTracePlayback — pause, resume, teardown", () => {
  it("pause stops advancing and cancels the pending frame", () => {
    seedBuffered(100);
    mount();
    play();
    frames(3);
    pause();
    expect(vi.getTimerCount()).toBe(0);
    frames(10);
    expect(playhead()).toBe(3);
  });

  it("resuming after a long pause does not jump by the paused time", () => {
    seedBuffered(10_000);
    mount();
    play();
    frames(3);
    expect(playhead()).toBe(3);
    pause();

    act(() => {
      vi.advanceTimersByTime(10_000); // 10 s paused: 500 events' worth at 1×
    });
    play();
    frames(1);
    expect(playhead()).toBe(4);
  });

  it("leaves no frame scheduled after unmount, and the playhead stops", () => {
    seedBuffered(100);
    const { unmount } = mount();
    play();
    frames(2);
    expect(vi.getTimerCount()).toBe(1);

    unmount();
    expect(vi.getTimerCount()).toBe(0);
    frames(10);
    expect(playhead()).toBe(2);
  });

  it("a frame already dispatched when tracePlaying went false does not advance", () => {
    // The store can flip tracePlaying outside React (setPlayhead from a scrub,
    // a store write from a socket handler) and a frame can run before React
    // commits the effect cleanup that would cancel it. The callback re-reads
    // tracePlaying, so such a frame is inert. Simulated with the manual queue:
    // take the pending callback, flip the flag (the cleanup now cancels it),
    // then run the callback anyway, as a browser already dispatching it would.
    const driver = manualFrames();
    seedBuffered(100);
    mount();
    play();
    driver.fire(0);
    driver.fire(16);
    expect(playhead()).toBe(2);

    const [inFlight] = driver.take();
    expect(inFlight).toBeDefined();
    act(() => {
      useGraphStore.setState({ tracePlaying: false });
    });
    inFlight?.(32);

    expect(playhead()).toBe(2);
    expect(driver.pending()).toBe(0); // and it did not reschedule itself
  });
});

/** Playhead after one second of 1×/N× playback at the fake clock's 16 ms. */
function advanceInOneSecond(speed: number): number {
  restoreInitialState(useGraphStore);
  seedBuffered(100_000);
  useGraphStore.setState({ tracePlaybackSpeed: speed });
  const { unmount } = mount();
  play();
  act(() => {
    vi.advanceTimersByTime(1000);
  });
  const p = playhead();
  unmount();
  return p;
}

/**
 * A hand-driven rAF queue, so a test can choose the frame interval (the fake
 * clock's is fixed at 16 ms). `fire(ts)` runs every pending callback with `ts`;
 * `take()` removes and returns them without running them.
 */
function manualFrames() {
  const queue = new Map<number, FrameRequestCallback>();
  let nextId = 1;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    const id = nextId++;
    queue.set(id, cb);
    return id;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => {
    queue.delete(id);
  });
  const take = (): FrameRequestCallback[] => {
    const due = [...queue.values()];
    queue.clear();
    return due;
  };
  return {
    take,
    pending: (): number => queue.size,
    fire(ts: number): void {
      const due = take();
      act(() => {
        for (const cb of due) cb(ts);
      });
    },
  };
}

/** Playhead after `seconds` of playback at `hz` frames per second. */
function advanceAtRefreshRate(speed: number, hz: number, seconds = 1): number {
  restoreInitialState(useGraphStore);
  seedBuffered(100_000);
  useGraphStore.setState({ tracePlaybackSpeed: speed });
  const driver = manualFrames();
  const { unmount } = mount();
  play();
  // Frames at interval, 2×interval, … up to `seconds` — the schedule the fake
  // clock produces for its 16 ms interval.
  const interval = 1000 / hz;
  const count = Math.floor((seconds * 1000) / interval + 1e-9);
  for (let i = 1; i <= count; i++) driver.fire(i * interval);
  const p = playhead();
  unmount();
  vi.unstubAllGlobals();
  return p;
}

describe("useTracePlayback — speed multipliers", () => {
  it("2× covers about twice the ground of 1× at a 16 ms frame interval (control)", () => {
    const one = advanceInOneSecond(1);
    const two = advanceInOneSecond(2);
    expect(one).toBe(62); // 62 frames in 1000 ms, 1 event each
    expect(two / one).toBeGreaterThan(1.9);
    expect(two / one).toBeLessThan(2.1);
  });

  it("the hand-driven frame queue reproduces the fake clock at 62.5 Hz (control)", () => {
    // Same arithmetic as the control above, through the manual driver — so
    // the 120 Hz ledger entry below measures the hook, not the harness.
    expect(advanceAtRefreshRate(1, 62.5)).toBe(62);
    expect(advanceAtRefreshRate(2, 62.5)).toBe(123);
  });

  // KNOWN DEFECT (T11-6, docs/test-campaigns/phase-12.md): each frame
  // advances `max(1, round(deltaMs × 0.05 × speed))` and throws the fractional
  // remainder away, so the multipliers are not proportional. At a 16 ms frame
  // interval 4× advances 3 events a frame (round(3.2)), i.e. 3× the 1× rate.
  // Remove `.fails` in the PR that carries the remainder across frames.
  it.fails("4× covers about four times the ground of 1× at a 16 ms frame interval", () => {
    const one = advanceInOneSecond(1);
    const four = advanceInOneSecond(4);
    expect(four / one).toBeGreaterThan(3.8);
  });

  // KNOWN DEFECT (T11-6, docs/test-campaigns/phase-12.md): same root cause.
  // At 120 Hz a frame is 8.3 ms: 1× rounds 0.42 up to the floor of 1 and 2×
  // rounds 0.83 to 1, so on a 120 Hz display the 2× option plays at exactly
  // the 1× speed — and 1× runs at 120 events/s against the documented 50.
  it.fails("2× is faster than 1× on a 120 Hz display", () => {
    const one = advanceAtRefreshRate(1, 120);
    const two = advanceAtRefreshRate(2, 120);
    expect(two / one).toBeGreaterThan(1.5);
  });
});
