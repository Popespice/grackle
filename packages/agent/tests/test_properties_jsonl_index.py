"""Campaign T8-3 (docs/test-campaigns/phase-12.md): ``JsonlIndex`` vs
``read_jsonl``, differentially.

The same file goes through both readers: the byte-level seek index
(``JsonlIndex.build`` / ``build_seekable`` + ``read_window``, used by
``serve --trace-source`` seek and session load) and the whole-file strict
reader (``read_jsonl``, used by non-seekable replay and ``trace --connect``).
Every generated string is drawn with extra weight on the characters that sit
between the two readers' notions of a line: U+0085 / U+2028 / U+2029 (line
boundaries to ``str.splitlines()`` but not to either reader — the hazard
``read_jsonl``'s docstring documents), the ASCII separators U+001C–U+001F,
VT/FF, NBSP, U+3000, BOM, raw CR/LF, quotes and backslashes.

Properties: both readers return exactly the written events; every partition
of the index into windows concatenates back to them; absurd windows
(negative, zero, huge) never raise and clamp as documented; and a
``write_jsonl`` file read back window by window and re-written is
byte-identical.

The generator never pads a line with VT/FF (ledgered, F-11 #3) or puts a raw
CR between JSON tokens (ledgered, F-11 #4). One new disagreement is ledgered
at the bottom (finding T8-3): the readers disagree on what a *blank* line is.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from grackle.python_runtime.aggregates import build_seekable
from grackle.python_runtime.jsonl_index import JsonlIndex
from grackle.python_runtime.writer import read_jsonl, write_jsonl

if TYPE_CHECKING:
    from pathlib import Path

_LEDGER = "(docs/test-campaigns/phase-12.md)"

_HAZARDS = '\u0085  \x1c\x1d\x1e\x1f\x0b\x0c\r\n\t  　﻿"\\/'
hazard_text = st.text(
    alphabet=st.one_of(
        st.sampled_from(_HAZARDS),
        st.characters(codec="utf-8"),  # no lone surrogates: they cannot be UTF-8
    ),
    max_size=12,
)
_json_scalar = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(-(2**70), 2**70),
    st.floats(allow_nan=False),  # NaN != NaN would defeat list equality, not the readers
    hazard_text,
)
json_value = st.recursive(
    _json_scalar,
    lambda ch: st.one_of(st.lists(ch, max_size=3), st.dictionaries(hazard_text, ch, max_size=3)),
    max_leaves=8,
)
events_st = st.fixed_dictionaries(
    {
        "event": st.sampled_from(["call", "return", "exception"]),
        "node_id": hazard_text,
        "ts_ns": st.integers(0, 2**63),
    },
    optional={"metadata": st.dictionaries(hazard_text, json_value, max_size=3)},
)

# A file is a list of items: an event (with its own escaping choice and
# JSON-whitespace padding) or a blank line of ASCII whitespace.
_json_ws = st.text(alphabet=" \t", max_size=2)
_event_item = st.tuples(st.just("event"), events_st, st.booleans(), _json_ws, _json_ws)
_blank_item = st.tuples(st.just("blank"), st.text(alphabet=" \t", max_size=3))
items_st = st.lists(st.one_of(_event_item, _blank_item), max_size=25)


def _encode_item(item: tuple[Any, ...]) -> bytes:
    if item[0] == "blank":
        return str(item[1]).encode()
    _, event, ascii_only, left, right = item
    body = json.dumps(event, ensure_ascii=ascii_only)
    return f"{left}{body}{right}".encode()


def _write_items(path: Path, items: list[tuple[Any, ...]], crlf: bool, final: bool) -> list[Any]:
    sep = b"\r\n" if crlf else b"\n"
    body = sep.join(_encode_item(item) for item in items)
    if items and final:
        body += sep
    path.write_bytes(body)
    return [item[1] for item in items if item[0] == "event"]


@pytest.fixture(scope="module")
def trace_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("t8_3") / "trace.jsonl"


# ===========================================================================
# Properties that hold
# ===========================================================================


@given(items=items_st, crlf=st.booleans(), final=st.booleans())
def test_both_readers_return_exactly_the_written_events(
    trace_path: Path, items: list[tuple[Any, ...]], crlf: bool, final: bool
) -> None:
    events = _write_items(trace_path, items, crlf, final)
    assert read_jsonl(trace_path) == events
    for index in (JsonlIndex.build(trace_path), build_seekable(trace_path)[0]):
        assert len(index) == len(events)
        assert index.read_window(0, len(index)) == events


@given(
    items=items_st,
    crlf=st.booleans(),
    cuts=st.lists(st.integers(0, 25), max_size=6),
)
def test_windows_concatenate_back_to_the_whole_trace(
    trace_path: Path, items: list[tuple[Any, ...]], crlf: bool, cuts: list[int]
) -> None:
    events = _write_items(trace_path, items, crlf, final=True)
    index = JsonlIndex.build(trace_path)
    bounds = sorted({0, len(events), *(c for c in cuts if c <= len(events))})
    pieces: list[Any] = []
    for lo, hi in zip(bounds, bounds[1:], strict=False):
        pieces.extend(index.read_window(lo, hi - lo))
    assert pieces == events


_absurd = st.one_of(
    st.integers(-5, 30),
    st.sampled_from([-(2**63), -(2**31), 2**31, 2**63, 10**30]),
    st.integers(),
)


@given(items=items_st, start=_absurd, count=_absurd)
def test_absurd_windows_never_raise_and_clamp(
    trace_path: Path, items: list[tuple[Any, ...]], start: int, count: int
) -> None:
    """``start`` clamps into [0, len]; the window then runs ``count`` events
    from there, cut at the end; a non-positive span is empty."""
    events = _write_items(trace_path, items, crlf=False, final=True)
    index = JsonlIndex.build(trace_path)
    lo = max(0, min(start, len(events)))
    hi = min(lo + count, len(events))
    assert index.read_window(start, count) == (events[lo:hi] if lo < hi else [])


@given(events=st.lists(events_st, max_size=20), cuts=st.lists(st.integers(0, 20), max_size=4))
def test_write_jsonl_round_trips_byte_for_byte_through_the_index(
    tmp_path_factory: pytest.TempPathFactory, events: list[Any], cuts: list[int]
) -> None:
    base = tmp_path_factory.getbasetemp()
    first, second = base / "t8_3_a.jsonl", base / "t8_3_b.jsonl"
    assert write_jsonl(events, first) == len(events)
    assert read_jsonl(first) == events
    index = JsonlIndex.build(first)
    bounds = sorted({0, len(events), *(c for c in cuts if c <= len(events))})
    windows: list[Any] = []
    for lo, hi in zip(bounds, bounds[1:], strict=False):
        windows.extend(index.read_window(lo, hi - lo))
    write_jsonl(windows, second)
    assert second.read_bytes() == first.read_bytes()


# ===========================================================================
# Finding T8-3 (ledgered): the readers disagree on what a blank line is
# ===========================================================================

# Characters str.strip() removes but bytes.strip() keeps (and JSON does not
# accept as whitespace): Unicode whitespace plus the four ASCII separators.
_STR_ONLY_WS = "\x1c\x1d\x1e\x1f\x85\xa0                　"
_str_only_ws = st.text(alphabet=_STR_ONLY_WS, min_size=1, max_size=3)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T8-3: read_jsonl strips each line with str.strip(), the byte readers "
        "with bytes.strip(), so a line that is (or is padded with) Unicode "
        "whitespace or U+001C-U+001F is blank/an event to read_jsonl but a "
        "malformed slot to JsonlIndex/build_seekable/read_window — the two "
        f"replay paths of one file disagree on its event count {_LEDGER}"
    ),
)
@given(
    items=st.lists(
        st.one_of(
            st.tuples(st.just("event"), events_st, st.booleans(), _str_only_ws, st.just("")),
            st.tuples(st.just("blank"), _str_only_ws),
            _event_item,
        ),
        max_size=10,
    )
)
@example(items=[("blank", "\x85")])  # an extra slot: event count N+1
@example(items=[("event", {"event": "call", "node_id": "", "ts_ns": 0}, False, "\x85", "")])
def test_readers_agree_on_which_lines_are_events(
    trace_path: Path, items: list[tuple[Any, ...]]
) -> None:
    sep = b"\n"
    trace_path.write_bytes(sep.join(_encode_item(item) for item in items) + sep)
    strict = read_jsonl(trace_path)
    index = JsonlIndex.build(trace_path)
    assert len(index) == len(strict)
    assert index.read_window(0, len(index)) == strict
