"""Tests for the pure precise-coverage helpers (ADR-0022)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from grackle.node_runtime.coverage_poll import (
    OffsetLineMap,
    coverage_event,
    diff_coverage,
    normalize_precise_coverage,
)

if TYPE_CHECKING:
    from grackle.node_runtime.coverage_poll import CoverageEntry, CoverageKey


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
