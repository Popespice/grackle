import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { NodeInspector } from "./NodeInspector";

afterEach(cleanup);

const BASE_NODE = {
  id: "src/app.py:App",
  kind: "class",
  name: "App",
  path: "src/app.py",
  line: 12,
  metadata: {},
};

describe("NodeInspector", () => {
  it("renders nothing when node is null", () => {
    const { container } = render(<NodeInspector node={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("renders kind label, name, and path for a node", () => {
    render(<NodeInspector node={BASE_NODE} />);
    expect(screen.getByText("class")).toBeInTheDocument();
    expect(screen.getByText("App")).toBeInTheDocument();
    expect(screen.getByText(/src\/app\.py:12/)).toBeInTheDocument();
  });

  it("renders the node id", () => {
    render(<NodeInspector node={BASE_NODE} />);
    expect(screen.getByText("src/app.py:App")).toBeInTheDocument();
  });

  it("renders parent from metadata when present", () => {
    const node = {
      ...BASE_NODE,
      metadata: { parent: "src/base.py:Base" },
    };
    render(<NodeInspector node={node} />);
    expect(screen.getByText("parent")).toBeInTheDocument();
    expect(screen.getByText("src/base.py:Base")).toBeInTheDocument();
  });

  it("omits parent row when metadata.parent is absent", () => {
    render(<NodeInspector node={BASE_NODE} />);
    expect(screen.queryByText("parent")).not.toBeInTheDocument();
  });

  it("renders decorators from metadata when present", () => {
    const node = {
      ...BASE_NODE,
      metadata: { decorators: ["dataclass", "cached_property"] },
    };
    render(<NodeInspector node={node} />);
    expect(screen.getByText("decorators")).toBeInTheDocument();
    expect(
      screen.getByText("@dataclass, @cached_property")
    ).toBeInTheDocument();
  });

  it("calls onClose when the × button is clicked", () => {
    const onClose = vi.fn();
    render(<NodeInspector node={BASE_NODE} onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: "Close inspector" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("renders without crashing when onClose is not provided", () => {
    render(<NodeInspector node={BASE_NODE} />);
    fireEvent.click(screen.getByRole("button", { name: "Close inspector" }));
    // no error thrown
  });
});
