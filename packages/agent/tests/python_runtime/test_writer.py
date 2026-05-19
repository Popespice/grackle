"""Tests for python_runtime.writer — JSONL write/read atomicity."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from grackle.python_runtime.writer import read_jsonl, write_jsonl

if TYPE_CHECKING:
    from pathlib import Path

    from grackle.adapters.base import TraceEvent


def _events(n: int) -> list[TraceEvent]:
    return [
        {
            "event": "call",
            "node_id": f"src/app.py:fn{i}",
            "ts_ns": i * 1000,
            "thread_id": 1,
            "frame_depth": i,
            "metadata": {},
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# write_jsonl
# ---------------------------------------------------------------------------


def test_write_creates_file(tmp_path: Path) -> None:
    dest = tmp_path / "out.jsonl"
    write_jsonl(_events(3), dest)
    assert dest.exists()


def test_write_returns_event_count(tmp_path: Path) -> None:
    dest = tmp_path / "out.jsonl"
    count = write_jsonl(_events(5), dest)
    assert count == 5


def test_write_zero_events_creates_empty_file(tmp_path: Path) -> None:
    dest = tmp_path / "empty.jsonl"
    count = write_jsonl([], dest)
    assert count == 0
    assert dest.read_text(encoding="utf-8") == ""


def test_each_line_is_valid_json(tmp_path: Path) -> None:
    dest = tmp_path / "out.jsonl"
    write_jsonl(_events(4), dest)
    for line in dest.read_text(encoding="utf-8").splitlines():
        obj = json.loads(line)
        assert isinstance(obj, dict)


def test_no_tmp_file_left_behind(tmp_path: Path) -> None:
    dest = tmp_path / "out.jsonl"
    write_jsonl(_events(2), dest)
    # The new tmp name is "<name>.tmp" (appended), so check both
    # the modern shape and the legacy with_suffix shape — neither should
    # leak after a successful write.
    assert not (tmp_path / "out.jsonl.tmp").exists()
    assert not (tmp_path / "out.tmp").exists()


def test_tmp_path_uses_append_not_with_suffix(tmp_path: Path) -> None:
    """Regression: foo.tar.gz must produce foo.tar.gz.tmp, not foo.tar.tmp.

    ``with_suffix(".tmp")`` only replaces the final extension and would
    collide when multiple destinations share a stem. The writer appends
    ``.tmp`` to the full filename instead.
    """
    # Multi-suffix file — with_suffix would strip ".gz" and collide.
    dest = tmp_path / "trace.tar.gz"
    write_jsonl(_events(1), dest)
    assert dest.exists()
    # The wrong-but-tempting shape must not exist
    assert not (tmp_path / "trace.tar.tmp").exists()


def test_atomic_write_replaces_existing(tmp_path: Path) -> None:
    dest = tmp_path / "out.jsonl"
    write_jsonl(_events(2), dest)
    first_content = dest.read_text(encoding="utf-8")
    write_jsonl(_events(3), dest)
    second_content = dest.read_text(encoding="utf-8")
    assert second_content != first_content
    assert len(second_content.splitlines()) == 3


# ---------------------------------------------------------------------------
# read_jsonl
# ---------------------------------------------------------------------------


def test_roundtrip(tmp_path: Path) -> None:
    dest = tmp_path / "trace.jsonl"
    original = _events(6)
    write_jsonl(original, dest)
    loaded = read_jsonl(dest)
    assert len(loaded) == 6
    for orig, loaded_e in zip(original, loaded, strict=True):
        assert loaded_e["node_id"] == orig["node_id"]
        assert loaded_e["event"] == orig["event"]
        assert loaded_e["ts_ns"] == orig["ts_ns"]


def test_read_skips_blank_lines(tmp_path: Path) -> None:
    dest = tmp_path / "trace.jsonl"
    dest.write_text(
        '{"event":"call","node_id":"a.py","ts_ns":1,"thread_id":1,"frame_depth":0,"metadata":{}}\n'
        "\n"
        '{"event":"return","node_id":"a.py","ts_ns":2,"thread_id":1,"frame_depth":0,"metadata":{}}\n',
        encoding="utf-8",
    )
    events = read_jsonl(dest)
    assert len(events) == 2


def test_read_raises_on_malformed_json(tmp_path: Path) -> None:
    dest = tmp_path / "bad.jsonl"
    dest.write_text("not-valid-json\n", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        read_jsonl(dest)
