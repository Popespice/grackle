/**
 * ConnectionBadge — the header's agent-connection indicator (campaign T11-6,
 * `docs/test-campaigns/phase-12.md`). It had no test.
 *
 * Each of the three socket states has its own label, dot colour, and — for
 * "connected" only — a pulse. The badge must follow the client store live:
 * it is the one place the UI says the agent went away.
 */
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { restoreInitialState } from "../test/storeReset";
import { type ConnectionStatus, useGrackleClient } from "../ws/client";
import { ConnectionBadge } from "./ConnectionBadge";

const CASES: {
  status: ConnectionStatus;
  label: string;
  color: string;
  pulses: boolean;
}[] = [
  {
    status: "disconnected",
    label: "agent disconnected",
    color: "var(--color-error)",
    pulses: false,
  },
  {
    status: "connecting",
    label: "connecting…",
    color: "var(--color-warning)",
    pulses: false,
  },
  {
    status: "connected",
    label: "agent connected",
    color: "var(--color-success)",
    pulses: true,
  },
];

function badge(): HTMLElement {
  const el = document.querySelector<HTMLElement>(".connection-badge");
  expect(el).not.toBeNull();
  return el as HTMLElement;
}

function dot(): HTMLElement {
  return screen.getByTestId("status-dot");
}

beforeEach(() => {
  restoreInitialState(useGrackleClient);
});

afterEach(cleanup);

describe("ConnectionBadge", () => {
  it("starts from the client's initial status: disconnected", () => {
    render(<ConnectionBadge />);
    expect(badge()).toHaveAttribute("data-status", "disconnected");
    expect(screen.getByText("agent disconnected")).toBeInTheDocument();
  });

  for (const c of CASES) {
    it(`renders the ${c.status} state: label, data-status, dot colour, pulse`, () => {
      useGrackleClient.setState({ status: c.status });
      render(<ConnectionBadge />);

      expect(badge()).toHaveAttribute("data-status", c.status);
      expect(badge()).toHaveTextContent(c.label);
      expect(dot().style.background).toBe(c.color);
      if (c.pulses) {
        expect(dot().style.animation).toContain("badge-pulse");
      } else {
        expect(dot().style.animation).toBe("none");
      }
    });
  }

  it("gives each state a distinct label and colour", () => {
    expect(new Set(CASES.map((c) => c.label)).size).toBe(3);
    expect(new Set(CASES.map((c) => c.color)).size).toBe(3);
  });

  it("follows the store live through a connect / drop / reconnect cycle", () => {
    render(<ConnectionBadge />);
    const seen: string[] = [];
    for (const status of [
      "connecting",
      "connected",
      "disconnected",
      "connecting",
      "connected",
    ] as const) {
      act(() => {
        useGrackleClient.setState({ status });
      });
      // The label is the badge's last child (the dot carries a <style>).
      seen.push(
        `${badge().dataset.status}:${badge().lastElementChild?.textContent}`
      );
    }
    expect(seen).toEqual([
      "connecting:connecting…",
      "connected:agent connected",
      "disconnected:agent disconnected",
      "connecting:connecting…",
      "connected:agent connected",
    ]);
  });

  it("stops pulsing as soon as the connection drops", () => {
    useGrackleClient.setState({ status: "connected" });
    render(<ConnectionBadge />);
    expect(dot().style.animation).toContain("badge-pulse");

    act(() => {
      useGrackleClient.setState({ status: "disconnected" });
    });
    expect(dot().style.animation).toBe("none");
    expect(dot().style.background).toBe("var(--color-error)");
  });
});
