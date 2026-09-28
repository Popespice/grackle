import { describe, expect, it, vi } from "vitest";
import { create } from "zustand";
import { restoreInitialState } from "./storeReset";

// Campaign T11-4: the reset must hand back fresh collections, not the initial
// state's own (a store that mutates a Set/Map in place mutates that object).
interface State {
  handlers: Set<() => void>;
  pending: Map<string, number>;
  count: number;
  bump: () => void;
}

function makeStore() {
  return create<State>()((set) => ({
    handlers: new Set(),
    pending: new Map(),
    count: 0,
    bump: () => set((s) => ({ count: s.count + 1 })),
  }));
}

describe("restoreInitialState", () => {
  it("clears a Set and a Map the store mutated in place", () => {
    const store = makeStore();
    restoreInitialState(store);
    store.getState().handlers.add(() => {});
    store.getState().pending.set("req", 1);

    restoreInitialState(store);

    expect(store.getState().handlers.size).toBe(0);
    expect(store.getState().pending.size).toBe(0);
  });

  it("hands out new collection objects each time", () => {
    const store = makeStore();
    restoreInitialState(store);
    const first = store.getState().handlers;
    restoreInitialState(store);
    expect(store.getState().handlers).not.toBe(first);
  });

  it("still puts back the original actions and plain fields", () => {
    const store = makeStore();
    restoreInitialState(store);
    const bump = store.getState().bump;
    store.setState({ count: 5, bump: vi.fn() });

    restoreInitialState(store);

    expect(store.getState().count).toBe(0);
    expect(store.getState().bump).toBe(bump);
  });
});
