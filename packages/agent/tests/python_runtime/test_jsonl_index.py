"""Tests for grackle.python_runtime.jsonl_index (Phase 7.3).

Design notes:
- All fixtures are written via tmp_path so tests are hermetic.
- Blank lines are tested explicitly: they must be skipped (not yield a slot).
- Out-of-range clamping must return a partial result, never raise.
- The byte-offset index is a black box here — we only test the public API
  (build / __len__ / read_window).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from grackle.python_runtime.jsonl_index import JsonlIndex

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(i: int) -> dict[str, object]:
    return {
        "event": "call",
        "node_id": f"s.py:fn_{i}",
        "ts_ns": i * 1_000_000,
        "thread_id": 1,
        "frame_depth": i % 5,
        "metadata": {},
    }


def _write_jsonl(path: Path, events: list[dict[str, object]]) -> None:
    lines = [json.dumps(ev) for ev in events]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# build + __len__
# ---------------------------------------------------------------------------


def test_build_len_empty_file(tmp_path: Path) -> None:
    """Empty file → index with 0 events."""
    f = tmp_path / "empty.jsonl"
    f.write_text("", encoding="utf-8")
    idx = JsonlIndex.build(f)
    assert len(idx) == 0


def test_build_len_single_event(tmp_path: Path) -> None:
    """Single non-blank line → index with 1 event."""
    f = tmp_path / "one.jsonl"
    _write_jsonl(f, [_make_event(0)])
    idx = JsonlIndex.build(f)
    assert len(idx) == 1


def test_build_len_multiple_events(tmp_path: Path) -> None:
    """N non-blank lines → index with N events."""
    n = 20
    f = tmp_path / "multi.jsonl"
    _write_jsonl(f, [_make_event(i) for i in range(n)])
    idx = JsonlIndex.build(f)
    assert len(idx) == n


def test_build_blank_lines_skipped(tmp_path: Path) -> None:
    """Blank lines in the file are not counted as events."""
    f = tmp_path / "blanks.jsonl"
    # Write 3 events separated by blank lines.
    lines = [
        json.dumps(_make_event(0)),
        "",
        json.dumps(_make_event(1)),
        "",
        "",
        json.dumps(_make_event(2)),
        "",
    ]
    f.write_text("\n".join(lines), encoding="utf-8")
    idx = JsonlIndex.build(f)
    assert len(idx) == 3


# ---------------------------------------------------------------------------
# read_window — basic reads
# ---------------------------------------------------------------------------


def test_read_window_full_slice(tmp_path: Path) -> None:
    """read_window(0, N) returns all N events in order."""
    n = 5
    events = [_make_event(i) for i in range(n)]
    f = tmp_path / "full.jsonl"
    _write_jsonl(f, events)
    idx = JsonlIndex.build(f)
    window = idx.read_window(0, n)
    assert len(window) == n
    for i, ev in enumerate(window):
        assert ev["node_id"] == f"s.py:fn_{i}"


def test_read_window_from_zero(tmp_path: Path) -> None:
    """read_window(0, 3) returns the first 3 events."""
    events = [_make_event(i) for i in range(10)]
    f = tmp_path / "start.jsonl"
    _write_jsonl(f, events)
    idx = JsonlIndex.build(f)
    window = idx.read_window(0, 3)
    assert len(window) == 3
    assert [ev["node_id"] for ev in window] == [
        "s.py:fn_0",
        "s.py:fn_1",
        "s.py:fn_2",
    ]


def test_read_window_from_middle(tmp_path: Path) -> None:
    """read_window(start, count) returns the correct mid-slice."""
    events = [_make_event(i) for i in range(10)]
    f = tmp_path / "mid.jsonl"
    _write_jsonl(f, events)
    idx = JsonlIndex.build(f)
    window = idx.read_window(4, 3)
    assert len(window) == 3
    assert [ev["node_id"] for ev in window] == [
        "s.py:fn_4",
        "s.py:fn_5",
        "s.py:fn_6",
    ]


def test_read_window_last_event(tmp_path: Path) -> None:
    """read_window targeting the last event returns it correctly."""
    events = [_make_event(i) for i in range(5)]
    f = tmp_path / "last.jsonl"
    _write_jsonl(f, events)
    idx = JsonlIndex.build(f)
    window = idx.read_window(4, 1)
    assert len(window) == 1
    assert window[0]["node_id"] == "s.py:fn_4"


# ---------------------------------------------------------------------------
# read_window — clamping / out-of-range behaviour
# ---------------------------------------------------------------------------


def test_read_window_start_past_end_returns_empty(tmp_path: Path) -> None:
    """start >= len → empty list, no raise."""
    events = [_make_event(i) for i in range(3)]
    f = tmp_path / "oob.jsonl"
    _write_jsonl(f, events)
    idx = JsonlIndex.build(f)
    assert idx.read_window(10, 5) == []


def test_read_window_negative_start_clamped_to_zero(tmp_path: Path) -> None:
    """Negative start is clamped to 0 — first event is returned."""
    events = [_make_event(i) for i in range(5)]
    f = tmp_path / "neg.jsonl"
    _write_jsonl(f, events)
    idx = JsonlIndex.build(f)
    window = idx.read_window(-3, 2)
    assert len(window) == 2
    assert window[0]["node_id"] == "s.py:fn_0"


def test_read_window_count_past_eof_returns_partial(tmp_path: Path) -> None:
    """count extending past the end → partial result (no raise)."""
    n = 5
    events = [_make_event(i) for i in range(n)]
    f = tmp_path / "partial.jsonl"
    _write_jsonl(f, events)
    idx = JsonlIndex.build(f)
    # Ask for 100 events starting at index 3 — only 2 exist (3 and 4).
    window = idx.read_window(3, 100)
    assert len(window) == 2
    assert [ev["node_id"] for ev in window] == ["s.py:fn_3", "s.py:fn_4"]


def test_read_window_zero_count_returns_empty(tmp_path: Path) -> None:
    """count = 0 → empty list."""
    events = [_make_event(i) for i in range(5)]
    f = tmp_path / "zero.jsonl"
    _write_jsonl(f, events)
    idx = JsonlIndex.build(f)
    assert idx.read_window(0, 0) == []


def test_read_window_empty_index_returns_empty(tmp_path: Path) -> None:
    """read_window on an empty file → empty list."""
    f = tmp_path / "empty2.jsonl"
    f.write_text("", encoding="utf-8")
    idx = JsonlIndex.build(f)
    assert idx.read_window(0, 10) == []


# ---------------------------------------------------------------------------
# read_window — blank lines in the file
# ---------------------------------------------------------------------------


def test_read_window_skips_blank_lines(tmp_path: Path) -> None:
    """Blank lines between events do not become slots; read_window returns only JSON."""
    f = tmp_path / "blanks2.jsonl"
    lines = [
        json.dumps(_make_event(10)),
        "",
        json.dumps(_make_event(20)),
        "",
        json.dumps(_make_event(30)),
    ]
    f.write_text("\n".join(lines), encoding="utf-8")
    idx = JsonlIndex.build(f)
    assert len(idx) == 3
    window = idx.read_window(0, 3)
    assert [ev["node_id"] for ev in window] == [
        "s.py:fn_10",
        "s.py:fn_20",
        "s.py:fn_30",
    ]


# ---------------------------------------------------------------------------
# read_window — data integrity
# ---------------------------------------------------------------------------


def test_read_window_preserves_metadata(tmp_path: Path) -> None:
    """All fields of each TraceEvent are preserved by round-trip."""
    events = [_make_event(i) for i in range(3)]
    events[1]["metadata"] = {"key": "value", "n": 42}
    f = tmp_path / "meta.jsonl"
    _write_jsonl(f, events)
    idx = JsonlIndex.build(f)
    window = idx.read_window(0, 3)
    assert window[1]["metadata"] == {"key": "value", "n": 42}


def test_read_window_large_file(tmp_path: Path) -> None:
    """Index handles a file with 1 000 events correctly at an arbitrary window."""
    n = 1_000
    events = [_make_event(i) for i in range(n)]
    f = tmp_path / "large.jsonl"
    _write_jsonl(f, events)
    idx = JsonlIndex.build(f)
    assert len(idx) == n
    window = idx.read_window(500, 10)
    assert len(window) == 10
    assert window[0]["node_id"] == "s.py:fn_500"
    assert window[9]["node_id"] == "s.py:fn_509"


# ---------------------------------------------------------------------------
# Multiple consecutive builds (same file) are independent
# ---------------------------------------------------------------------------


def test_two_builds_of_same_file_are_independent(tmp_path: Path) -> None:
    """Building the index twice on the same file gives two independent objects."""
    f = tmp_path / "same.jsonl"
    _write_jsonl(f, [_make_event(i) for i in range(5)])
    idx1 = JsonlIndex.build(f)
    idx2 = JsonlIndex.build(f)
    assert idx1 is not idx2
    assert len(idx1) == len(idx2) == 5
    assert idx1.read_window(0, 5) == idx2.read_window(0, 5)
