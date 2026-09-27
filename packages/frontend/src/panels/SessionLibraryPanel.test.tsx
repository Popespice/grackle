/**
 * SessionLibraryPanel — the stored-session list (Phase 8.3, ADR-0020)
 * (campaign T11-6, `docs/test-campaigns/phase-12.md`). No test for it existed
 * anywhere.
 *
 * The client store is the seam: `status`, `requestSessionList` and
 * `sendSessionLoad` are replaced per test after a full reset (T11-4's
 * `restoreInitialState`), so no stubbed action outlives its test.
 */
import type { SessionListResponse, SessionMeta } from "@grackle/shared-types";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { restoreInitialState } from "../test/storeReset";
import { type ConnectionStatus, useGrackleClient } from "../ws/client";
import { SessionLibraryPanel } from "./SessionLibraryPanel";

const EMPTY_TEXT =
  "No stored sessions. Start the server with --store to save sessions.";
const TIMEOUT_MESSAGE = "session_list_request timed out";

function meta(id: string, label: string, eventCount: number): SessionMeta {
  return {
    id,
    label,
    started_ns: 1,
    ended_ns: 2,
    source_path: `/tmp/${id}.jsonl`,
    event_count: eventCount,
    language: "python",
  };
}

const ALPHA = meta("sess-alpha", "alpha run", 1234);
const BETA = meta("sess-beta", "beta run", 7);

function listResponse(sessions: SessionMeta[]): SessionListResponse {
  return {
    id: "r1",
    type: "session_list_response",
    payload: { sessions },
  };
}

function stubClient(
  status: ConnectionStatus,
  list: () => Promise<SessionListResponse>
) {
  const requestSessionList = vi.fn(list);
  const sendSessionLoad = vi.fn();
  useGrackleClient.setState({ status, requestSessionList, sendSessionLoad });
  return { requestSessionList, sendSessionLoad };
}

function setStatus(status: ConnectionStatus): void {
  act(() => {
    useGrackleClient.setState({ status });
  });
}

beforeEach(() => {
  restoreInitialState(useGrackleClient);
});

afterEach(cleanup);

describe("SessionLibraryPanel — requesting the list", () => {
  it("requests nothing while disconnected or connecting, and shows the empty state", () => {
    const { requestSessionList } = stubClient("disconnected", () =>
      Promise.resolve(listResponse([ALPHA]))
    );
    render(<SessionLibraryPanel />);
    setStatus("connecting");

    expect(requestSessionList).not.toHaveBeenCalled();
    expect(screen.getByText(EMPTY_TEXT)).toBeInTheDocument();
  });

  it("requests the list once when the connection opens", async () => {
    const { requestSessionList } = stubClient("connecting", () =>
      Promise.resolve(listResponse([ALPHA]))
    );
    render(<SessionLibraryPanel />);
    expect(requestSessionList).not.toHaveBeenCalled();

    setStatus("connected");
    await screen.findByText("alpha run");
    expect(requestSessionList).toHaveBeenCalledTimes(1);
  });

  it("requests on mount when already connected", async () => {
    const { requestSessionList } = stubClient("connected", () =>
      Promise.resolve(listResponse([ALPHA]))
    );
    render(<SessionLibraryPanel />);
    await screen.findByText("alpha run");
    expect(requestSessionList).toHaveBeenCalledTimes(1);
  });

  it("requests again after a reconnect, and not while the socket is down", async () => {
    const { requestSessionList } = stubClient("connected", () =>
      Promise.resolve(listResponse([ALPHA]))
    );
    render(<SessionLibraryPanel />);
    await screen.findByText("alpha run");

    setStatus("disconnected");
    expect(requestSessionList).toHaveBeenCalledTimes(1);
    setStatus("connected");
    await waitFor(() => expect(requestSessionList).toHaveBeenCalledTimes(2));
  });
});

describe("SessionLibraryPanel — rendering sessions", () => {
  it("lists each session's label, event count and language, with a count header", async () => {
    stubClient("connected", () => Promise.resolve(listResponse([ALPHA, BETA])));
    render(<SessionLibraryPanel />);

    await screen.findByText("alpha run");
    expect(screen.getByText("beta run")).toBeInTheDocument();
    expect(screen.getByText("2 sessions")).toBeInTheDocument();
    // toLocaleString, so the expectation follows the runner's locale.
    const alphaCount = `${(1234).toLocaleString()} events · python`;
    expect(screen.getByText(alphaCount)).toBeInTheDocument();
    expect(screen.getByText("7 events · python")).toBeInTheDocument();
    expect(screen.queryByText(EMPTY_TEXT)).toBeNull();
  });

  it("uses the singular for one session", async () => {
    stubClient("connected", () => Promise.resolve(listResponse([BETA])));
    render(<SessionLibraryPanel />);
    await screen.findByText("1 session");
  });

  it("shows the empty state for an empty list (a server without --store)", async () => {
    const { requestSessionList } = stubClient("connected", () =>
      Promise.resolve(listResponse([]))
    );
    render(<SessionLibraryPanel />);
    await waitFor(() => expect(requestSessionList).toHaveBeenCalled());
    expect(await screen.findByText(EMPTY_TEXT)).toBeInTheDocument();
  });

  it("shows a loading header, not the empty state, while the first request is in flight", () => {
    stubClient("connected", () => new Promise(() => {}));
    render(<SessionLibraryPanel />);
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(screen.queryByText(EMPTY_TEXT)).toBeNull();
  });
});

describe("SessionLibraryPanel — loading a session", () => {
  it("sends a load request for the clicked session's id", async () => {
    const { sendSessionLoad } = stubClient("connected", () =>
      Promise.resolve(listResponse([ALPHA, BETA]))
    );
    render(<SessionLibraryPanel />);

    fireEvent.click(await screen.findByRole("button", { name: /beta run/ }));
    expect(sendSessionLoad).toHaveBeenCalledTimes(1);
    expect(sendSessionLoad).toHaveBeenCalledWith("sess-beta");
  });

  it("Refresh re-requests and replaces the list", async () => {
    let sessions = [ALPHA];
    const { requestSessionList } = stubClient("connected", () =>
      Promise.resolve(listResponse(sessions))
    );
    render(<SessionLibraryPanel />);
    await screen.findByText("alpha run");

    sessions = [BETA];
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByText("beta run");
    expect(screen.queryByText("alpha run")).toBeNull();
    expect(requestSessionList).toHaveBeenCalledTimes(2);
  });
});

describe("SessionLibraryPanel — errors", () => {
  it("shows the failure when the first request fails (control)", async () => {
    stubClient("connected", () => Promise.reject(new Error(TIMEOUT_MESSAGE)));
    render(<SessionLibraryPanel />);
    expect(
      await screen.findByText(new RegExp(TIMEOUT_MESSAGE))
    ).toBeInTheDocument();
    expect(screen.queryByText(EMPTY_TEXT)).toBeNull();
  });

  // KNOWN DEFECT (T11-6, docs/test-campaigns/phase-12.md): the panel renders
  // `Error: ${String(e)}`, and String(new Error(m)) is already "Error: m", so
  // every failure reads "Error: Error: session_list_request timed out".
  // Remove `.fails` in the PR that renders the message once.
  it.fails("names the failure once, without a doubled 'Error:' prefix", async () => {
    stubClient("connected", () => Promise.reject(new Error(TIMEOUT_MESSAGE)));
    render(<SessionLibraryPanel />);
    await screen.findByText(new RegExp(TIMEOUT_MESSAGE));
    expect(screen.getByText(`Error: ${TIMEOUT_MESSAGE}`)).toBeInTheDocument();
  });

  // KNOWN DEFECT (T11-6, docs/test-campaigns/phase-12.md): the Refresh button
  // exists only in the populated branch. After a failed first request (the
  // client's 5 s timeout) the panel shows the error and nothing to act on —
  // the only retry is a reconnect or a page reload. Remove `.fails` in the PR
  // that offers a retry from the error state.
  it.fails("offers a retry after a failed request", async () => {
    stubClient("connected", () => Promise.reject(new Error(TIMEOUT_MESSAGE)));
    render(<SessionLibraryPanel />);
    await screen.findByText(new RegExp(TIMEOUT_MESSAGE));
    expect(
      screen.getByRole("button", { name: /refresh|retry/i })
    ).toBeInTheDocument();
  });

  // KNOWN DEFECT (T11-6, docs/test-campaigns/phase-12.md): `error` is only
  // rendered by the empty-list branch. A Refresh that fails while sessions
  // are listed sets it and shows nothing: the stale list stays up and reads
  // as current. Remove `.fails` in the PR that surfaces the error there too.
  it.fails("shows a failed Refresh while sessions are listed", async () => {
    let fail = false;
    stubClient("connected", () =>
      fail
        ? Promise.reject(new Error(TIMEOUT_MESSAGE))
        : Promise.resolve(listResponse([ALPHA]))
    );
    render(<SessionLibraryPanel />);
    await screen.findByText("alpha run");

    fail = true;
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByText("1 session"); // the request has settled
    expect(screen.getByText(new RegExp(TIMEOUT_MESSAGE))).toBeInTheDocument();
  });

  it("a failed Refresh while sessions are listed settles back to the list (control)", async () => {
    // The ledgered test above waits on this settling; pinned separately so
    // that test can only fail on its final assertion.
    let fail = false;
    const { requestSessionList } = stubClient("connected", () =>
      fail
        ? Promise.reject(new Error(TIMEOUT_MESSAGE))
        : Promise.resolve(listResponse([ALPHA]))
    );
    render(<SessionLibraryPanel />);
    await screen.findByText("alpha run");

    fail = true;
    fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByText("1 session");
    expect(requestSessionList).toHaveBeenCalledTimes(2);
    expect(screen.getByText("alpha run")).toBeInTheDocument();
  });

  it("a successful Refresh clears an earlier error", async () => {
    let fail = true;
    stubClient("connected", () =>
      fail
        ? Promise.reject(new Error(TIMEOUT_MESSAGE))
        : Promise.resolve(listResponse([ALPHA]))
    );
    render(<SessionLibraryPanel />);
    await screen.findByText(new RegExp(TIMEOUT_MESSAGE));

    // No Refresh in the error state (ledgered above): a reconnect re-requests.
    fail = false;
    setStatus("disconnected");
    setStatus("connected");
    await screen.findByText("alpha run");
    expect(screen.queryByText(new RegExp(TIMEOUT_MESSAGE))).toBeNull();
  });
});
