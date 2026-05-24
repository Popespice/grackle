import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useGraphStore } from "../graph/useGraphStore";
import { TimelinePanel } from "./TimelinePanel";

afterEach(cleanup);

function mkEv(node_id: string, event = "call") {
  return { event, node_id, ts_ns: 0, thread_id: 1, frame_depth: 0 };
}

const EVENTS = [mkEv("a", "call"), mkEv("b", "call"), mkEv("a", "return")];

beforeEach(() => {
  useGraphStore.setState({
    graph: null,
    traceEvents: [],
    traceSessionId: null,
    traceSessionComplete: false,
    tracePlayhead: 0,
    tracePlaying: false,
    tracePlaybackSpeed: 1,
    traceEventTypeFilter: new Set<string>(),
    traceHeatMode: "cumulative",
    traceWindowSize: 200,
  });
});

describe("TimelinePanel", () => {
  it("renders null when traceSessionId is null", () => {
    const { container } = render(<TimelinePanel />);
    expect(container.firstChild).toBeNull();
  });

  it("renders the panel when a trace session is active", () => {
    useGraphStore.setState({ traceSessionId: "s1", traceEvents: EVENTS });
    render(<TimelinePanel />);
    expect(
      screen.getByRole("region", { name: "Trace timeline" })
    ).toBeInTheDocument();
  });

  it("renders the scrubber with correct min/max/value", () => {
    useGraphStore.setState({
      traceSessionId: "s1",
      traceEvents: EVENTS,
      tracePlayhead: 1,
    });
    render(<TimelinePanel />);
    const slider = screen.getByRole("slider", { name: "Playback position" });
    expect(slider).toHaveAttribute("max", "3");
    expect(slider).toHaveAttribute("value", "1");
  });

  it("shows 'Play' button when paused", () => {
    useGraphStore.setState({
      traceSessionId: "s1",
      traceEvents: EVENTS,
      tracePlaying: false,
    });
    render(<TimelinePanel />);
    expect(screen.getByRole("button", { name: /play/i })).toBeInTheDocument();
  });

  it("shows 'Pause' button when playing", () => {
    useGraphStore.setState({
      traceSessionId: "s1",
      traceEvents: EVENTS,
      tracePlaying: true,
    });
    render(<TimelinePanel />);
    expect(screen.getByRole("button", { name: /pause/i })).toBeInTheDocument();
  });

  it("calls play() when Play button is clicked", () => {
    useGraphStore.setState({
      traceSessionId: "s1",
      traceEvents: EVENTS,
      tracePlaying: false,
    });
    render(<TimelinePanel />);
    fireEvent.click(screen.getByRole("button", { name: /play/i }));
    expect(useGraphStore.getState().tracePlaying).toBe(true);
  });

  it("calls pause() when Pause button is clicked", () => {
    useGraphStore.setState({
      traceSessionId: "s1",
      traceEvents: EVENTS,
      tracePlaying: true,
    });
    render(<TimelinePanel />);
    fireEvent.click(screen.getByRole("button", { name: /pause/i }));
    expect(useGraphStore.getState().tracePlaying).toBe(false);
  });

  it("updates playhead when scrubber changes", () => {
    useGraphStore.setState({
      traceSessionId: "s1",
      traceEvents: EVENTS,
      tracePlayhead: 0,
    });
    render(<TimelinePanel />);
    const slider = screen.getByRole("slider", { name: "Playback position" });
    fireEvent.change(slider, { target: { value: "2" } });
    expect(useGraphStore.getState().tracePlayhead).toBe(2);
  });

  it("renders event-kind filter chips from the events", () => {
    useGraphStore.setState({ traceSessionId: "s1", traceEvents: EVENTS });
    render(<TimelinePanel />);
    // "call" and "return" should both appear as labeled checkboxes
    expect(screen.getByRole("checkbox", { name: "call" })).toBeInTheDocument();
    expect(
      screen.getByRole("checkbox", { name: "return" })
    ).toBeInTheDocument();
  });

  it("toggles event type filter when chip clicked", () => {
    useGraphStore.setState({ traceSessionId: "s1", traceEvents: EVENTS });
    render(<TimelinePanel />);
    fireEvent.click(screen.getByRole("checkbox", { name: "call" }));
    expect(useGraphStore.getState().traceEventTypeFilter.has("call")).toBe(
      true
    );
  });

  it("heat mode buttons switch traceHeatMode", () => {
    useGraphStore.setState({ traceSessionId: "s1", traceEvents: EVENTS });
    render(<TimelinePanel />);
    fireEvent.click(
      screen.getByRole("button", { name: /sliding window heat/i })
    );
    expect(useGraphStore.getState().traceHeatMode).toBe("sliding");
    fireEvent.click(screen.getByRole("button", { name: /cumulative heat/i }));
    expect(useGraphStore.getState().traceHeatMode).toBe("cumulative");
  });

  it("shows window-size control only in sliding mode", () => {
    useGraphStore.setState({
      traceSessionId: "s1",
      traceEvents: EVENTS,
      traceHeatMode: "cumulative",
    });
    render(<TimelinePanel />);
    expect(screen.queryByLabelText("Sliding window size")).toBeNull();

    useGraphStore.setState({ traceHeatMode: "sliding" });
    // Re-render
    cleanup();
    render(<TimelinePanel />);
    expect(screen.getByLabelText("Sliding window size")).toBeInTheDocument();
  });

  it("updates window size when number input changes", () => {
    useGraphStore.setState({
      traceSessionId: "s1",
      traceEvents: EVENTS,
      traceHeatMode: "sliding",
    });
    render(<TimelinePanel />);
    const input = screen.getByLabelText("Sliding window size");
    fireEvent.change(input, { target: { value: "500" } });
    expect(useGraphStore.getState().traceWindowSize).toBe(500);
  });

  it("speed select updates tracePlaybackSpeed", () => {
    useGraphStore.setState({ traceSessionId: "s1", traceEvents: EVENTS });
    render(<TimelinePanel />);
    const select = screen.getByLabelText("Playback speed");
    fireEvent.change(select, { target: { value: "4" } });
    expect(useGraphStore.getState().tracePlaybackSpeed).toBe(4);
  });
});
