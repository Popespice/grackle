import { describe, expect, it } from "vitest";
import { PanelRegistry } from "./registry";

const noop = () => null;

describe("PanelRegistry", () => {
  it("registers a panel and retrieves it by slot", () => {
    const r = new PanelRegistry();
    r.register({ slot: "top-bar", id: "foo", component: noop, order: 0 });
    const entries = r.getForSlot("top-bar");
    expect(entries).toHaveLength(1);
    expect(entries[0]?.id).toBe("foo");
  });

  it("returns empty array for a slot with no registrations", () => {
    const r = new PanelRegistry();
    expect(r.getForSlot("left-sidebar")).toEqual([]);
  });

  it("filters panels by slot", () => {
    const r = new PanelRegistry();
    r.register({ slot: "top-bar", id: "top", component: noop, order: 0 });
    r.register({ slot: "left-sidebar", id: "left", component: noop, order: 0 });
    const topEntries = r.getForSlot("top-bar");
    const leftEntries = r.getForSlot("left-sidebar");
    expect(topEntries).toHaveLength(1);
    expect(topEntries[0]?.id).toBe("top");
    expect(leftEntries).toHaveLength(1);
    expect(leftEntries[0]?.id).toBe("left");
  });

  it("sorts panels by order within a slot", () => {
    const r = new PanelRegistry();
    r.register({ slot: "right-sidebar", id: "b", component: noop, order: 20 });
    r.register({ slot: "right-sidebar", id: "a", component: noop, order: 10 });
    const sorted = r.getForSlot("right-sidebar");
    expect(sorted[0]?.id).toBe("a");
    expect(sorted[1]?.id).toBe("b");
  });

  it("throws when registering a duplicate id", () => {
    const r = new PanelRegistry();
    r.register({ slot: "top-bar", id: "dup", component: noop, order: 0 });
    expect(() =>
      r.register({ slot: "left-sidebar", id: "dup", component: noop, order: 0 })
    ).toThrow("'dup' is already registered");
  });

  it("accepts unknown slot strings (open-string per ADR-0004)", () => {
    const r = new PanelRegistry();
    r.register({
      slot: "custom-extension-slot",
      id: "ext",
      component: noop,
      order: 0,
    });
    expect(r.getForSlot("custom-extension-slot")).toHaveLength(1);
  });

  it("getForSlot does not mutate order on repeated calls", () => {
    const r = new PanelRegistry();
    r.register({ slot: "top-bar", id: "z", component: noop, order: 30 });
    r.register({ slot: "top-bar", id: "a", component: noop, order: 5 });
    r.getForSlot("top-bar"); // first call
    const second = r.getForSlot("top-bar"); // second call
    expect(second[0]?.id).toBe("a");
    expect(second[1]?.id).toBe("z");
  });
});
