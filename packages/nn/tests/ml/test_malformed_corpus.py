"""Test campaign T6-5 (docs/test-campaigns/phase-12.md), nn side:
``heat_from_jsonl`` over a seeded malformed-trace corpus.

The agent's ``tests/test_malformed_corpus.py`` sweeps the agent's trace readers
with a seeded adversarial generator and cross-checks this mirror against them
on every line both define. This module holds the mirror to its own, wider
contract: unlike the agent's builders it also skips a non-object line (T5-8)
and a non-string node id, so this corpus includes both. The generator is a
compact copy of the agent's (test packages do not import each other); keep the
two sets of line kinds in step.
"""

from __future__ import annotations

import json
import math
import random
import sys
from typing import TYPE_CHECKING, Any

import pytest

from grackle_nn.ml.labels import heat_from_jsonl

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

_NODE_IDS: tuple[str, ...] = (
    "a.py:f",
    "pkg/mod.py:Cls.method",
    "热点.py:计算",
    "nel\u0085.py:f",
    "ls .py:f",
    "ps .py:f",
    "dir\\win.py:f",
    "../escape.py:f",
    "lone\ud800.py:f",
)
_METADATA: tuple[Any, ...] = (
    {},
    {"count": 3},
    {"count": 0},
    {"count": -7},
    {"count": 2.9},
    {"count": 1e308},
    {"count": math.nan},
    {"count": math.inf},
    {"count": True},
    {"count": "5"},
    {"count": None},
    {"count": [5]},
    {"count": 10**300},
    [],
    "meta",
    None,
)
_BAD_UTF8 = (b"\xff", b"\x80", b"\xc0\xaf", b"\xed\xa0\x80", b"\xe7\x83")
_MARKER = "@@MARK@@"


def _dumps(value: Any, rng: random.Random) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=rng.random() < 0.5).encode("utf-8")
    except UnicodeEncodeError:  # a lone surrogate survives only as an escape
        return json.dumps(value, ensure_ascii=True).encode("utf-8")


def _event(rng: random.Random, **fields: Any) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event": rng.choice(("call", "return")),
        "node_id": rng.choice(_NODE_IDS),
        "ts_ns": rng.randrange(0, 2**63),
        "metadata": rng.choice(_METADATA),
    }
    event.update(fields)
    return event


def _k_event(rng: random.Random) -> bytes:
    return _dumps(_event(rng), rng)


def _k_node_id_not_a_string(rng: random.Random) -> bytes:
    node_id = rng.choice(("", None, False, 0, [], {}, 42, 1.5, True, ["a.py:f"], {"id": "a"}))
    return _dumps(_event(rng, node_id=node_id), rng)


def _k_no_node_id(rng: random.Random) -> bytes:
    return rng.choice((b"{}", b'{"event": "call", "metadata": {"count": 9}}'))


def _k_non_object(rng: random.Random) -> bytes:
    return rng.choice((b"[1, 2]", b"42", b'"a.py:f"', b"null", b"true", b"[]", b"1.5"))


def _k_padded(rng: random.Random) -> bytes:
    pad = (b" ", b"\t", b"\r", b"\x0b", b"\x0c")
    return rng.choice(pad) + _k_event(rng) + rng.choice(pad)


def _k_blank(rng: random.Random) -> bytes:
    return rng.choice((b"", b"   ", b"\t", b"\r", b" \x0b\x0c "))


def _k_truncated(rng: random.Random) -> bytes:
    line = _k_event(rng)
    return line[: rng.randrange(1, len(line))]


def _k_invalid_utf8(rng: random.Random) -> bytes:
    line = _dumps(_event(rng, node_id=f"x{_MARKER}.py:f"), rng)
    return line.replace(_MARKER.encode(), rng.choice(_BAD_UTF8))


def _k_raw_control(rng: random.Random) -> bytes:
    line = _dumps(_event(rng, node_id=f"x{_MARKER}.py:f"), rng)
    return line.replace(_MARKER.encode(), rng.choice((b"\x00", b"\x0b", b"\x1c")))


def _k_bom(rng: random.Random) -> bytes:
    return b"\xef\xbb\xbf" + _k_event(rng)


def _k_joined(rng: random.Random) -> bytes:
    return _k_event(rng) + rng.choice((b"", b" ", b"\r")) + _k_event(rng)


def _k_trailing_junk(rng: random.Random) -> bytes:
    return _k_event(rng) + rng.choice((b" x", b",", b"]", b"\x00"))


def _k_duplicate_keys(rng: random.Random) -> bytes:
    first, last = rng.sample(_NODE_IDS, 2)
    return b'{"node_id": ' + _dumps(first, rng) + b', "node_id": ' + _dumps(last, rng) + b"}"


def _k_nested(rng: random.Random) -> bytes:
    deep: Any = []
    for _ in range(rng.randrange(64, 257)):
        deep = [deep]
    return _dumps(_event(rng, metadata={"count": 2, "deep": deep}), rng)


def _k_long_value(rng: random.Random) -> bytes:
    return _dumps(_event(rng, metadata={"blob": "v" * (256 * 1024)}), rng)


def _k_number_forms(rng: random.Random) -> bytes:
    count = rng.choice((b"1E+2", b"-1e400", b"1e400", b"0.5e1", b"-0", b"NaN", b"-Infinity"))
    node = _dumps(rng.choice(_NODE_IDS), rng)
    return b'{"node_id": ' + node + b', "metadata": {"count": ' + count + b"}}"


def _k_cr_whitespace(rng: random.Random) -> bytes:
    return json.dumps(_event(rng), ensure_ascii=True, separators=(",\r", ":\r")).encode()


_KINDS: dict[str, Callable[[random.Random], bytes]] = {
    "event": _k_event,
    "node_id_not_a_string": _k_node_id_not_a_string,
    "no_node_id": _k_no_node_id,
    "non_object": _k_non_object,
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
    "number_forms": _k_number_forms,
    "cr_whitespace": _k_cr_whitespace,
}


def _corpus(seed: int, *, lines: int = 240) -> bytes:
    rng = random.Random(seed)
    names = sorted(_KINDS)
    pool = ("event",) * len(names) + tuple(names)
    order = names + [rng.choice(pool) for _ in range(lines - len(names))]
    rng.shuffle(order)
    out = b"".join(_KINDS[name](rng) + rng.choice((b"\n", b"\n", b"\r\n")) for name in order)
    return out + (_k_event(rng) if seed % 2 else b"")


def _ref_weight(event: dict[str, Any]) -> int:
    metadata = event.get("metadata")
    count = metadata.get("count", 1) if isinstance(metadata, dict) else 1
    if type(count) is int:
        return max(1, count)
    if type(count) is float and math.isfinite(count):
        return max(1, int(count))
    return 1


def _expected_heat(data: bytes) -> dict[str, int]:
    """The documented policy: every non-blank line that is UTF-8 JSON, an
    object, and carries a non-empty string node_id adds its count-weight."""
    heat: dict[str, int] = {}
    for raw in data.split(b"\n"):
        stripped = raw.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped.decode("utf-8"))
        except (ValueError, RecursionError):
            continue
        if isinstance(event, dict):
            node_id = event.get("node_id")
            if isinstance(node_id, str) and node_id:
                heat[node_id] = heat.get(node_id, 0) + _ref_weight(event)
    return heat


@pytest.mark.parametrize("seed", range(8))
def test_heat_from_jsonl_survives_the_malformed_corpus(tmp_path: Path, seed: int) -> None:
    data = _corpus(seed)
    path = tmp_path / "corpus.jsonl"
    path.write_bytes(data)
    heat = heat_from_jsonl(path)
    expected = _expected_heat(data)
    assert heat == expected
    assert len(expected) >= 5  # the check is not vacuous


@pytest.fixture
def default_int_digit_limit() -> Iterator[int]:
    """Pin CPython's default int-to-str digit limit, so a
    ``PYTHONINTMAXSTRDIGITS`` override cannot hide the trigger below."""
    previous = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(4300)
    try:
        yield 4300
    finally:
        sys.set_int_max_str_digits(previous)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T6-5: a line that json.loads rejects with a plain ValueError (an "
        "integer over the int-digit limit) or a RecursionError (nesting deeper "
        "than the recursion guard), not JSONDecodeError, escapes "
        "heat_from_jsonl's per-line tolerance and fails the whole file "
        "(docs/test-campaigns/phase-12.md)"
    ),
)
@pytest.mark.parametrize("kind", ["huge_int", "deep_nesting"])
def test_line_json_rejects_without_a_decode_error_is_skipped(
    tmp_path: Path, default_int_digit_limit: int, kind: str
) -> None:
    if kind == "huge_int":
        middle = b'{"node_id": "a.py:f", "ts_ns": ' + b"7" * (default_int_digit_limit + 1) + b"}"
    else:
        middle = b'{"node_id": "a.py:f", "metadata": ' + b"[" * 100_000 + b"]" * 100_000 + b"}"
    path = tmp_path / "trace.jsonl"
    path.write_bytes(b'{"node_id": "a.py:f"}\n' + middle + b'\n{"node_id": "b.py:g"}\n')
    assert heat_from_jsonl(path) == {"a.py:f": 1, "b.py:g": 1}
