import { cleanup, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useGrackleClient } from "../ws/client";
import { _resetSourceCacheForTest, useSource } from "./useSource";

afterEach(() => {
  cleanup();
  _resetSourceCacheForTest();
  vi.restoreAllMocks();
});

const MOCK_SOURCE_RESPONSE = {
  id: "1",
  type: "source_response" as const,
  payload: { path: "a.py", source: "x = 1\n", encoding: "utf-8" },
};

const MOCK_SOURCE_ERROR = {
  id: "2",
  type: "source_error" as const,
  payload: { path: "missing.py", reason: "not_found" as const },
};

beforeEach(() => {
  useGrackleClient.setState({
    sendReadSource: () => Promise.resolve(MOCK_SOURCE_RESPONSE),
  });
});

describe("useSource", () => {
  it("returns idle when path is null", () => {
    const { result } = renderHook(() => useSource(null));
    expect(result.current.status).toBe("idle");
  });

  it("transitions loading → loaded on success", async () => {
    const { result } = renderHook(() => useSource("a.py"));
    expect(result.current.status).toBe("loading");
    await waitFor(() => expect(result.current.status).toBe("loaded"));
    if (result.current.status === "loaded") {
      expect(result.current.source).toBe("x = 1\n");
      expect(result.current.path).toBe("a.py");
    }
  });

  it("transitions loading → error on source_error reply", async () => {
    useGrackleClient.setState({
      sendReadSource: () => Promise.resolve(MOCK_SOURCE_ERROR),
    });
    const { result } = renderHook(() => useSource("missing.py"));
    await waitFor(() => expect(result.current.status).toBe("error"));
    if (result.current.status === "error") {
      expect(result.current.reason).toBe("not_found");
    }
  });

  it("transitions loading → error on rejection (e.g. timeout)", async () => {
    useGrackleClient.setState({
      sendReadSource: () => Promise.reject(new Error("read_source timeout")),
    });
    const { result } = renderHook(() => useSource("slow.py"));
    await waitFor(() => expect(result.current.status).toBe("error"));
    if (result.current.status === "error") {
      expect(result.current.reason).toContain("timeout");
    }
  });

  it("uses cached result on second mount for same path", async () => {
    const spy = vi.fn().mockResolvedValue(MOCK_SOURCE_RESPONSE);
    useGrackleClient.setState({ sendReadSource: spy });

    const { result: r1, unmount } = renderHook(() => useSource("a.py"));
    await waitFor(() => expect(r1.current.status).toBe("loaded"));
    unmount();

    const { result: r2 } = renderHook(() => useSource("a.py"));
    // Should be loaded immediately from cache, no new call.
    expect(r2.current.status).toBe("loaded");
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("returns idle when path changes back to null", async () => {
    const { result, rerender } = renderHook(
      ({ path }: { path: string | null }) => useSource(path),
      { initialProps: { path: "a.py" as string | null } }
    );
    await waitFor(() => expect(result.current.status).toBe("loaded"));
    rerender({ path: null });
    expect(result.current.status).toBe("idle");
  });
});
