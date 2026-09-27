"""Campaign T8-2 (docs/test-campaigns/phase-12.md): a metamorphic oracle for
``TraceAggregates``.

For any generated trace file and every ``at_index`` from before the start to
past the end, each query must equal a naive linear recount of the lines as
generated: ``cumulative_heat``, ``coverage_count``, ``top_k`` (every ``k``),
``cumulative_heat_all``, ``node_ids`` and ``len``. The recount never parses
the file; it knows what each line *is* because it generated it, and weights
events by the documented ``metadata.count`` rule (``_event_weight``'s
docstring), written out independently below.

``sparse_k > 1`` is documented as approximate: the answer is ``<=`` the true
count. It is checked against that band *and* against an exact recount of what
the docstring says is recorded (only indices that are multiples of
``sparse_k``, with ``at_index`` rounded down to one). The same docstring's
second promise — "differs by at most ``sparse_k - 1`` events" — does not hold
on a dense trace (``sparse_k=2``, one node hit at indices 0–3, ``at_index=4``:
true 4, sparse 2); that overstatement was already noted in C3 (T4-2) and is
not re-ledgered here.

``build`` and ``build_seekable`` must agree on every query, and the index
``build_seekable`` returns must count the same events.

Generated lines stay clear of the ledgered parser defects: every JSON line is
an object (T5-8), every ``node_id`` present is a string (F-11 #2), and no
number is long enough to trip the int-digit limit (F-11 #1).
"""

from __future__ import annotations

import dataclasses
import json
import math
from typing import TYPE_CHECKING, Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from grackle.python_runtime.aggregates import TraceAggregates, build_seekable

if TYPE_CHECKING:
    from pathlib import Path

_NODES = ("a.py:f", "b.py:g", "pkg/c.py:C.m", "d.py:<module>", "热点.py:计算")

# ---------------------------------------------------------------------------
# Generated lines
# ---------------------------------------------------------------------------

_MISSING = object()

# Hypothesis draws each one_of branch about equally often, so the branches
# are grouped to keep the interesting cases dense: numeric counts in half the
# draws, and within them fractions where truncating, rounding and flooring
# disagree.
_numeric_count = st.one_of(
    st.integers(-5, 10**18),
    st.sampled_from([1.5, 1.9, 2.5, 2.7, 3.5, 4.6, 0.9, 7.25, -1.5, -0.0, 1e20]),
    st.floats(allow_nan=True, allow_infinity=True, width=64),
)
_other_count = st.one_of(st.booleans(), st.none(), st.text(max_size=3), st.just([3]))
_count = st.one_of(_numeric_count, _other_count)
_metadata = st.one_of(
    st.builds(lambda count: {"count": count}, _numeric_count),
    st.one_of(
        st.just(_MISSING),
        st.fixed_dictionaries({}, optional={"count": _count, "live": st.booleans()}),
        st.sampled_from([[], "meta", 7, None]),
    ),
)


@dataclasses.dataclass(frozen=True)
class Line:
    """One generated line: ``kind`` is event / blank / malformed."""

    kind: str
    node_id: Any = _MISSING  # str, "" or _MISSING
    metadata: Any = _MISSING
    raw: bytes = b""  # the bytes of a blank or malformed line

    def encode(self) -> bytes:
        if self.kind != "event":
            return self.raw
        event: dict[str, Any] = {"event": "call", "ts_ns": 1, "thread_id": 1, "frame_depth": 0}
        if self.node_id is not _MISSING:
            event["node_id"] = self.node_id
        if self.metadata is not _MISSING:
            event["metadata"] = self.metadata
        return json.dumps(event, ensure_ascii=False).encode("utf-8")


_event_line = st.builds(
    Line,
    kind=st.just("event"),
    node_id=st.sampled_from((*_NODES, "", _MISSING)),
    metadata=_metadata,
)
_blank_line = st.builds(
    Line, kind=st.just("blank"), raw=st.sampled_from([b"", b"  ", b"\t", b" \t "])
)
_malformed_line = st.builds(
    Line,
    kind=st.just("malformed"),
    raw=st.sampled_from([b"{not json", b'{"node_id": "a.py:f"', b"\xff\xfe{}", b"}", b"nul"]),
)
lines_st = st.lists(st.one_of(_event_line, st.one_of(_blank_line, _malformed_line)), max_size=40)


def _write(path: Path, lines: list[Line], crlf: bool, final_newline: bool) -> None:
    sep = b"\r\n" if crlf else b"\n"
    body = sep.join(line.encode() for line in lines)
    if lines and final_newline:
        body += sep
    path.write_bytes(body)


# ---------------------------------------------------------------------------
# The naive oracle
# ---------------------------------------------------------------------------


def _weight(metadata: Any) -> int:
    """The documented rule: a dict ``metadata`` with a finite, non-bool
    numeric ``count`` contributes that count truncated toward zero and floored
    at 1; anything else contributes 1."""
    if not isinstance(metadata, dict) or "count" not in metadata:
        return 1
    raw = metadata["count"]
    if type(raw) is int:
        return max(1, raw)
    if type(raw) is float and math.isfinite(raw):
        return max(1, math.trunc(raw))
    return 1


@dataclasses.dataclass
class Recount:
    total: int
    hits: list[tuple[int, str, int]]  # (event index, node id, weight)

    @classmethod
    def of(cls, lines: list[Line]) -> Recount:
        index = 0
        hits: list[tuple[int, str, int]] = []
        for line in lines:
            if line.kind == "blank":
                continue
            if line.kind == "event" and isinstance(line.node_id, str) and line.node_id:
                hits.append((index, line.node_id, _weight(line.metadata)))
            index += 1
        return cls(index, hits)

    def heat(self, at: int, sparse_k: int = 1) -> dict[str, int]:
        """``{node: count}`` over [0, at), counting only what sparse mode
        records (multiples of sparse_k, at rounded down to one)."""
        cutoff = at if sparse_k == 1 else (at // sparse_k) * sparse_k
        out: dict[str, int] = {}
        for i, node, w in self.hits:
            if i < cutoff and i % sparse_k == 0:
                out[node] = out.get(node, 0) + w
        return out

    def coverage(self, at: int) -> int:
        first: dict[str, int] = {}
        for i, node, _ in self.hits:
            first.setdefault(node, i)
        return sum(1 for i in first.values() if i < at)

    def recorded_nodes(self, sparse_k: int) -> frozenset[str]:
        return frozenset(node for i, node, _ in self.hits if i % sparse_k == 0)


def _ranked(heat: dict[str, int]) -> list[tuple[str, int]]:
    return sorted(heat.items(), key=lambda item: (-item[1], item[0]))


def _check_against(agg: TraceAggregates, want: Recount, sparse_k: int) -> None:
    assert len(agg) == want.total
    assert agg.node_ids == want.recorded_nodes(sparse_k)
    for at in range(-2, want.total + 3):
        exact = want.heat(at)
        heat = want.heat(at, sparse_k)
        assert agg.cumulative_heat_all(at) == heat, at
        for node in (*_NODES, "never.py:seen"):
            got = agg.cumulative_heat(node, at)
            assert got == heat.get(node, 0), (node, at)
            assert 0 <= got <= exact.get(node, 0), (node, at)  # the documented band
        assert agg.coverage_count(at) == want.coverage(at), at
        ranked = _ranked(heat)
        for k in (-1, 0, 1, 2, len(_NODES) + 1):
            assert agg.top_k(k, at) == (ranked[:k] if k > 0 else []), (k, at)


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def trace_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("t8_2") / "trace.jsonl"


@given(lines=lines_st, crlf=st.booleans(), final_newline=st.booleans())
def test_full_resolution_queries_equal_a_naive_recount(
    trace_path: Path, lines: list[Line], crlf: bool, final_newline: bool
) -> None:
    _write(trace_path, lines, crlf, final_newline)
    _check_against(TraceAggregates.build(trace_path), Recount.of(lines), sparse_k=1)


@given(
    lines=lines_st,
    sparse_k=st.integers(2, 5),
    crlf=st.booleans(),
    final_newline=st.booleans(),
)
def test_sparse_queries_equal_the_documented_sampling_and_stay_in_band(
    trace_path: Path, lines: list[Line], sparse_k: int, crlf: bool, final_newline: bool
) -> None:
    _write(trace_path, lines, crlf, final_newline)
    agg = TraceAggregates.build(trace_path, sparse_k=sparse_k)
    _check_against(agg, Recount.of(lines), sparse_k)


@given(lines=lines_st, sparse_k=st.integers(-1, 4), crlf=st.booleans())
def test_build_seekable_agrees_with_build_on_every_query(
    trace_path: Path, lines: list[Line], sparse_k: int, crlf: bool
) -> None:
    """One pass or two, the aggregates are the same; ``sparse_k < 1`` means 1."""
    _write(trace_path, lines, crlf, final_newline=True)
    index, seek = build_seekable(trace_path, sparse_k=sparse_k)
    agg = TraceAggregates.build(trace_path, sparse_k=sparse_k)
    assert len(index) == len(seek) == len(agg)
    assert seek.node_ids == agg.node_ids
    for at in range(-1, len(agg) + 2):
        assert seek.cumulative_heat_all(at) == agg.cumulative_heat_all(at)
        assert seek.coverage_count(at) == agg.coverage_count(at)
        assert seek.top_k(3, at) == agg.top_k(3, at)
    _check_against(seek, Recount.of(lines), max(1, sparse_k))


@given(lines=lines_st, sparse_k=st.integers(1, 4))
def test_heat_and_coverage_are_monotone_and_settle_at_the_end(
    trace_path: Path, lines: list[Line], sparse_k: int
) -> None:
    """Oracle-free: a prefix sum never decreases as the playhead advances, is
    zero at or before the start, and stops changing once the playhead passes
    the end — in sparse mode, the end rounded *up* to a multiple of sparse_k,
    since a query is rounded down first (so at ``len(agg)`` itself a sparse
    answer can still be short of its own final value)."""
    _write(trace_path, lines, crlf=False, final_newline=True)
    agg = TraceAggregates.build(trace_path, sparse_k=sparse_k)
    end = len(agg)
    settled = -(-end // sparse_k) * sparse_k
    for node in _NODES:
        series = [agg.cumulative_heat(node, at) for at in range(-2, settled + 4)]
        assert series[:3] == [0, 0, 0]
        assert series == sorted(series)
        assert agg.cumulative_heat(node, end + 10**9) == agg.cumulative_heat(node, settled)
    coverage = [agg.coverage_count(at) for at in range(-2, end + 4)]
    assert coverage == sorted(coverage)
    assert coverage[:3] == [0, 0, 0]
    # Coverage is full-resolution: every node with any event, recorded or not.
    assert coverage[-1] >= len(agg.node_ids)
    if sparse_k == 1:
        assert coverage[-1] == len(agg.node_ids)
