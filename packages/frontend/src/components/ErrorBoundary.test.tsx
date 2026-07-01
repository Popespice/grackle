import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "./ErrorBoundary";

afterEach(cleanup);

function Bomb({
  shouldThrow,
  thrown,
}: {
  shouldThrow: boolean;
  thrown?: unknown;
}) {
  if (shouldThrow) {
    throw thrown === undefined ? new Error("boom") : thrown;
  }
  return null;
}

describe("ErrorBoundary", () => {
  it("renders children when there is no error", () => {
    render(
      <ErrorBoundary label="test-panel">
        <div>safe content</div>
      </ErrorBoundary>
    );
    expect(screen.getByText("safe content")).toBeInTheDocument();
  });

  it("renders a fallback with the panel label and message on a thrown Error", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <ErrorBoundary label="test-panel">
        <Bomb shouldThrow />
      </ErrorBoundary>
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "test-panel crashed: boom"
    );
    spy.mockRestore();
  });

  it("normalises a thrown non-Error value into a readable message", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <ErrorBoundary label="test-panel">
        <Bomb shouldThrow thrown="raw string throw" />
      </ErrorBoundary>
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "test-panel crashed: raw string throw"
    );
    spy.mockRestore();
  });

  it("logs the panel label, error, and component stack via console.error", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <ErrorBoundary label="test-panel">
        <Bomb shouldThrow />
      </ErrorBoundary>
    );
    expect(spy).toHaveBeenCalledWith(
      "[ErrorBoundary] panel 'test-panel' crashed:",
      expect.any(Error),
      expect.anything()
    );
    spy.mockRestore();
  });

  it("recovers when Try again is clicked and the child no longer throws", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    let shouldThrow = true;
    function Flaky() {
      if (shouldThrow) throw new Error("transient");
      return <div>recovered</div>;
    }
    render(
      <ErrorBoundary label="test-panel">
        <Flaky />
      </ErrorBoundary>
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();

    shouldThrow = false;
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(screen.getByText("recovered")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    spy.mockRestore();
  });

  it("isolates the crash to this boundary's own children", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    render(
      <div>
        <ErrorBoundary label="crashy-panel">
          <Bomb shouldThrow />
        </ErrorBoundary>
        <div>sibling content</div>
      </div>
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("sibling content")).toBeInTheDocument();
    spy.mockRestore();
  });
});
