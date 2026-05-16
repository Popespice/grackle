import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useGraphStore } from "../graph/useGraphStore";
import { SearchFilterPanel } from "./SearchFilterPanel";

afterEach(cleanup);

const MOCK_GRAPH = {
  version: 1,
  language: "python",
  nodes: [
    { id: "a.py:App", kind: "class", name: "App", path: "a.py" },
    { id: "b.py:main", kind: "function", name: "main", path: "b.py" },
    { id: "c.py:helper", kind: "function", name: "helper", path: "c.py" },
  ],
  edges: [],
};

beforeEach(() => {
  useGraphStore.setState({
    graph: MOCK_GRAPH,
    searchTerm: "",
    hiddenKinds: new Set<string>(),
    excludeGlobs: [],
    selectedNodeId: null,
  });
});

describe("SearchFilterPanel", () => {
  it("renders nothing when graph is null", () => {
    useGraphStore.setState({ graph: null });
    const { container } = render(<SearchFilterPanel />);
    expect(container.firstChild).toBeNull();
  });

  it("renders search input, kind checkboxes, and glob textarea", () => {
    render(<SearchFilterPanel />);
    expect(
      screen.getByRole("textbox", { name: /search/i })
    ).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: /file/i })).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: /class/i })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: /function/i })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: /method/i })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("textbox", { name: /exclude/i })
    ).toBeInTheDocument();
  });

  it("typing in search calls setSearch on the store", () => {
    render(<SearchFilterPanel />);
    const input = screen.getByRole("textbox", { name: /search/i });
    fireEvent.change(input, { target: { value: "auth" } });
    expect(useGraphStore.getState().searchTerm).toBe("auth");
  });

  it("toggling a kind checkbox calls toggleKind on the store", () => {
    render(<SearchFilterPanel />);
    const checkbox = screen.getByRole("checkbox", { name: /function/i });
    fireEvent.click(checkbox);
    expect(useGraphStore.getState().hiddenKinds.has("function")).toBe(true);
  });

  it("unchecking a hidden kind makes it visible again", () => {
    useGraphStore.setState({ hiddenKinds: new Set(["class"]) });
    render(<SearchFilterPanel />);
    const checkbox = screen.getByRole("checkbox", { name: /class/i });
    expect(checkbox).not.toBeChecked();
    fireEvent.click(checkbox);
    expect(useGraphStore.getState().hiddenKinds.has("class")).toBe(false);
  });

  it("blurring glob textarea calls setExcludes on the store", () => {
    render(<SearchFilterPanel />);
    const textarea = screen.getByRole("textbox", { name: /exclude/i });
    fireEvent.change(textarea, {
      target: { value: "tests/**\nsrc/conftest.py" },
    });
    fireEvent.blur(textarea);
    expect(useGraphStore.getState().excludeGlobs).toEqual([
      "tests/**",
      "src/conftest.py",
    ]);
  });

  it("shows hidden badge when nodes are hidden", () => {
    useGraphStore.setState({ hiddenKinds: new Set(["function"]) });
    render(<SearchFilterPanel />);
    // 2 function nodes are hidden, 1 class visible => hidden: 2 / 3
    expect(screen.getByLabelText(/Hidden: 2 of 3/)).toBeInTheDocument();
  });

  it("hides the badge when no nodes are hidden", () => {
    render(<SearchFilterPanel />);
    expect(screen.queryByLabelText(/Hidden:/)).not.toBeInTheDocument();
  });

  it("all kind checkboxes are checked by default", () => {
    render(<SearchFilterPanel />);
    for (const kind of ["file", "class", "function", "method"]) {
      expect(
        screen.getByRole("checkbox", { name: new RegExp(kind, "i") })
      ).toBeChecked();
    }
  });
});
