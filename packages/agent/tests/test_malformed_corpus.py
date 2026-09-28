"""Test campaign T6-5 (docs/test-campaigns/phase-12.md): the malformed-corpus sweep.

A seeded, deterministic adversarial-input generator (the hand-written
``_EIGHT_LINES`` template of ``packages/nn/tests/ml/test_labels.py`` grown into
a corpus, and kept here as code in the ``fixtures/stress-2k/generate.py``
tradition, so every run sees the same bytes), driven through every reader of
untrusted input the agent has:

- the trace readers: ``read_jsonl``, which is whole-file strict by contract and
  so may only raise cleanly; and the per-line-tolerant salvage readers behind
  ``grackle diff``/``learn``, ``serve --trace-source`` and session load:
  ``JsonlIndex.build`` + ``read_window``, ``TraceAggregates.build`` and
  ``build_seekable``, plus a cross-check against the ``grackle-nn`` mirror
  ``heat_from_jsonl`` wherever both are defined;
- the three external-tool output parsers, whose end-to-end tests are
  toolchain-gated off most CI legs: Go ``covdata textfmt`` (``parse_textfmt``),
  ``llvm-cov export`` JSON (``parse_export``), and V8's CPU profile
  (``reconstruct``) and precise coverage (``iter_coverage_deltas``). Each is
  followed through its real resolver to the node ids it would emit.

A probe fails if a parser raises where its contract says it tolerates, fails to
return within a time bound, or emits a node id the static graph does not have,
in particular one containing ``\\`` or ``..``. The trace readers never mint node
ids (they report the file's own), so for them the check is stronger: every node
id is reported verbatim or not at all.

Every corpus line kind the readers handle correctly is in the main sweep, which
must pass. A kind that exposes a defect is kept out of it and ledgered as a
strict xfail below. The two defects already ledgered are referenced, not
duplicated: T5-7 (a torn, unterminated final line gets an event slot; this
generator terminates every line it tears) and T5-8 (a non-object JSON line
crashes the aggregate builders; this generator emits none, and the nn mirror,
which handles them, sweeps them in
``packages/nn/tests/ml/test_malformed_corpus.py``).
"""

from __future__ import annotations

import json
import math
import os
import random
import shutil
import sys
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NamedTuple, cast

import pytest

from grackle.adapters.runtime_resolution import UNRESOLVED
from grackle.go_runtime.covdata_parse import parse_textfmt
from grackle.go_runtime.resolution import GoResolver
from grackle.node_runtime.coverage_poll import iter_coverage_deltas
from grackle.node_runtime.launcher import (
    _line_map_for_url,
    _make_resolve,
    _resolve_coverage_delta,
)
from grackle.node_runtime.node_resolution import NodeResolver
from grackle.node_runtime.profile_reconstruct import reconstruct
from grackle.python_runtime.aggregates import TraceAggregates, build_seekable
from grackle.python_runtime.jsonl_index import JsonlIndex
from grackle.python_runtime.writer import read_jsonl
from grackle.rust_runtime.llvm_cov_parse import parse_export
from grackle.rust_runtime.resolution import RustResolver

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from grackle.adapters.base import StaticGraph, TraceEvent
    from grackle.node_runtime.coverage_poll import CoverageDelta

# Generous: every call below finishes in well under a second. The bound exists
# so a hang fails the test instead of stalling the suite.
_BOUND_S = 30.0

_LEDGER = "(docs/test-campaigns/phase-12.md)"


def _bounded[T](fn: Callable[[], T], timeout: float = _BOUND_S) -> T:
    """Run *fn* on a daemon thread and return its result, re-raising its error.

    Fails the test if *fn* has not returned within *timeout* seconds. A hung
    worker is abandoned (a daemon thread dies with the process).
    """
    outcome: list[tuple[bool, Any]] = []

    def target() -> None:
        try:
            outcome.append((True, fn()))
        except BaseException as exc:  # handed back to the test thread below
            outcome.append((False, exc))

    worker = threading.Thread(target=target, name="t6-5-bounded", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        pytest.fail(f"parser did not return within {timeout}s")
    ok, value = outcome[0]
    if not ok:
        raise value
    return cast("T", value)


@pytest.fixture
def default_int_digit_limit() -> Iterator[int]:
    """Pin CPython's default int-to-str digit limit (4300) for one test, so a
    ``PYTHONINTMAXSTRDIGITS`` override cannot hide the over-long-number trigger."""
    previous = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(4300)
    try:
        yield 4300
    finally:
        sys.set_int_max_str_digits(previous)


def _assert_safe_node_id(node_id: str | None, graph_ids: frozenset[str]) -> None:
    """A resolver may filter a frame (None) or mark it unresolved, but it may
    only ever emit a node id the static graph has."""
    if node_id is None:
        return
    assert node_id == UNRESOLVED or node_id in graph_ids, node_id
    assert "\\" not in node_id
    assert ".." not in node_id


# ===========================================================================
# Trace corpus
# ===========================================================================

_TRACE_NODE_IDS: tuple[str, ...] = (
    "a.py:f",
    "pkg/mod.py:Cls.method",
    "pkg/mod.py:<module>",
    "热点.py:计算",
    "sp ace.py:f",
    # Raw (with ensure_ascii=False) these three are line boundaries to
    # str.splitlines(), but not to any JSONL reader.
    "nel\u0085.py:f",
    "ls .py:f",
    "ps .py:f",
    # A reader passes node ids through untouched; these must come back verbatim.
    "dir\\win.py:f",
    "../escape.py:f",
    "<unresolved>",
    # A lone surrogate is valid JSON only as an escape.
    "lone\ud800.py:f",
)

_METADATA: tuple[Any, ...] = (
    {},
    {"count": 3},
    {"count": 0},
    {"count": -7},
    {"count": 2.9},
    {"count": 1e308},
    {"count": -0.0},
    {"count": math.nan},
    {"count": math.inf},
    {"count": -math.inf},
    {"count": True},
    {"count": False},
    {"count": "5"},
    {"count": None},
    {"count": [5]},
    {"count": {"n": 5}},
    {"count": 10**300},
    {"count": 2**64},
    {"live": True, "count": 4},
    [],
    "meta",
    7,
    None,
)

_BLANKS = (b"", b"   ", b"\t", b"\r", b" \x0b\x0c ")
# JSON whitespace only. VT/FF padding is its own (ledgered) kind below.
_PADDING = (b" ", b"\t", b"\r", b"  \t ")
_BAD_UTF8 = (b"\xff", b"\x80", b"\xc0\xaf", b"\xed\xa0\x80", b"\xe7\x83", b"\xf4\x90\x80\x80")
_RAW_CONTROLS = (b"\x00", b"\x07", b"\x0b", b"\x1c", b"\x1f")
_MARKER = "@@MARK@@"


def _dumps(value: Any, rng: random.Random) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=rng.random() < 0.5).encode("utf-8")
    except UnicodeEncodeError:  # a lone surrogate survives only as an escape
        return json.dumps(value, ensure_ascii=True).encode("utf-8")


def _event(rng: random.Random, **fields: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event": rng.choice(("call", "return", "exception", "line")),
        "node_id": rng.choice(_TRACE_NODE_IDS),
        "ts_ns": rng.randrange(0, 2**63),
        "thread_id": rng.randrange(0, 2**31),
        "frame_depth": rng.randrange(0, 64),
        "metadata": rng.choice(_METADATA),
    }
    event.update(fields)
    return event


def _nested_list(depth: int) -> Any:
    value: Any = []
    for _ in range(depth - 1):
        value = [value]
    return value


def _with_marker(rng: random.Random, replacement: bytes) -> bytes:
    line = _dumps(_event(rng, node_id=f"x{_MARKER}.py:f"), rng)
    return line.replace(_MARKER.encode(), replacement)


# --- line kinds the readers handle: the main sweep -------------------------


def _k_event(rng: random.Random) -> bytes:
    return _dumps(_event(rng), rng)


def _k_odd_fields(rng: random.Random) -> bytes:
    """Every field but node_id dropped or given a wrong type."""
    event = _event(rng)
    for key in ("event", "ts_ns", "thread_id", "frame_depth", "metadata"):
        roll = rng.random()
        if roll < 0.3:
            del event[key]
        elif roll < 0.6:
            event[key] = rng.choice(("x", -1, 2**70, 1.5, None, [], {}, True, math.nan))
    return _dumps(event, rng)


def _k_no_node_id(rng: random.Random) -> bytes:
    event = _event(rng)
    del event["node_id"]
    return rng.choice((_dumps(event, rng), b"{}"))


def _k_falsy_node_id(rng: random.Random) -> bytes:
    return _dumps(_event(rng, node_id=rng.choice(("", None, False, 0, 0.0, [], {}))), rng)


def _k_padded(rng: random.Random) -> bytes:
    return rng.choice(_PADDING) + _k_event(rng) + rng.choice(_PADDING)


def _k_blank(rng: random.Random) -> bytes:
    return rng.choice(_BLANKS)


def _k_truncated(rng: random.Random) -> bytes:
    """Torn mid-file: still newline-terminated, so not the T5-7 shape."""
    line = _k_event(rng)
    return line[: rng.randrange(1, len(line))]


def _k_invalid_utf8(rng: random.Random) -> bytes:
    return _with_marker(rng, rng.choice(_BAD_UTF8))


def _k_raw_control(rng: random.Random) -> bytes:
    return _with_marker(rng, rng.choice(_RAW_CONTROLS))


def _k_bom(rng: random.Random) -> bytes:
    return b"\xef\xbb\xbf" + _k_event(rng)


def _k_joined(rng: random.Random) -> bytes:
    return _k_event(rng) + rng.choice((b"", b" ", b"\r")) + _k_event(rng)


def _k_trailing_junk(rng: random.Random) -> bytes:
    return _k_event(rng) + rng.choice((b" x", b",", b"]", b"}", b" //", b"\x00"))


def _k_duplicate_keys(rng: random.Random) -> bytes:
    first, last = rng.sample(_TRACE_NODE_IDS, 2)
    return (
        b'{"event": "call", "node_id": '
        + _dumps(first, rng)
        + b', "node_id": '
        + _dumps(last, rng)
        + b"}"
    )


def _k_nested(rng: random.Random) -> bytes:
    """Nesting well under every supported interpreter's recursion guard."""
    return _dumps(
        _event(rng, metadata={"count": 2, "deep": _nested_list(rng.randrange(64, 257))}), rng
    )


def _k_long_value(rng: random.Random) -> bytes:
    return _dumps(_event(rng, metadata={"blob": "v" * (256 * 1024)}), rng)


def _k_long_node_id(rng: random.Random) -> bytes:
    return _dumps(_event(rng, node_id="n" * (64 * 1024) + ".py:f"), rng)


def _k_number_forms(rng: random.Random) -> bytes:
    count = rng.choice(
        (b"1E+2", b"-1e400", b"1e400", b"0.5e1", b"-0", b"1" + b"0" * 300, b"NaN", b"-Infinity")
    )
    node = _dumps(rng.choice(_TRACE_NODE_IDS), rng)
    return b'{"event": "call", "node_id": ' + node + b', "metadata": {"count": ' + count + b"}}"


def _k_cr_whitespace(rng: random.Random) -> bytes:
    """A raw CR is legal JSON whitespace between tokens."""
    return json.dumps(_event(rng), ensure_ascii=True, separators=(",\r", ":\r")).encode()


# --- kinds that expose a defect (new, ledgered below) ------------------------


def _k_huge_int(rng: random.Random) -> bytes:
    digits = b"7" * (sys.get_int_max_str_digits() + 1)
    return b'{"event": "call", "node_id": "a.py:f", "ts_ns": ' + digits + b"}"


def _k_deep_nesting(rng: random.Random) -> bytes:
    depth = 100_000
    return (
        b'{"event": "call", "node_id": "a.py:f", "metadata": ' + b"[" * depth + b"]" * depth + b"}"
    )


def _k_vt_ff_padded(rng: random.Random) -> bytes:
    """Padded with VT/FF: whitespace to bytes.strip(), not to JSON."""
    return rng.choice((b"\x0b", b"\x0c")) + _k_event(rng) + rng.choice((b"\x0b", b"\x0c", b""))


_MAIN_KINDS: dict[str, Callable[[random.Random], bytes]] = {
    "event": _k_event,
    "odd_fields": _k_odd_fields,
    "no_node_id": _k_no_node_id,
    "falsy_node_id": _k_falsy_node_id,
    "padded": _k_padded,
    "blank": _k_blank,
    "truncated": _k_truncated,
    "invalid_utf8": _k_invalid_utf8,
    "raw_control": _k_raw_control,
    "bom": _k_bom,
    "joined": _k_joined,
    "trailing_junk": _k_trailing_junk,
    "duplicate_keys": _k_duplicate_keys,
    "nested": _k_nested,
    "long_value": _k_long_value,
    "long_node_id": _k_long_node_id,
    "number_forms": _k_number_forms,
    "cr_whitespace": _k_cr_whitespace,
}


class _Line(NamedTuple):
    kind: str
    raw: bytes  # without its terminator
    terminator: bytes


def _trace_corpus(seed: int, *, lines: int = 240) -> list[_Line]:
    """Every main kind at least once, then a draw weighted toward well-formed
    events, shuffled. Mixed LF / CRLF terminators. Odd seeds end on a
    well-formed event with no terminator at all (still an event: not T5-7)."""
    rng = random.Random(seed)
    names = sorted(_MAIN_KINDS)
    pool = ("event",) * len(names) + tuple(names)
    order = names + [rng.choice(pool) for _ in range(lines - len(names))]
    rng.shuffle(order)
    corpus = [
        _Line(name, _MAIN_KINDS[name](rng), rng.choice((b"\n", b"\n", b"\n", b"\r\n")))
        for name in order
    ]
    if seed % 2:
        corpus.append(_Line("event", _k_event(rng), b""))
    for line in corpus:
        assert b"\n" not in line.raw, "generator bug: a line kind emitted a newline"
    return corpus


def _join(corpus: list[_Line]) -> bytes:
    return b"".join(line.raw + line.terminator for line in corpus)


# --- the oracle ----------------------------------------------------------------


class _Unparsable:
    def __repr__(self) -> str:
        return "<unparsable>"


_BAD = _Unparsable()


def _classify(stripped: bytes) -> object:
    """The tolerant readers' documented policy for one non-blank line: its JSON
    value, or _BAD if it is not UTF-8 JSON (whatever exception json raises)."""
    try:
        return json.loads(stripped.decode("utf-8"))
    except (ValueError, RecursionError):
        return _BAD


def _ref_weight(event: dict[str, Any]) -> int:
    """``metadata.count`` as documented in aggregates.py: a finite number,
    truncated and floored at 1; anything else (absent, bool, non-numeric,
    non-finite, metadata not an object) weighs 1."""
    metadata = event.get("metadata")
    count = metadata.get("count", 1) if isinstance(metadata, dict) else 1
    if type(count) is int:
        return max(1, count)
    if type(count) is float and math.isfinite(count):
        return max(1, int(count))
    return 1


@dataclass
class _Expected:
    """What a correct tolerant reader reports for a trace file."""

    slots: list[object] = field(default_factory=list)  # one per non-blank line
    offsets: list[int] = field(default_factory=list)
    heat_prefix: list[dict[str, int]] = field(default_factory=list)
    coverage_prefix: list[int] = field(default_factory=list)

    def window(self, start: int, count: int) -> list[object]:
        total = len(self.slots)
        start = max(0, min(start, total))
        end = max(start, min(start + count, total))
        return [slot for slot in self.slots[start:end] if slot is not _BAD]


def _oracle(data: bytes) -> _Expected:
    want = _Expected()
    position = 0
    for raw in data.split(b"\n"):
        stripped = raw.strip()
        if stripped:
            want.offsets.append(position)
            want.slots.append(_classify(stripped))
        position += len(raw) + 1
    heat: dict[str, int] = {}
    want.heat_prefix.append({})
    want.coverage_prefix.append(0)
    for slot in want.slots:
        if isinstance(slot, dict):
            node_id = slot.get("node_id")
            if isinstance(node_id, str) and node_id:
                heat[node_id] = heat.get(node_id, 0) + _ref_weight(slot)
        want.heat_prefix.append(dict(heat))
        want.coverage_prefix.append(len(heat))
    return want


def _canon(values: list[Any]) -> list[str]:
    return [json.dumps(value, sort_keys=True, ensure_ascii=True) for value in values]


# --- the main sweep --------------------------------------------------------------


@pytest.mark.parametrize("seed", range(8))
def test_tolerant_trace_readers_survive_the_malformed_corpus(tmp_path: Path, seed: int) -> None:
    corpus = _trace_corpus(seed)
    data = _join(corpus)
    path = tmp_path / "corpus.jsonl"
    path.write_bytes(data)
    want = _oracle(data)
    total = len(want.slots)
    assert not any(slot is not _BAD and not isinstance(slot, dict) for slot in want.slots), (
        "generator bug: a non-object line (T5-8) reached the agent sweep"
    )
    assert _BAD in want.slots

    index = _bounded(lambda: JsonlIndex.build(path))
    seek_index, seek_agg = _bounded(lambda: build_seekable(path))
    agg = _bounded(lambda: TraceAggregates.build(path))

    # One slot per non-blank line, at that line's byte offset, identically in
    # all three builders: the alignment every seek and query depends on.
    assert index._offsets == want.offsets
    assert seek_index._offsets == want.offsets
    assert len(index) == len(seek_index) == len(agg) == len(seek_agg) == total

    for at in range(total + 1):
        assert agg.cumulative_heat_all(at) == want.heat_prefix[at], at
        assert seek_agg.cumulative_heat_all(at) == want.heat_prefix[at], at
        assert agg.coverage_count(at) == want.coverage_prefix[at], at
        assert seek_agg.coverage_count(at) == want.coverage_prefix[at], at
    final = want.heat_prefix[total]
    ranked = sorted(final.items(), key=lambda item: (-item[1], item[0]))
    assert agg.top_k(10, total) == ranked[:10]
    # Node ids come back verbatim or not at all, `\` and `..` included.
    assert agg.node_ids == seek_agg.node_ids == frozenset(final)

    events = [slot for slot in want.slots if slot is not _BAD]
    assert _canon(_bounded(lambda: index.read_window(0, total + 3))) == _canon(events)
    rng = random.Random(seed)
    for _ in range(25):
        start, count = rng.randrange(-5, total + 5), rng.randrange(-2, 40)
        assert _canon(index.read_window(start, count)) == _canon(want.window(start, count))


@pytest.mark.parametrize("seed", range(8))
def test_strict_read_jsonl_over_the_malformed_corpus(tmp_path: Path, seed: int) -> None:
    """``read_jsonl`` is whole-file strict by contract: on the corpus it must
    raise, cleanly; on the corpus's well-formed lines it must return every one.

    The clean projection keeps the raw U+0085 / U+2028 / U+2029 node ids and
    the padded, BOM-free, CRLF-terminated lines, so it pins the documented
    ``split("\\n")`` (never ``splitlines()``). It leaves out ``cr_whitespace``
    lines, which read_jsonl rejects (ledgered below).
    """
    corpus = _trace_corpus(seed)
    path = tmp_path / "corpus.jsonl"
    path.write_bytes(_join(corpus))
    with pytest.raises((ValueError, RecursionError)):
        _bounded(lambda: read_jsonl(path))

    kept = [
        line
        for line in corpus
        if line.raw.strip()
        and line.kind != "cr_whitespace"
        and _classify(line.raw.strip()) is not _BAD
    ]
    clean = tmp_path / "clean.jsonl"
    clean.write_bytes(_join(kept))
    expected = [_classify(line.raw.strip()) for line in kept]
    assert _canon(cast("list[Any]", _bounded(lambda: read_jsonl(clean)))) == _canon(expected)


@pytest.mark.parametrize("seed", range(4))
def test_nn_heat_mirror_agrees_with_the_aggregates_on_the_malformed_corpus(
    tmp_path: Path, seed: int
) -> None:
    """``heat_from_jsonl`` documents itself as a line-for-line mirror of the
    aggregates (it only goes further on non-object lines and non-string node
    ids, which the agent sweep excludes). Hold it to that on adversarial input."""
    labels = pytest.importorskip("grackle_nn.ml.labels")
    path = tmp_path / "corpus.jsonl"
    path.write_bytes(_join(_trace_corpus(seed)))
    agg = TraceAggregates.build(path)
    assert labels.heat_from_jsonl(path) == agg.cumulative_heat_all(len(agg))


# --- new defects: trace readers ------------------------------------------------


def _three_line_trace(tmp_path: Path, middle: bytes) -> Path:
    first = json.dumps({"event": "call", "node_id": "a.py:f", "metadata": {}}).encode()
    last = json.dumps({"event": "call", "node_id": "b.py:g", "metadata": {"count": 2}}).encode()
    path = tmp_path / "trace.jsonl"
    path.write_bytes(first + b"\n" + middle + b"\n" + last + b"\n")
    return path


_OVERLONG_OR_DEEP = (
    "T6-5: a line that json.loads rejects with a plain ValueError (an integer "
    "over the int-digit limit) or a RecursionError (nesting deeper than the "
    "recursion guard), not JSONDecodeError, escapes the per-line tolerance and "
    f"fails the whole file {_LEDGER}"
)


@pytest.mark.xfail(strict=True, reason=_OVERLONG_OR_DEEP)
@pytest.mark.parametrize("kind", ["huge_int", "deep_nesting"])
@pytest.mark.parametrize("reader", ["aggregates", "build_seekable", "read_window"])
def test_line_json_rejects_without_a_decode_error_is_skipped_like_any_malformed_line(
    tmp_path: Path, default_int_digit_limit: int, kind: str, reader: str
) -> None:
    builder = _k_huge_int if kind == "huge_int" else _k_deep_nesting
    path = _three_line_trace(tmp_path, builder(random.Random(0)))
    if reader == "read_window":
        # The slot survives (it is a malformed line); only its event is skipped.
        events = JsonlIndex.build(path).read_window(0, 3)
        assert [event["node_id"] for event in events] == ["a.py:f", "b.py:g"]
        return
    agg = TraceAggregates.build(path) if reader == "aggregates" else build_seekable(path)[1]
    assert len(agg) == 3
    assert agg.cumulative_heat_all(3) == {"a.py:f": 1, "b.py:g": 2}


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T6-5: a truthy non-string node_id is admitted into the aggregates: an "
        "array/object raises TypeError out of the build, and a number/bool "
        "becomes a non-str key, so top_k raises TypeError on a count tie with a "
        f"string id and grackle diff exits on a TypeError traceback {_LEDGER}"
    ),
)
@pytest.mark.parametrize("node_id", [42, 1.5, True, ["a.py:f"], {"id": "a.py:f"}])
@pytest.mark.parametrize("builder", ["aggregates", "build_seekable"])
def test_non_string_node_id_is_skipped_like_a_missing_one(
    tmp_path: Path, node_id: object, builder: str
) -> None:
    """Sibling of T5-8, one level down: the line is an object, its node_id is
    not a string. A falsy one (``0``, ``[]``) is already skipped; the nn
    mirror already skips every non-string one."""
    middle = json.dumps({"event": "call", "node_id": node_id, "metadata": {}}).encode()
    path = _three_line_trace(tmp_path, middle)
    agg = TraceAggregates.build(path) if builder == "aggregates" else build_seekable(path)[1]
    assert agg.cumulative_heat_all(3) == {"a.py:f": 1, "b.py:g": 2}
    assert agg.top_k(10, 3) == [("b.py:g", 2), ("a.py:f", 1)]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T6-5: JsonlIndex.read_window parses a slot's line unstripped while the "
        "aggregate builders strip it first, so a line padded with VT/FF (ASCII "
        "whitespace to bytes.strip(), not to JSON) is counted by every heat and "
        f"coverage query but never returned by a seek {_LEDGER}"
    ),
)
@pytest.mark.parametrize("seed", range(4))
def test_read_window_returns_exactly_the_events_the_aggregates_count(
    tmp_path: Path, seed: int
) -> None:
    """build_seekable promises index slot i and aggregate event i are the same
    line; the two must also agree on whether that line is an event at all."""
    middle = _k_vt_ff_padded(random.Random(seed))
    path = _three_line_trace(tmp_path, middle)
    index, agg = build_seekable(path)
    counted = agg.cumulative_heat_all(2) != agg.cumulative_heat_all(1)
    assert counted == bool(index.read_window(1, 1))


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T6-5: read_jsonl reads in universal-newline text mode, so a raw CR "
        "(legal JSON whitespace between tokens) splits a well-formed line, "
        "contradicting its documented \\n-only splitting shared with "
        f"JsonlIndex.build {_LEDGER}"
    ),
)
def test_read_jsonl_splits_lines_only_on_newline(tmp_path: Path) -> None:
    event = {"event": "call", "node_id": "a.py:f", "ts_ns": 1, "metadata": {"count": 2}}
    path = tmp_path / "trace.jsonl"
    path.write_bytes(json.dumps(event, separators=(",\r", ":\r")).encode() + b"\n")
    # The tolerant readers already take it as one well-formed event.
    assert TraceAggregates.build(path).cumulative_heat_all(1) == {"a.py:f": 2}
    assert read_jsonl(path) == [event]


# ===========================================================================
# External-tool output: Go covdata textfmt
# ===========================================================================

_GO_MODULE = "example.com/app"
_GO_PATHS: tuple[str, ...] = (
    f"{_GO_MODULE}/pkg/u.go",
    f"{_GO_MODULE}/pkg/../pkg/u.go",
    f"{_GO_MODULE}/pkg/./u.go",
    f"{_GO_MODULE}/../../etc/passwd.go",
    f"{_GO_MODULE}/pkg/../../../x.go",
    f"{_GO_MODULE}/pkg\\u.go",
    f"{_GO_MODULE}//pkg//u.go",
    f"{_GO_MODULE}/unindexed/new.go",
    f"{_GO_MODULE}/有/文件.go",
    f"{_GO_MODULE}/a:b/c.go",
    f"{_GO_MODULE}/nul\x00.go",
    f"{_GO_MODULE}/" + "d" * 4096 + ".go",
    "example.com/appendix/pkg/u.go",
    "fmt/print.go",
    "/abs/path.go",
)
_GO_JUNK = "abcdefghijklmnopqrstuvwxyz ._-/,"


def _go_project(tmp_path: Path) -> tuple[GoResolver, frozenset[str]]:
    root = tmp_path / "goproj"
    (root / "pkg").mkdir(parents=True)
    (root / "go.mod").write_text(f"module {_GO_MODULE}\n\ngo 1.21\n", encoding="utf-8")
    nodes: list[dict[str, Any]] = [
        {"id": "pkg/u.go", "kind": "file", "name": "u.go", "path": "pkg/u.go"},
        {"id": "pkg/u.go:F", "kind": "function", "name": "F", "path": "pkg/u.go", "line": 3},
        {"id": "pkg/u.go:T.M", "kind": "method", "name": "M", "path": "pkg/u.go", "line": 9},
    ]
    graph = cast("StaticGraph", {"version": 1, "language": "go", "nodes": nodes, "edges": []})
    return GoResolver(root, graph), frozenset(str(node["id"]) for node in nodes)


def _covdata_corpus(seed: int) -> tuple[str, list[tuple[str, int, int]]]:
    """Textfmt with mixed valid, padded, header, blank and malformed lines.

    Every malformed shape breaks the documented grammar
    ``<path>:<sLine>.<sCol>,<eLine>.<eCol> <numStmts> <count>``; the expected
    blocks are exactly the grammatical lines, in order.
    """
    rng = random.Random(seed)
    out: list[str] = []
    expected: list[tuple[str, int, int]] = []
    for _ in range(220):
        path = rng.choice(_GO_PATHS)
        sl, sc, el, ec, n, count = (rng.randrange(0, 10 ** rng.randrange(1, 13)) for _ in range(6))
        body = f"{path}:{sl}.{sc},{el}.{ec} {n} {count}"
        roll = rng.random()
        if roll < 0.45:
            line = body
            expected.append((path, sl, count))
        elif roll < 0.55:
            line = rng.choice((" ", "\t", "  ")) + body + rng.choice(("", " ", "\t "))
            expected.append((path, sl, count))
        elif roll < 0.6:
            line = rng.choice(("mode: count", "mode: set", "mode: atomic", "mode:atomic"))
        elif roll < 0.65:
            line = rng.choice(("", "   ", "\t"))
        else:
            line = rng.choice(
                (
                    f"{path}:{sl}.{sc},{el}.{ec} {n}",
                    f"{path}:{sl}.{sc},{el}.{ec} {n} {count} {count}",
                    f"{path}:{sl}.{sc},{el}.{ec} {n} {count} junk",
                    f"{path}:{sl}.{sc},{el}.{ec} {n} {count}x",
                    f"{path}:{sl}.{sc},{el}.{ec} {n} -{count}",
                    f"{path}:{sl}.{sc},{el}.{ec} {n} {count}.5",
                    f"{path}:{sl}.x,{el}.{ec} {n} {count}",
                    f"{path}:{sl}.{sc},{el}.{ec}\t{n}\t{count}",
                    f"{path}:{sl}.{sc},{el}.{ec}  {n} {count}",
                    f"{path}:{sl}.{sc};{el}.{ec} {n} {count}",
                    f"{path}:0x{sl}.{sc},{el}.{ec} {n} {count}",
                    f"{path} {sl}.{sc},{el}.{ec} {n} {count}",
                    f":{sl}.{sc},{el}.{ec} {n} {count}",
                    "".join(rng.choice(_GO_JUNK) for _ in range(rng.randrange(1, 80))),
                )
            )
        out.append(line + rng.choice(("\n", "\n", "\r\n")))
    return "".join(out), expected


@pytest.mark.parametrize("seed", range(6))
def test_covdata_parser_and_resolver_survive_the_malformed_corpus(
    tmp_path: Path, seed: int
) -> None:
    text, expected = _covdata_corpus(seed)
    blocks = _bounded(lambda: parse_textfmt(text))
    assert [(b["import_path"], b["start_line"], b["count"]) for b in blocks] == expected

    resolver, graph_ids = _go_project(tmp_path)
    for block in blocks:
        _assert_safe_node_id(
            resolver.resolve_block(block["import_path"], block["start_line"]), graph_ids
        )
        posix = resolver._cached_normalize(block["import_path"])
        assert ".." not in posix.split("/")
        assert not posix.startswith("/")
    # Positive controls, so the safety check above cannot pass vacuously.
    assert resolver.resolve_block(f"{_GO_MODULE}/pkg/../pkg/u.go", 5) == "pkg/u.go:F"
    assert resolver.resolve_block(f"{_GO_MODULE}/unindexed/new.go", 1) == UNRESOLVED
    assert resolver.resolve_block(f"{_GO_MODULE}/../../etc/passwd.go", 1) is None


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T6-5: parse_textfmt raises ValueError on a grammatical line whose line "
        "or count field is longer than the int-digit limit, instead of skipping "
        f"it like any other malformed line {_LEDGER}"
    ),
)
@pytest.mark.parametrize("overlong", ["start_line", "count"])
def test_covdata_overlong_number_is_skipped(default_int_digit_limit: int, overlong: str) -> None:
    huge = "9" * (default_int_digit_limit + 1)
    good = f"{_GO_MODULE}/pkg/u.go:3.1,4.2 1 7"
    bad = (
        f"{_GO_MODULE}/pkg/u.go:{huge}.1,4.2 1 7"
        if overlong == "start_line"
        else f"{_GO_MODULE}/pkg/u.go:3.1,4.2 1 {huge}"
    )
    block = {"import_path": f"{_GO_MODULE}/pkg/u.go", "start_line": 3, "count": 7}
    assert parse_textfmt(f"mode: count\n{good}\n{bad}\n{good}\n") == [block, block]


# ===========================================================================
# External-tool output: llvm-cov export JSON
# ===========================================================================


def _rust_project(tmp_path: Path) -> tuple[Path, RustResolver, frozenset[str]]:
    root = (tmp_path / "rsproj").resolve()
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    nodes: list[dict[str, Any]] = [
        {"id": "src/main.rs", "kind": "file", "name": "main.rs", "path": "src/main.rs"},
        {
            "id": "src/main.rs:main",
            "kind": "function",
            "name": "main",
            "path": "src/main.rs",
            "line": 1,
        },
        {
            "id": "src/main.rs:helper",
            "kind": "function",
            "name": "helper",
            "path": "src/main.rs",
            "line": 10,
        },
    ]
    graph = cast("StaticGraph", {"version": 1, "language": "rust", "nodes": nodes, "edges": []})
    return root, RustResolver(root, graph), frozenset(str(node["id"]) for node in nodes)


def _rust_paths(root: Path) -> tuple[str, ...]:
    main = root / "src" / "main.rs"
    return (
        str(main),
        str(root / "src" / ".." / "src" / "main.rs"),
        str(root / "src" / ".." / ".." / "escape.rs"),
        str(root) + "/src\\main.rs",
        "src/main.rs",
        main.as_uri(),
        str(root / "src" / "unindexed.rs"),
        str(root / ("r" * 4096 + ".rs")),
        str(root / "src" / "nul\x00.rs"),
        "/definitely/not/here.rs",
        "héllo/ünïcode.rs",
    )


_LLVM_BAD_COUNTS: tuple[Any, ...] = (True, False, 1.5, "3", None, [3], {"n": 3}, math.nan)
_LLVM_BAD_FILENAMES: tuple[Any, ...] = (None, "x", [], [5], [""], [None])
_LLVM_BAD_REGIONS: tuple[Any, ...] = (None, "x", [], 5)
_LLVM_BAD_ROWS: tuple[Any, ...] = (
    [],
    "row",
    None,
    5,
    [True, 1, 2, 1, 0, 0, 0, 0],
    [1.5, 1, 2, 1, 0, 0, 0, 0],
    [None, 1, 2, 1, 0, 0, 0, 0],
    ["3", 1, 2, 1, 0, 0, 0, 0],
    [math.nan, 1, 2, 1, 0, 0, 0, 0],
)


def _llvm_function(rng: random.Random, root: Path) -> tuple[Any, tuple[str, int, int] | None]:
    """One ``functions[]`` entry and the record the documented contract expects
    of it (None: skipped). Negative counts are left out: ledgered below."""
    if rng.random() < 0.06:
        return rng.choice((None, 5, "fn", [1, 2])), None
    entry: dict[str, Any] = {"name": rng.choice(("_RNvCs_4main", "", "x" * 3000))}
    valid = True
    if rng.random() < 0.85:
        count: Any = rng.choice((0, 1, 7, 2**31, 2**63 - 1, 2**64))
    else:
        count, valid = rng.choice(_LLVM_BAD_COUNTS), False
    if rng.random() > 0.03:
        entry["count"] = count
    else:
        valid = False
    path = rng.choice(_rust_paths(root))
    if rng.random() < 0.9:
        entry["filenames"] = [path, *rng.sample(_rust_paths(root), rng.randrange(0, 3))]
    else:
        entry["filenames"], valid = rng.choice(_LLVM_BAD_FILENAMES), False
    rows: list[Any] = []
    for _ in range(rng.randrange(1, 8)):
        roll = rng.random()
        line = rng.choice((0, 1, 3, 10, 99, -4, 2**40))
        if roll < 0.55:
            rows.append([line, 1, line + 1, 1, rng.randrange(0, 9), 0, 0, 0])
        elif roll < 0.8:
            rows.append([line, 1, line + 1, 1, 0, rng.choice((1, 2)), 0, 1])
        else:
            rows.append(rng.choice(_LLVM_BAD_ROWS))
    if rng.random() < 0.93:
        entry["regions"] = rows
    else:
        entry["regions"], valid = rng.choice(_LLVM_BAD_REGIONS), False
    if not valid:
        return entry, None

    def lines(primary_only: bool) -> list[int]:
        return [
            row[0]
            for row in rows
            if isinstance(row, list)
            and row
            and type(row[0]) is int
            and (not primary_only or (len(row) >= 6 and row[5] == 0))
        ]

    candidates = lines(primary_only=True) or lines(primary_only=False)
    if not candidates:
        return entry, None
    return entry, (path, min(candidates), count)


def _llvm_corpus(seed: int, root: Path) -> tuple[str, list[tuple[str, int, int]]]:
    rng = random.Random(seed)
    sections: list[Any] = []
    expected: list[tuple[str, int, int]] = []
    for _ in range(rng.randrange(1, 4)):
        functions: list[Any] = []
        for _ in range(rng.randrange(0, 40)):
            entry, want = _llvm_function(rng, root)
            functions.append(entry)
            if want is not None:
                expected.append(want)
        sections.append({"functions": functions, "files": [], "totals": {}})
    junk_sections: tuple[Any, ...] = (None, 5, "s", [], {}, {"functions": None}, {"functions": "x"})
    for junk in rng.sample(junk_sections, k=3):
        sections.insert(rng.randrange(len(sections) + 1), junk)
    document = {"version": "2.0.1", "type": "llvm.coverage.json.export", "data": sections}
    return json.dumps(document), expected


@pytest.mark.parametrize("seed", range(6))
def test_llvm_cov_parser_and_resolver_survive_the_malformed_corpus(
    tmp_path: Path, seed: int
) -> None:
    root, resolver, graph_ids = _rust_project(tmp_path)
    text, expected = _llvm_corpus(seed, root)
    functions = _bounded(lambda: parse_export(text))
    assert [(f["path"], f["start_line"], f["count"]) for f in functions] == expected
    for function in functions:
        _assert_safe_node_id(
            resolver.resolve_function(function["path"], function["start_line"]), graph_ids
        )
    assert resolver.resolve_function(str(root / "src" / ".." / "src" / "main.rs"), 1) == (
        "src/main.rs:main"
    )
    assert resolver.resolve_function(str(root / "src" / ".." / ".." / "escape.rs"), 1) is None


@pytest.mark.parametrize(
    "text",
    [
        "",
        "{",
        "null",
        "[]",
        "5",
        '"s"',
        "NaN",
        "﻿{}",
        '{"data": 5}',
        '{"data": {"functions": []}}',
        '{"data": [null, 5, "x", {"functions": {}}]}',
        '{"data": []} trailing',
        '{"data": [{"functions": [{"count": 1, "filenames": ["/x.rs"], "regions": [[1]]}]',
    ],
)
def test_llvm_cov_malformed_document_yields_nothing(text: str) -> None:
    assert _bounded(lambda: parse_export(text)) == []


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T6-5: parse_export catches JSONDecodeError/ValueError only, so a "
        "document nested past the recursion guard raises RecursionError instead "
        f"of returning [] like any other unparsable document {_LEDGER}"
    ),
)
def test_llvm_cov_deeply_nested_document_yields_nothing() -> None:
    depth = 100_000
    assert parse_export('{"data": ' + "[" * depth + "]" * depth + "}") == []


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T6-5: parse_export keeps a function whose count is negative, although "
        "its contract (the _parse_function comment and RustCoverFunction.count) "
        "is a non-negative integer; the adapter then emits it as an executed "
        f"call, or lets it cancel a real monomorphisation's count {_LEDGER}"
    ),
)
def test_llvm_cov_negative_count_is_skipped(tmp_path: Path) -> None:
    root, _, _ = _rust_project(tmp_path)
    path = str(root / "src" / "main.rs")
    functions = [
        {"count": -3, "filenames": [path], "regions": [[1, 1, 2, 1, 0, 0, 0, 0]]},
        {"count": 4, "filenames": [path], "regions": [[10, 1, 11, 1, 0, 0, 0, 0]]},
    ]
    assert parse_export(json.dumps({"data": [{"functions": functions}]})) == [
        {"path": path, "start_line": 10, "count": 4}
    ]


# ===========================================================================
# External-tool output: V8 CPU profile and precise coverage
# ===========================================================================

_V8_PSEUDO = frozenset({"(root)", "(program)", "(idle)", "(garbage collector)", "(gc)"})
_V8_NAMES: tuple[str, ...] = (
    "f",
    "g",
    "m",
    "Cls.m",
    "",
    *sorted(_V8_PSEUDO),
    "anonymous",
    "热",
    "a.b.c",
    "x" * 2048,
)
_V8_LINE_NUMBERS: tuple[Any, ...] = (0, 1, 4, 9, -1, -5, 2**40, "3", 1.5, None, True)


def _v8_project(tmp_path: Path) -> tuple[Path, NodeResolver, frozenset[str]]:
    root = (tmp_path / "tsproj").resolve()
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.ts").write_text(
        "export function f() {}\n\n\n\nclass Cls {\n  m() {}\n}\n", encoding="utf-8"
    )
    (root / "src" / "b.ts").write_text("\nexport function g() {}\n", encoding="utf-8")
    nodes: list[dict[str, Any]] = [
        {"id": "src/a.ts", "kind": "file", "name": "a.ts", "path": "src/a.ts"},
        {"id": "src/a.ts:f", "kind": "function", "name": "f", "path": "src/a.ts", "line": 1},
        {"id": "src/a.ts:Cls.m", "kind": "method", "name": "m", "path": "src/a.ts", "line": 6},
        {"id": "src/b.ts", "kind": "file", "name": "b.ts", "path": "src/b.ts"},
        {"id": "src/b.ts:g", "kind": "function", "name": "g", "path": "src/b.ts", "line": 2},
    ]
    graph = cast(
        "StaticGraph", {"version": 1, "language": "typescript", "nodes": nodes, "edges": []}
    )
    return root, NodeResolver(root, graph), frozenset(str(node["id"]) for node in nodes)


def _v8_urls(root: Path) -> tuple[str, ...]:
    base = root.as_uri()
    local = "file://localhost" + base[len("file://") :]
    return (
        f"{base}/src/a.ts",
        f"{base}/src/b.ts",
        f"{base}/src/../src/a.ts",
        f"{base}/src/../../etc/x.ts",
        f"{base}/src/%2e%2e/%2e%2e/etc/x.ts",
        f"{base}/src%5ca.ts",
        f"{base}/src/a.ts%00",
        f"{base}/src/unindexed.ts",
        f"{base}/src",
        f"{local}/src/a.ts",
        f"{base}/" + "q" * 4096 + ".ts",
        "node:internal/modules/run_main",
        "http://evil.example/x.ts",
        "evalmachine.<anonymous>",
        "",
        str(root / "src" / "a.ts"),
        # file://<remote-host>/... is left out: it raises on 3.14 POSIX (below).
    )


def _call_frame(rng: random.Random, urls: tuple[str, ...]) -> dict[str, Any]:
    return {
        "functionName": rng.choice(_V8_NAMES),
        "scriptId": str(rng.randrange(0, 50)),
        "url": rng.choice(urls),
        "lineNumber": rng.choice(_V8_LINE_NUMBERS),
        "columnNumber": rng.randrange(0, 80),
    }


def _v8_profile(seed: int, root: Path) -> dict[str, Any]:
    """A V8 ``Profiler.stop`` profile, type-correct per the CDP schema but
    adversarial in value: hostile URLs, pseudo frames, negative and huge ids and
    times, dangling children, a parent cycle, a duplicated id, id-less and
    frame-less nodes, unknown sample ids, and samples/timeDeltas of different
    lengths."""
    rng = random.Random(seed)
    urls = _v8_urls(root)
    ids = rng.sample(range(-50, 10**6), rng.randrange(8, 80))
    frames: dict[int, dict[str, Any]] = {
        ids[0]: {"functionName": "(root)", "scriptId": "0", "url": "", "lineNumber": -1}
    }
    parent: dict[int, int] = {}
    for position, node_id in enumerate(ids[1:], start=1):
        frames[node_id] = _call_frame(rng, urls)
        parent[node_id] = rng.choice(ids[:position])
    # A frame guaranteed to resolve, and to be sampled: the positive control.
    control = 10**7
    frames[control] = {"functionName": "f", "scriptId": "1", "url": urls[0], "lineNumber": 0}
    parent[control] = ids[0]
    children: dict[int, list[int]] = {node_id: [] for node_id in frames}
    for child, par in parent.items():
        children[par].append(child)
    nodes: list[dict[str, Any]] = []
    for node_id, frame in frames.items():
        node: dict[str, Any] = {"id": node_id, "callFrame": frame, "hitCount": rng.randrange(0, 5)}
        kids = children[node_id] + [rng.randrange(2 * 10**7, 3 * 10**7)] * (rng.random() < 0.1)
        if kids or rng.random() < 0.5:
            node["children"] = kids
        nodes.append(node)
    # A parent cycle: some node's ancestor listed again as its child.
    deep = max(parent, key=lambda n: _depth(n, parent))
    ancestor = parent[deep]
    next(node for node in nodes if node["id"] == deep).setdefault("children", []).append(ancestor)
    # A duplicated id (the later entry wins; never the control's), an id-less
    # node, a frame-less node.
    original = rng.choice([node for node in nodes if node["id"] != control])
    duplicate = dict(original, callFrame=_call_frame(rng, urls))
    nodes.append(duplicate)
    nodes.append({"children": [], "callFrame": _call_frame(rng, urls)})
    nodes.append({"id": 3 * 10**7 + 1, "children": []})
    rng.shuffle(nodes)
    known = [*frames]
    samples = [
        rng.choice(known) if rng.random() < 0.9 else rng.randrange(-10, 10**8)
        for _ in range(rng.randrange(0, 300))
    ]
    samples.insert(rng.randrange(len(samples) + 1), control)
    deltas = [
        rng.choice((0, 1, 125, 10**9, -40)) for _ in range(len(samples) + rng.choice((-3, 0, 0, 2)))
    ]
    profile: dict[str, Any] = {"nodes": nodes, "samples": samples, "timeDeltas": deltas}
    if rng.random() < 0.8:
        profile["startTime"] = rng.choice((0, 1_000_000, 2**52, 1.5e6))
    if rng.random() < 0.8:
        profile["endTime"] = rng.choice((0, 2_000_000, 2**53))
    return profile


def _depth(node_id: int, parent: dict[int, int]) -> int:
    depth = 0
    while node_id in parent:
        node_id = parent[node_id]
        depth += 1
    return depth


def _assert_well_nested(events: list[TraceEvent], graph_ids: frozenset[str]) -> None:
    """The documented invariant: a call opens at the current stack depth, and its
    return closes the same frame at the same depth; nothing is left open."""
    stack: list[str] = []
    for event in events:
        _assert_safe_node_id(event["node_id"], graph_ids)
        if event["event"] == "call":
            assert event["frame_depth"] == len(stack)
            stack.append(event["node_id"])
        else:
            assert event["event"] == "return"
            assert stack
            assert event["frame_depth"] == len(stack) - 1
            assert stack.pop() == event["node_id"]
    assert stack == []


@pytest.mark.parametrize("seed", range(8))
def test_v8_profile_reconstruction_survives_the_malformed_corpus(tmp_path: Path, seed: int) -> None:
    root, resolver, graph_ids = _v8_project(tmp_path)
    profile = _v8_profile(seed, root)
    events = _bounded(lambda: reconstruct(profile, _make_resolve(resolver)))
    _assert_well_nested(events, graph_ids)
    assert "src/a.ts:f" in {event["node_id"] for event in events}


def test_v8_profile_parent_cycle_is_bounded(tmp_path: Path) -> None:
    """Each node lists the other as its child. The ``seen`` guard in
    ``reconstruct`` must end the walk up the parent chain; without it the walk
    never ends (hence the short bound)."""
    root, resolver, graph_ids = _v8_project(tmp_path)
    base = root.as_uri()
    profile = {
        "nodes": [
            {
                "id": 1,
                "callFrame": {"functionName": "f", "url": f"{base}/src/a.ts", "lineNumber": 0},
                "children": [2],
            },
            {
                "id": 2,
                "callFrame": {"functionName": "g", "url": f"{base}/src/b.ts", "lineNumber": 1},
                "children": [1],
            },
        ],
        "samples": [1, 2],
        "timeDeltas": [1, 1],
    }
    events = _bounded(lambda: reconstruct(profile, _make_resolve(resolver)), timeout=2.0)
    _assert_well_nested(events, graph_ids)
    assert [(e["event"], e["node_id"], e["frame_depth"]) for e in events] == [
        ("call", "src/b.ts:g", 0),
        ("call", "src/a.ts:f", 1),
        ("return", "src/a.ts:f", 1),
        ("return", "src/b.ts:g", 0),
    ]


def _v8_coverage(seed: int, root: Path) -> list[Any]:
    """A ``takePreciseCoverage`` result: type-correct where its parser does not
    already defend a field, adversarial wherever it does (null/non-numeric
    offsets and counts, non-string ids/urls/names, non-object range heads)."""
    rng = random.Random(seed)
    urls = (*_v8_urls(root), 5, None)
    offsets: tuple[Any, ...] = (0, 1, 17, 30, 10**9, -4, None, "12", 1.5, "x")
    counts: tuple[Any, ...] = (0, 1, 5, 10**15, -3, None, "7", 2.5, True, math.nan, "x")
    scripts: list[Any] = []
    for _ in range(rng.randrange(1, 12)):
        functions: list[Any] = []
        for _ in range(rng.randrange(0, 20)):
            ranges: list[Any] = [
                {
                    "startOffset": rng.choice(offsets),
                    "endOffset": rng.choice(offsets),
                    "count": rng.choice(counts),
                }
                for _ in range(rng.randrange(0, 4))
            ]
            if ranges and rng.random() < 0.1:
                ranges[0] = rng.choice(("head", 5, None, [1, 2]))
            functions.append(
                {
                    "functionName": rng.choice((*_V8_NAMES, 5, None, ["x"])),
                    "ranges": ranges,
                    "isBlockCoverage": rng.random() < 0.5,
                }
            )
        scripts.append(
            {
                "scriptId": rng.choice(("1", "2", 3, None, "")),
                "url": rng.choice(urls),
                "functions": functions,
            }
        )
    return scripts


def _ref_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _ref_counts(result: list[Any]) -> dict[tuple[str, int], int]:
    """The documented baseline: per (scriptId, first range's startOffset), the
    first range's count, coerced to int (0 if it cannot be), last write wins."""
    counts: dict[tuple[str, int], int] = {}
    for script in result:
        for function in script.get("functions") or []:
            ranges = function.get("ranges") or []
            if ranges and isinstance(ranges[0], dict):
                key = (str(script.get("scriptId", "")), _ref_int(ranges[0].get("startOffset")))
                counts[key] = _ref_int(ranges[0].get("count"))
    return counts


@pytest.mark.xfail(
    strict=True,
    raises=ValueError,
    reason=(
        "F-11 #11: _line_map_for_url catches OSError only, so a NUL in a coverage script's "
        "path escapes as ValueError and aborts the --stream session on platforms whose path "
        "resolution does not reject it first (docs/test-campaigns/phase-12.md)"
    ),
)
def test_a_nul_in_a_coverage_path_does_not_escape_the_line_map_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`_line_map_for_url`'s docstring promises `None` for "read failures", but it
    catches `OSError` only, and `Path.read_bytes()` on a path containing a NUL
    raises `ValueError` on every platform. Whether a hostile URL gets that far
    depends on the resolver rejecting it first, which differs by platform and
    Python version: it does on POSIX and Windows py3.12 (the corpus sweep below
    passes there) and does not on Windows py3.13 (where it failed CI). Handing the
    function a NUL path directly makes the defect deterministic everywhere."""
    _, resolver, _ = _v8_project(tmp_path)
    nul_path = tmp_path / "a.ts\x00"
    monkeypatch.setattr(resolver, "source_path", lambda url: nul_path)
    assert _line_map_for_url(resolver, {}, "file:///x/a.ts%00") is None


@pytest.mark.parametrize("seed", range(8))
def test_v8_coverage_polling_survives_the_malformed_corpus(tmp_path: Path, seed: int) -> None:
    root, resolver, graph_ids = _v8_project(tmp_path)
    first, second = _v8_coverage(seed, root), _v8_coverage(seed + 1000, root)
    deltas_1, baseline = _bounded(lambda: iter_coverage_deltas(first, {}))
    deltas_2, counts = _bounded(lambda: iter_coverage_deltas(second, baseline))
    assert baseline == _ref_counts(first)
    assert counts == _ref_counts(second)
    deltas: list[CoverageDelta] = [*deltas_1, *deltas_2]
    for delta in deltas:
        assert delta["delta"] > 0
        assert isinstance(delta["url"], str)
        assert isinstance(delta["function_name"], str)
    line_maps: dict[str, Any] = {}
    for delta in deltas:
        try:
            node_id = _resolve_coverage_delta(resolver, line_maps, delta)
        except ValueError:
            # F-11 #11 (docs/test-campaigns/phase-12.md), pinned deterministically by
            # test_a_nul_in_a_coverage_path_does_not_escape_the_line_map_lookup: a NUL
            # in the URL reaches `read_bytes()` on platforms whose path resolution does
            # not reject it first (Windows, py3.13 — this sweep's seeds 4-7 failed
            # there). Only that known case is tolerated; any other ValueError still fails.
            assert "%00" in delta["url"].lower(), delta["url"]
            continue
        _assert_safe_node_id(node_id, graph_ids)
    control, _ = iter_coverage_deltas(
        [
            {
                "scriptId": "9",
                "url": f"{root.as_uri()}/src/a.ts",
                "functions": [{"functionName": "f", "ranges": [{"startOffset": 0, "count": 3}]}],
            }
        ],
        {},
    )
    assert _resolve_coverage_delta(resolver, line_maps, control[0]) == "src/a.ts:f"


_GOOD_SCRIPT = {
    "scriptId": "1",
    "url": "u",
    "functions": [{"functionName": "f", "ranges": [{"startOffset": 0, "count": 2}]}],
}


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T6-5: iter_coverage_deltas tolerates null/non-numeric numbers and a "
        "non-object range head, but a non-object script or function entry, a "
        "non-list functions/ranges, or a non-finite count raises "
        "(AttributeError/TypeError/OverflowError), which poll()'s except "
        "CDPError does not catch: the --stream session its own _as_int "
        f"docstring says must survive is aborted {_LEDGER}"
    ),
)
@pytest.mark.parametrize(
    "malformed",
    [
        5,
        {"scriptId": "2", "functions": [5]},
        {"scriptId": "2", "functions": 5},
        {"scriptId": "2", "functions": [{"ranges": 5}]},
        {"scriptId": "2", "functions": [{"ranges": [{"startOffset": 0, "count": math.inf}]}]},
    ],
    ids=["script", "function", "functions", "ranges", "infinite_count"],
)
def test_v8_coverage_malformed_entry_is_skipped(malformed: Any) -> None:
    deltas, _ = iter_coverage_deltas([malformed, _GOOD_SCRIPT], {})
    assert [(d["script_id"], d["delta"]) for d in deltas] == [("1", 2)]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T6-5: the V8 sampling pipeline tolerates malformed node dicts and "
        "unknown sample ids, but a non-integer sample id, child id, time delta "
        "or start time, a non-object callFrame, or a non-string functionName "
        "raises out of reconstruct (TypeError/ValueError/AttributeError) and "
        f"loses the whole trace {_LEDGER}"
    ),
)
@pytest.mark.parametrize(
    "perturbation",
    ["sample_id", "child_id", "time_delta", "start_time", "call_frame", "function_name"],
)
def test_v8_profile_malformed_field_is_skipped(tmp_path: Path, perturbation: str) -> None:
    root, resolver, graph_ids = _v8_project(tmp_path)
    frame_f = {"functionName": "f", "url": f"{root.as_uri()}/src/a.ts", "lineNumber": 0}
    frame_x: Any = {"functionName": "g", "url": f"{root.as_uri()}/src/b.ts", "lineNumber": 1}
    if perturbation == "call_frame":
        frame_x = ["not", "an", "object"]
    elif perturbation == "function_name":
        frame_x = dict(frame_x, functionName=["g"])
    profile: dict[str, Any] = {
        "nodes": [
            {"id": 1, "callFrame": {"functionName": "(root)", "url": ""}, "children": [2]},
            {"id": 2, "callFrame": frame_f, "children": [3]},
            {"id": 3, "callFrame": frame_x},
        ],
        "samples": [2, 3, 2],
        "timeDeltas": [1, 1, 1],
        "startTime": 0,
    }
    if perturbation == "sample_id":
        profile["samples"] = [2, "x", 2]
    elif perturbation == "child_id":
        profile["nodes"][0]["children"] = [2, "x"]
    elif perturbation == "time_delta":
        profile["timeDeltas"] = [1, None, 1]
    elif perturbation == "start_time":
        profile["startTime"] = None
    events = reconstruct(profile, _make_resolve(resolver))
    _assert_well_nested(events, graph_ids)
    assert ("call", "src/a.ts:f", 0) in [
        (e["event"], e["node_id"], e["frame_depth"]) for e in events
    ]


@pytest.mark.skipif(
    os.name == "nt",
    reason="Windows maps a remote host to a UNC path by design; resolving it goes to the network",
)
@pytest.mark.xfail(
    sys.version_info >= (3, 14),  # url2pathname grew its host check in 3.14
    strict=True,
    reason=(
        "T6-5: on Python >= 3.14 (POSIX) url2pathname raises URLError for a "
        "file:// URL with a non-local host, and NodeResolver._normalize calls it "
        "outside its try, so one such URL aborts the whole trace or --stream "
        f"session instead of being filtered as out-of-project {_LEDGER}"
    ),
)
@pytest.mark.parametrize("channel", ["sampling", "coverage"])
def test_v8_file_url_with_a_remote_host_is_filtered(tmp_path: Path, channel: str) -> None:
    _, resolver, _ = _v8_project(tmp_path)
    url = "file://build-host/share/src/a.ts"
    if channel == "sampling":
        resolve = _make_resolve(resolver)
        assert resolve({"url": url, "lineNumber": 0, "functionName": "f"}) is None
    else:
        delta: CoverageDelta = {
            "script_id": "1",
            "url": url,
            "function_name": "f",
            "start_offset": 0,
            "delta": 1,
        }
        assert _resolve_coverage_delta(resolver, {}, delta) is None


# ===========================================================================
# Nightly: the same sweeps over many more seeds
# ===========================================================================

_SWEEPS: tuple[Callable[[Path, int], None], ...] = (
    test_tolerant_trace_readers_survive_the_malformed_corpus,
    test_strict_read_jsonl_over_the_malformed_corpus,
    test_nn_heat_mirror_agrees_with_the_aggregates_on_the_malformed_corpus,
    test_covdata_parser_and_resolver_survive_the_malformed_corpus,
    test_llvm_cov_parser_and_resolver_survive_the_malformed_corpus,
    test_v8_profile_reconstruction_survives_the_malformed_corpus,
    test_v8_coverage_polling_survives_the_malformed_corpus,
)


@pytest.mark.hammer
def test_malformed_corpus_hammer(tmp_path: Path) -> None:
    """Every seeded sweep above, over seeds 8..399 (the gate runs the first few)."""
    for seed in range(8, 400):
        for sweep in _SWEEPS:
            workdir = tmp_path / f"{seed}-{sweep.__name__}"
            workdir.mkdir()
            sweep(workdir, seed)
            shutil.rmtree(workdir)
