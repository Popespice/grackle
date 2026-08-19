import { describe, expect, it } from "vitest";
import { lastAtOrBefore, lastIndexAtOrBefore } from "./playheadLookup";

const KEY = (p: { eventIndex: number }) => p.eventIndex;
const SERIES = [{ eventIndex: 5 }, { eventIndex: 10 }, { eventIndex: 20 }];

describe("lastIndexAtOrBefore", () => {
  it("returns -1 before the first key", () => {
    expect(lastIndexAtOrBefore(SERIES, 4, KEY)).toBe(-1);
  });

  it("INCLUDES an exact key match", () => {
    expect(lastIndexAtOrBefore(SERIES, 5, KEY)).toBe(0);
    expect(lastIndexAtOrBefore(SERIES, 10, KEY)).toBe(1);
    expect(lastIndexAtOrBefore(SERIES, 20, KEY)).toBe(2);
  });

  it("holds the last item past the end", () => {
    expect(lastIndexAtOrBefore(SERIES, 1000, KEY)).toBe(2);
  });

  it("returns -1 for an empty series", () => {
    expect(lastIndexAtOrBefore([], 100, KEY)).toBe(-1);
  });

  it("agrees with a linear scan at every playhead", () => {
    for (let playhead = 0; playhead <= 25; playhead++) {
      let expected = -1;
      SERIES.forEach((p, i) => {
        if (p.eventIndex <= playhead) expected = i;
      });
      expect(lastIndexAtOrBefore(SERIES, playhead, KEY)).toBe(expected);
    }
  });
});

describe("lastAtOrBefore", () => {
  it("returns the item, or null when nothing has been reached", () => {
    expect(lastAtOrBefore(SERIES, 4, KEY)).toBeNull();
    expect(lastAtOrBefore(SERIES, 15, KEY)).toEqual({ eventIndex: 10 });
  });
});
