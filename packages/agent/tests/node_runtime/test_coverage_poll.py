"""Tests for the pure precise-coverage helpers (ADR-0022)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

from grackle.node_runtime.coverage_poll import (
    CoverageDelta,
    CoverageKey,
    OffsetLineMap,
    coverage_event,
    iter_coverage_deltas,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


# ---------------------------------------------------------------------------
# Reference decomposition (oracle for iter_coverage_deltas equivalence tests)
# normalize_precise_coverage + diff_coverage were removed from the production
# module (they are dead in production); they live here as the test oracle only.
# ---------------------------------------------------------------------------


class CoverageEntry(TypedDict):
    url: str
    function_name: str
    start_offset: int
    count: int


def normalize_precise_coverage(
    result: Iterable[Mapping[str, Any]],
) -> dict[CoverageKey, CoverageEntry]:
    from grackle.node_runtime.coverage_poll import _as_int

    out: dict[CoverageKey, CoverageEntry] = {}
    for script in result:
        script_id = str(script.get("scriptId", ""))
        url = str(script.get("url", ""))
        for fn in script.get("functions") or []:
            ranges = fn.get("ranges") or []
            if not ranges:
                continue
            head = ranges[0]
            if not isinstance(head, dict):
                continue
            start_offset = _as_int(head.get("startOffset"))
            count = _as_int(head.get("count"))
            out[(script_id, start_offset)] = {
                "url": url,
                "function_name": str(fn.get("functionName", "")),
                "start_offset": start_offset,
                "count": count,
            }
    return out


def diff_coverage(
    prev: Mapping[CoverageKey, CoverageEntry],
    curr: Mapping[CoverageKey, CoverageEntry],
) -> list[CoverageDelta]:
    deltas: list[CoverageDelta] = []
    for (script_id, start_offset), entry in curr.items():
        before = prev.get((script_id, start_offset))
        delta = entry["count"] - (before["count"] if before is not None else 0)
        if delta > 0:
            deltas.append(
                {
                    "script_id": script_id,
                    "url": entry["url"],
                    "function_name": entry["function_name"],
                    "start_offset": start_offset,
                    "delta": delta,
                }
            )
    return deltas


def test_offset_line_map_basic() -> None:
    # Lines: "abc\n" (0..3), "de\n" (4..6), "f" (7)
    src = "abc\nde\nf"
    line_map = OffsetLineMap(src)
    assert line_map.line_of(0) == 1
    assert line_map.line_of(3) == 1  # the '\n' itself is still on line 1
    assert line_map.line_of(4) == 2  # start of line 2
    assert line_map.line_of(5) == 2
    assert line_map.line_of(7) == 3
    assert line_map.line_of(9999) == 3  # past end clamps to last line


def test_offset_line_map_empty_source() -> None:
    line_map = OffsetLineMap("")
    assert line_map.line_of(0) == 1


def test_normalize_takes_function_level_range() -> None:
    result = [
        {
            "scriptId": "7",
            "url": "file:///proj/src/m.ts",
            "functions": [
                {
                    "functionName": "fib",
                    "ranges": [
                        {"startOffset": 100, "endOffset": 200, "count": 5},
                        {"startOffset": 120, "endOffset": 150, "count": 3},  # block range
                    ],
                }
            ],
        }
    ]
    norm = normalize_precise_coverage(result)
    assert norm == {
        ("7", 100): {
            "url": "file:///proj/src/m.ts",
            "function_name": "fib",
            "start_offset": 100,
            "count": 5,
        }
    }


def test_normalize_skips_functions_without_ranges() -> None:
    result = [{"scriptId": "1", "url": "u", "functions": [{"functionName": "x", "ranges": []}]}]
    assert normalize_precise_coverage(result) == {}


def test_normalize_tolerates_non_int_and_malformed_fields() -> None:
    # Finding #4: a null/non-int startOffset or count (or a non-dict range head)
    # must NOT raise — a TypeError here is not a CDPError and would abort the whole
    # --stream session. Bad numerics coerce to 0; malformed heads are skipped.
    result: list[dict[str, object]] = [
        {
            "scriptId": "7",
            "url": "u",
            "functions": [
                {"functionName": "a", "ranges": [{"startOffset": None, "count": None}]},
                {"functionName": "b", "ranges": [{"startOffset": 10, "count": "x"}]},
                {"functionName": "c", "ranges": ["not-a-dict"]},
                {"functionName": "d", "ranges": [{"startOffset": 20, "count": 3}]},
            ],
        }
    ]
    norm = normalize_precise_coverage(result)  # must not raise
    assert norm[("7", 0)]["count"] == 0  # 'a': null offset/count → 0
    assert norm[("7", 10)]["count"] == 0  # 'b': non-int count → 0
    assert ("7", 20) in norm  # 'd': valid, kept
    assert norm[("7", 20)]["count"] == 3
    # 'c' (non-dict range head) is the only function that produced no entry.
    assert len(norm) == 3


def test_diff_only_positive_deltas() -> None:
    prev: dict[CoverageKey, CoverageEntry] = {
        ("7", 100): {"url": "u", "function_name": "f", "start_offset": 100, "count": 2}
    }
    curr: dict[CoverageKey, CoverageEntry] = {
        ("7", 100): {"url": "u", "function_name": "f", "start_offset": 100, "count": 5},
        ("7", 300): {"url": "u", "function_name": "g", "start_offset": 300, "count": 1},
    }
    deltas = diff_coverage(prev, curr)
    by_offset = {d["start_offset"]: d["delta"] for d in deltas}
    assert by_offset == {100: 3, 300: 1}  # f: 5-2=3 (new), g: 1-0=1 (first seen)


def test_diff_ignores_unchanged_and_gone() -> None:
    prev: dict[CoverageKey, CoverageEntry] = {
        ("7", 100): {"url": "u", "function_name": "f", "start_offset": 100, "count": 5},
        ("7", 300): {"url": "u", "function_name": "g", "start_offset": 300, "count": 9},
    }
    curr: dict[CoverageKey, CoverageEntry] = {
        ("7", 100): {"url": "u", "function_name": "f", "start_offset": 100, "count": 5},  # same
    }
    assert diff_coverage(prev, curr) == []


def test_coverage_event_shape() -> None:
    event = coverage_event("src/m.ts:fib", 42, 1234)
    assert event == {
        "event": "call",
        "node_id": "src/m.ts:fib",
        "ts_ns": 1234,
        "thread_id": 0,
        "frame_depth": 0,
        "metadata": {"live": True, "count": 42},
    }


def test_iter_coverage_deltas_matches_normalize_then_diff() -> None:
    """The fused one-pass form is byte-equivalent to diff_coverage(prev, normalize(r))
    for the deltas, and its returned counts equal normalize(r)'s per-key counts."""
    r1 = [
        {
            "scriptId": "7",
            "url": "u",
            "functions": [
                {"functionName": "f", "ranges": [{"startOffset": 100, "count": 2}]},
                {"functionName": "g", "ranges": [{"startOffset": 300, "count": 0}]},
            ],
        },
    ]
    r2 = [
        {
            "scriptId": "7",
            "url": "u",
            "functions": [
                {"functionName": "f", "ranges": [{"startOffset": 100, "count": 5}]},
                {"functionName": "g", "ranges": [{"startOffset": 300, "count": 1}]},
            ],
        },
    ]
    # First poll: prev empty.
    norm1 = normalize_precise_coverage(r1)
    new_d1, counts1 = iter_coverage_deltas(r1, {})
    assert new_d1 == diff_coverage({}, norm1)
    assert counts1 == {k: v["count"] for k, v in norm1.items()}
    # Second poll: prev = first counts.
    norm2 = normalize_precise_coverage(r2)
    new_d2, counts2 = iter_coverage_deltas(r2, counts1)
    assert new_d2 == diff_coverage(norm1, norm2)  # diff_coverage reads ["count"]
    assert counts2 == {k: v["count"] for k, v in norm2.items()}


def test_iter_coverage_deltas_tolerates_malformed() -> None:
    r = [
        {
            "scriptId": "7",
            "url": "u",
            "functions": [
                {"functionName": "a", "ranges": [{"startOffset": None, "count": None}]},
                {"functionName": "c", "ranges": ["not-a-dict"]},
                {"functionName": "d", "ranges": [{"startOffset": 20, "count": 3}]},
            ],
        }
    ]
    deltas, counts = iter_coverage_deltas(r, {})  # must not raise
    by_off = {d["start_offset"]: d["delta"] for d in deltas}
    assert by_off == {20: 3}  # 'a' coerces to count 0 → delta 0 (not surfaced)
    assert counts == {("7", 0): 0, ("7", 20): 3}
