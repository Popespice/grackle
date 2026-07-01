"""Tests for python_runtime.value_repr — bounded, security-hardened value formatting.

Test rows are numbered to match the acceptance matrix in the Phase 10 plan
(chunk 10.1). Each numbered section is one row.
"""

from __future__ import annotations

import dataclasses
import enum
from typing import TYPE_CHECKING

from grackle.python_runtime.value_repr import (
    DEFAULT_LIMITS,
    ArgValue,
    ReprResult,
    ValueCaptureLimits,
    format_arg,
    is_sensitive_name,
    safe_repr,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Iterator
    from typing import NoReturn

# ---------------------------------------------------------------------------
# 1. Bounds: string / list / depth / dict truncate; in-bounds values don't
# ---------------------------------------------------------------------------


def test_long_string_truncates() -> None:
    result = safe_repr("x" * 500, ValueCaptureLimits(max_len=50))
    assert result.truncated is True
    assert len(result.text) <= 50


def test_short_string_not_truncated() -> None:
    result = safe_repr("hello", DEFAULT_LIMITS)
    assert result.truncated is False
    assert result.text == "'hello'"


def test_big_list_truncates() -> None:
    result = safe_repr(list(range(1000)), ValueCaptureLimits(max_items=5))
    assert result.truncated is True
    assert "..." in result.text


def test_small_list_not_truncated() -> None:
    result = safe_repr([1, 2, 3], ValueCaptureLimits(max_items=10))
    assert result.truncated is False
    assert result.text == "[1, 2, 3]"


def test_deep_nesting_truncates() -> None:
    nested: object = 0
    for _ in range(20):
        nested = [nested]
    result = safe_repr(nested, ValueCaptureLimits(max_depth=3))
    assert result.truncated is True


def test_shallow_nesting_not_truncated() -> None:
    result = safe_repr([[1, 2], [3, 4]], ValueCaptureLimits(max_depth=3, max_len=200))
    assert result.truncated is False


def test_big_dict_truncates() -> None:
    result = safe_repr({str(i): i for i in range(100)}, ValueCaptureLimits(max_items=3))
    assert result.truncated is True
    assert "..." in result.text


# ---------------------------------------------------------------------------
# 2. Cycles are bounded, not a recursion error
# ---------------------------------------------------------------------------


def test_self_referential_list_is_bounded() -> None:
    a: list[object] = []
    a.append(a)
    result = safe_repr(a)
    assert isinstance(result.text, str)
    assert result.truncated is True


def test_self_referential_dict_is_bounded() -> None:
    d: dict[str, object] = {}
    d["self"] = d
    result = safe_repr(d)
    assert isinstance(result.text, str)


# ---------------------------------------------------------------------------
# 3. User __repr__ is never invoked (covers raise + would-be-hang alike:
#    if it's never called, it can neither raise nor hang)
# ---------------------------------------------------------------------------


def test_user_repr_never_invoked_and_never_propagates() -> None:
    calls: list[str] = []

    class Hostile:
        def __repr__(self) -> str:
            calls.append("repr")
            raise RuntimeError("should never run")

    result = safe_repr(Hostile())
    assert calls == []
    assert isinstance(result.text, str)


# ---------------------------------------------------------------------------
# 4. Lazy iterators/generators are never consumed
# ---------------------------------------------------------------------------


def test_generator_not_consumed() -> None:
    def gen() -> Iterator[int]:
        yield 1
        yield 2

    g = gen()
    result = safe_repr(g)
    assert result.text == "<generator>"
    assert next(g) == 1  # still un-advanced


def test_map_iterator_not_consumed() -> None:
    it = map(str, [1, 2, 3])
    result = safe_repr(it)
    assert result.text == "<iterator>"
    assert next(it) == "1"


def test_list_iterator_not_consumed() -> None:
    it = iter([1, 2, 3])
    safe_repr(it)
    assert next(it) == 1


async def _async_gen() -> AsyncGenerator[int]:
    yield 1


async def _coro() -> int:
    return 1


async def test_async_generator_summarized_not_awaited() -> None:
    ag = _async_gen()
    result = safe_repr(ag)
    assert result.text == "<async_generator>"
    await ag.aclose()  # avoid an "unclosed" resource warning; not iterated by safe_repr


def test_coroutine_summarized_not_awaited() -> None:
    c = _coro()
    result = safe_repr(c)
    assert result.text == "<coroutine>"
    c.close()  # avoid an "unclosed" resource warning; not awaited/consumed


# ---------------------------------------------------------------------------
# 5. Name-spoofing: a class merely *named* "list"/"dict" is not iterated
# ---------------------------------------------------------------------------


def test_class_named_list_not_iterated() -> None:
    calls: list[str] = []

    class FakeList:
        def __iter__(self) -> Iterator[object]:
            calls.append("iter")
            return iter([])

        def __len__(self) -> int:
            calls.append("len")
            return 0

        def items(self) -> list[object]:
            calls.append("items")
            return []

    FakeList.__name__ = "list"
    FakeList.__qualname__ = "list"

    result = safe_repr(FakeList())
    assert calls == []
    assert "[" not in result.text


def test_class_named_dict_not_iterated() -> None:
    calls: list[str] = []

    class FakeDict:
        def items(self) -> list[object]:
            calls.append("items")
            return []

        def __len__(self) -> int:
            calls.append("len")
            return 0

    FakeDict.__name__ = "dict"
    FakeDict.__qualname__ = "dict"

    result = safe_repr(FakeDict())
    assert calls == []
    assert "{" not in result.text


# ---------------------------------------------------------------------------
# 6. __getattr__/__getattribute__ traps and __class__ spoofing
# ---------------------------------------------------------------------------


def test_getattr_trap_never_triggered() -> None:
    calls: list[str] = []

    class Trap:
        def __getattr__(self, name: str) -> object:
            calls.append(name)
            raise AttributeError(name)

    result = safe_repr(Trap())
    assert calls == []
    assert isinstance(result.text, str)


def test_class_spoofing_via_class_property_does_not_crash() -> None:
    class Evil:
        @property  # type: ignore[misc]
        def __class__(self) -> NoReturn:  # type: ignore[override]
            raise RuntimeError("boom")

    # type(x) bypasses the instance __class__ property entirely.
    result = safe_repr(Evil())
    assert isinstance(result.text, str)


# ---------------------------------------------------------------------------
# 7. Huge int beyond sys.get_int_max_str_digits
# ---------------------------------------------------------------------------


def test_huge_int_does_not_raise() -> None:
    huge = 10**5000
    result = safe_repr(huge)
    assert "bits" in result.text
    assert result.truncated is True


def test_normal_int_unaffected() -> None:
    result = safe_repr(42)
    assert result.text == "42"
    assert result.truncated is False


# ---------------------------------------------------------------------------
# 8. Bytes/bytearray never decoded; memoryview summarized
# ---------------------------------------------------------------------------


def test_bytes_never_decoded_and_shows_length() -> None:
    data = b"\xff\xfe\x00secret" * 50
    result = safe_repr(data, ValueCaptureLimits(max_len=200, max_items=5))
    assert f"len={len(data)}" in result.text
    assert result.truncated is True


def test_short_bytes_shown_in_full() -> None:
    result = safe_repr(b"hi")
    assert "len=2" in result.text


def test_bytearray_shown_with_length() -> None:
    result = safe_repr(bytearray(b"x" * 500), ValueCaptureLimits(max_len=200))
    assert "len=500" in result.text
    assert result.truncated is True


def test_memoryview_summarized_not_materialized() -> None:
    mv = memoryview(b"x" * 10_000)
    result = safe_repr(mv)
    assert "memoryview" in result.text
    # A shape/format summary is expected (and may legitimately include the
    # length, e.g. "shape=(10000,)"); the raw content must not be dumped.
    assert "x" * 100 not in result.text


# ---------------------------------------------------------------------------
# 9. Dict-key redaction; no sorting (no __lt__ invocation)
# ---------------------------------------------------------------------------


def test_dict_key_redaction() -> None:
    calls: list[str] = []

    class FlagOnRepr:
        def __repr__(self) -> str:
            calls.append("repr")
            return "FLAG"

    result = safe_repr({"api_key": FlagOnRepr()})
    assert calls == []
    assert "<redacted>" in result.text


def test_dict_non_str_keys_unaffected_by_redaction() -> None:
    result = safe_repr({1: "one", 2: "two"})
    assert "<redacted>" not in result.text


def test_dict_preserves_insertion_order_no_sort_call() -> None:
    class Uncomparable:
        def __lt__(self, other: object) -> bool:
            raise RuntimeError("must not be compared")

        def __repr__(self) -> str:
            return "U"

    d = {Uncomparable(): 1, Uncomparable(): 2}
    result = safe_repr(d)  # must not raise from sorting keys
    assert isinstance(result.text, str)


# ---------------------------------------------------------------------------
# 10. format_arg redaction by name
# ---------------------------------------------------------------------------


def test_format_arg_redacts_password() -> None:
    arg = format_arg("password", "hunter2")
    assert arg["repr"] == "<redacted>"
    assert arg.get("redacted") is True


def test_format_arg_redacts_case_insensitively() -> None:
    arg = format_arg("API_KEY", "abc123")
    assert arg["repr"] == "<redacted>"


def test_format_arg_redacts_substring_match() -> None:
    arg = format_arg("user_authorization_header", "Bearer xyz")
    assert arg["repr"] == "<redacted>"


def test_format_arg_does_not_redact_author() -> None:
    arg = format_arg("author", "Ada Lovelace")
    assert arg["repr"] != "<redacted>"
    assert "redacted" not in arg


def test_format_arg_redact_false_bypasses_redaction() -> None:
    arg = format_arg("password", "hunter2", redact=False)
    assert arg["repr"] != "<redacted>"


def test_is_sensitive_name_matches_and_excludes() -> None:
    assert is_sensitive_name("token")
    assert is_sensitive_name("Authorization")
    assert not is_sensitive_name("author")
    assert not is_sensitive_name("count")


# ---------------------------------------------------------------------------
# 11. Dataclasses: plain, slots, nested, sensitive field; __repr__ never called
# ---------------------------------------------------------------------------


@dataclasses.dataclass
class _PlainPoint:
    x: int
    y: int


@dataclasses.dataclass(slots=True)
class _SlottedPoint:
    x: int
    y: int


@dataclasses.dataclass
class _WithSecret:
    username: str
    password: str


@dataclasses.dataclass
class _Nested:
    point: _PlainPoint
    label: str


def test_plain_dataclass_renders_fields() -> None:
    result = safe_repr(_PlainPoint(1, 2))
    assert "x=1" in result.text
    assert "y=2" in result.text


def test_slotted_dataclass_renders_fields() -> None:
    result = safe_repr(_SlottedPoint(3, 4))
    assert "x=3" in result.text
    assert "y=4" in result.text


def test_nested_dataclass_renders() -> None:
    result = safe_repr(_Nested(_PlainPoint(1, 2), "hi"), ValueCaptureLimits(max_depth=3))
    assert "label='hi'" in result.text


def test_dataclass_sensitive_field_redacted() -> None:
    result = safe_repr(_WithSecret("alice", "hunter2"))
    assert "hunter2" not in result.text
    assert "<redacted>" in result.text


def test_dataclass_repr_never_invoked() -> None:
    calls: list[str] = []

    @dataclasses.dataclass
    class Tracked:
        value: int

        def __repr__(self) -> str:
            calls.append("repr")
            return "TRACKED"

    safe_repr(Tracked(1))
    assert calls == []


# ---------------------------------------------------------------------------
# 12. Enum members
# ---------------------------------------------------------------------------


class _Color(enum.Enum):
    RED = 1
    GREEN = 2


def test_enum_member_repr() -> None:
    result = safe_repr(_Color.RED)
    assert result.text == "_Color.RED"


# ---------------------------------------------------------------------------
# 13. Fake numpy/pandas path (no real numpy/pandas dependency)
# ---------------------------------------------------------------------------


def test_fake_numpy_ndarray_summary() -> None:
    class FakeArray:
        shape = (2, 3)
        dtype = "float64"

    FakeArray.__module__ = "numpy"
    FakeArray.__qualname__ = "ndarray"
    FakeArray.__name__ = "ndarray"

    result = safe_repr(FakeArray())
    assert "shape=(2, 3)" in result.text


def test_fake_numpy_raising_property_falls_back() -> None:
    class FakeArray:
        @property
        def shape(self) -> NoReturn:
            raise RuntimeError("boom")

    FakeArray.__module__ = "numpy"
    FakeArray.__qualname__ = "ndarray"
    FakeArray.__name__ = "ndarray"

    result = safe_repr(FakeArray())  # must not raise
    assert isinstance(result.text, str)
    assert "ndarray" in result.text  # falls back to the generic rung-8 summary


# ---------------------------------------------------------------------------
# 14. Exact-type dispatch, not isinstance — an int subclass is not fast-pathed
# ---------------------------------------------------------------------------


def test_int_subclass_with_evil_repr_not_fast_pathed() -> None:
    calls: list[str] = []

    class EvilInt(int):
        def __repr__(self) -> str:
            calls.append("repr")
            raise RuntimeError("boom")

    result = safe_repr(EvilInt(5))
    assert calls == []
    assert isinstance(result.text, str)


# ---------------------------------------------------------------------------
# 15. Never-raise gallery
# ---------------------------------------------------------------------------


def test_never_raise_gallery() -> None:
    class RaisingLen:
        def __len__(self) -> int:
            raise RuntimeError("len boom")

    class RaisingIter:
        def __iter__(self) -> NoReturn:
            raise RuntimeError("iter boom")

    class RaisingProperty:
        @property
        def anything(self) -> NoReturn:
            raise RuntimeError("prop boom")

    class RaisingLt:
        def __lt__(self, other: object) -> bool:
            raise RuntimeError("lt boom")

        def __eq__(self, other: object) -> bool:
            raise RuntimeError("eq boom")

        def __hash__(self) -> int:
            return 0

    pathological = [
        RaisingLen(),
        RaisingIter(),
        RaisingProperty(),
        RaisingLt(),
        {RaisingLt(): 1},
        [RaisingLt(), RaisingLt()],
    ]
    for value in pathological:
        result = safe_repr(value)
        assert isinstance(result, ReprResult)


# ---------------------------------------------------------------------------
# 16. Sanity: bounded work on a very large exact list (no timing assertions)
# ---------------------------------------------------------------------------


def test_million_element_list_is_bounded_by_construction() -> None:
    big = list(range(1_000_000))
    result = safe_repr(big, ValueCaptureLimits(max_items=5))
    assert "..." in result.text
    assert result.truncated is True


# ---------------------------------------------------------------------------
# 17. ArgValue shape: omit-when-false
# ---------------------------------------------------------------------------


def test_arg_value_omits_false_flags() -> None:
    arg: ArgValue = format_arg("x", 1)
    assert "redacted" not in arg
    assert "truncated" not in arg
    assert arg == {"name": "x", "repr": "1"}


def test_arg_value_sets_truncated_when_elided() -> None:
    arg = format_arg("big", "x" * 500, limits=ValueCaptureLimits(max_len=20))
    assert arg.get("truncated") is True
