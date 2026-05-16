import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { PanelRegistry } from "./registry";
import { SlotContainer } from "./SlotContainer";

afterEach(cleanup);

describe("SlotContainer", () => {
  it("renders nothing when no panels are registered for the slot", () => {
    const r = new PanelRegistry();
    const { container } = render(<SlotContainer slot="top-bar" registry={r} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders a registered panel", () => {
    const r = new PanelRegistry();
    r.register({
      slot: "top-bar",
      id: "hello",
      component: () => <div>Hello panel</div>,
      order: 0,
    });
    render(<SlotContainer slot="top-bar" registry={r} />);
    expect(screen.getByText("Hello panel")).toBeInTheDocument();
  });

  it("renders panels in order", () => {
    const r = new PanelRegistry();
    r.register({
      slot: "top-bar",
      id: "b",
      component: () => <span>B</span>,
      order: 20,
    });
    r.register({
      slot: "top-bar",
      id: "a",
      component: () => <span>A</span>,
      order: 10,
    });
    render(<SlotContainer slot="top-bar" registry={r} />);
    const items = screen.getAllByText(/^[AB]$/);
    expect(items[0]?.textContent).toBe("A");
    expect(items[1]?.textContent).toBe("B");
  });

  it("only renders panels for the requested slot", () => {
    const r = new PanelRegistry();
    r.register({
      slot: "left-sidebar",
      id: "other",
      component: () => <div>Other</div>,
      order: 0,
    });
    r.register({
      slot: "top-bar",
      id: "mine",
      component: () => <div>Mine</div>,
      order: 0,
    });
    render(<SlotContainer slot="top-bar" registry={r} />);
    expect(screen.queryByText("Other")).not.toBeInTheDocument();
    expect(screen.getByText("Mine")).toBeInTheDocument();
  });

  it("uses the singleton panels registry when no registry prop provided", () => {
    // Just verify it renders without error (singleton may or may not have panels)
    expect(() => render(<SlotContainer slot="bottom-status" />)).not.toThrow();
    cleanup();
  });
});
