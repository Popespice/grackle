"""Tests for python_runtime.value_repr — bounded, security-hardened value formatting.

Test rows are numbered to match the acceptance matrix in the Phase 10 plan
(chunk 10.1). Each numbered section is one row.
"""

from __future__ import annotations

import dataclasses
import enum
from typing import TYPE_CHECKING

import grackle.python_runtime.value_repr as value_repr
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
    from typing import Any, NoReturn

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
    # max_items is irrelevant here — bytes truncation is driven solely by
    # max_len (see test_bytes_truncation_ignores_max_items below).
    data = b"\xff\xfe\x00secret" * 50
    result = safe_repr(data, ValueCaptureLimits(max_len=200))
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


# ---------------------------------------------------------------------------
# 18. Post-review fixes (xhigh code review, PR #49)
# ---------------------------------------------------------------------------


def test_kebab_case_credential_names_are_redacted() -> None:
    # Regression: is_sensitive_name only matched underscore-joined spellings
    # ("api_key"), so a hyphenated/header-style name ("X-Api-Key") leaked its
    # value unredacted through both is_sensitive_name and the dict-key path.
    for name in ("x-api-key", "private-key", "access-key", "signing-key", "api-key"):
        assert is_sensitive_name(name), name

    result = safe_repr({"x-api-key": "SUPERSECRET"})
    assert "SUPERSECRET" not in result.text
    assert "<redacted>" in result.text

    arg = format_arg("private-key", "SUPERSECRET")
    assert arg["repr"] == "<redacted>"


def test_kebab_case_normalization_does_not_break_author_exclusion() -> None:
    assert not is_sensitive_name("author")
    assert not is_sensitive_name("co-author")


def test_dataclass_slot_property_getter_never_invoked() -> None:
    # Regression: the __slots__ fallback in _read_dataclass_field accepted
    # ANY descriptor exposing __get__ (hasattr(type(descriptor), "__get__")),
    # which a @property satisfies identically to a real slot descriptor —
    # so a property-backed dataclass field ran arbitrary user code.
    #
    # Construction: a slots=True dataclass field is overridden by a subclass
    # property of the same name. dataclasses.fields() still reports the
    # field (via inherited __dataclass_fields__), but type(x).__dict__ now
    # holds a `property`, not a real `member_descriptor`, for that name.
    # object.__new__ bypasses __init__ (the property has no setter, so
    # __init__'s self.sneaky = ... assignment would raise).
    calls: list[str] = []

    @dataclasses.dataclass(slots=True)
    class Base:
        sneaky: int = 0

    class Overridden(Base):
        __slots__ = ()

        @property
        def sneaky(self) -> int:  # type: ignore[override]
            calls.append("getter-ran")
            return 999

    inst = object.__new__(Overridden)
    result = safe_repr(inst)
    assert calls == []
    assert "sneaky=<unreadable>" in result.text


def test_dataclass_sensitive_slot_field_never_read() -> None:
    # Regression: the field VALUE was read before the is_sensitive_name(name)
    # check, so a sensitive descriptor-backed field's __get__ ran even though
    # the output was correctly redacted afterward.
    calls: list[str] = []

    @dataclasses.dataclass(slots=True)
    class Base:
        secret_token: str = ""

    class Overridden(Base):
        __slots__ = ()

        @property
        def secret_token(self) -> str:  # type: ignore[override]
            calls.append("getter-ran")
            return "hunter2"

    inst = object.__new__(Overridden)
    result = safe_repr(inst)
    assert calls == []
    assert "hunter2" not in result.text
    assert "<redacted>" in result.text


def test_set_truncation_does_not_materialize_whole_collection() -> None:
    # Regression: _repr_set_safe did items = list(x), copying the ENTIRE
    # set/frozenset before slicing to maxitems. itertools.islice + an O(1)
    # len() (safe here — x is guaranteed an exact set/frozenset by exact-type
    # dispatch) bounds the work to maxitems regardless of collection size.
    import tracemalloc

    big = set(range(2_000_000))
    tracemalloc.start()
    result = safe_repr(big, ValueCaptureLimits(max_items=5))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert result.truncated is True
    assert "..." in result.text
    # A full list(x) copy of 2M elements peaks in the tens of MB; bounded
    # work should stay a small fraction of that.
    assert peak < 1_000_000


def test_frozenset_truncation_does_not_materialize_whole_collection() -> None:
    import tracemalloc

    big = frozenset(range(1_000_000))
    tracemalloc.start()
    result = safe_repr(big, ValueCaptureLimits(max_items=5))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert result.truncated is True
    assert peak < 1_000_000


def test_numpy_summary_without_shape_does_not_call_len() -> None:
    # Regression: rung 7 fell back to len(x) when .shape was absent,
    # invoking user __len__ — beyond the documented "reads only .shape/
    # .dtype" contract for this rung.
    calls: list[str] = []

    class FakeArrayNoShape:
        def __len__(self) -> int:
            calls.append("len-called")
            return 42

    FakeArrayNoShape.__module__ = "numpy"
    FakeArrayNoShape.__qualname__ = "ndarray"
    FakeArrayNoShape.__name__ = "ndarray"

    result = safe_repr(FakeArrayNoShape())
    assert calls == []
    assert isinstance(result.text, str)


def test_nested_structure_bounds_total_assembled_characters() -> None:
    # Regression: the top-level max_len clamp only ran AFTER the fully
    # nested string was assembled; a max_depth-deep, max_items-wide
    # structure of near-max_len string leaves built a ~max_items**max_depth
    # intermediate (measured ~122 KB at default limits) before truncating
    # to max_len. The per-call character budget in repr1 now short-circuits
    # once the budget is exhausted, bounding total assembled work.
    import tracemalloc

    leaf = "x" * 200
    level1 = [leaf] * 10
    level2 = [level1] * 10
    structure = [level2] * 10  # 10 x 10 x 10 = 1000 leaves at max_depth=3

    tracemalloc.start()
    result = safe_repr(structure, DEFAULT_LIMITS)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    assert result.truncated is True
    assert len(result.text) <= DEFAULT_LIMITS.max_len
    # Pre-fix this peaked around ~370 KB; bounded assembly should stay well
    # under an order of magnitude of that.
    assert peak < 50_000


def test_bytes_suffix_never_mid_token_truncated_at_small_max_len() -> None:
    # Regression: _bytes_preview_max floored at 10 regardless of max_len, so
    # for max_len below ~30 the assembled preview + " (len=N)" suffix still
    # exceeded max_len, and the top-level clamp cut the suffix mid-token
    # (e.g. "b'x...xxx' (..." instead of a complete suffix or none at all).
    #
    # A first fix (a flat 20-char reserve floored at 0) still left a
    # narrower residual window: at max_len=8 specifically, the "(len=500)"
    # suffix (10 chars) is longer than max_len itself, so no amount of
    # shrinking the preview could make room for it, and the top-level clamp
    # cut it mid-token again. The current fix computes the ACTUAL suffix
    # length per call and omits the suffix entirely (rather than showing a
    # partial fragment) when even an empty preview can't make room for it —
    # exhaustively swept here, not just at cherry-picked sample points.
    for max_len in range(0, 41):
        result = safe_repr(b"x" * 500, ValueCaptureLimits(max_len=max_len))
        assert " (..." not in result.text, (max_len, result.text)
        assert len(result.text) <= max_len


def test_bytes_suffix_omitted_when_it_cannot_fit_at_all() -> None:
    # max_len=8 is smaller than " (len=500)" (10 chars) even with an empty
    # preview — the suffix must be dropped entirely, not shown truncated.
    result = safe_repr(b"x" * 500, ValueCaptureLimits(max_len=8))
    assert "len=" not in result.text
    assert len(result.text) <= 8


def test_bytes_truncation_ignores_max_items() -> None:
    # Documents (rather than asserts as a defect) that bytes truncation is
    # driven solely by max_len; max_items has no effect on the bytes path.
    small = safe_repr(b"x" * 500, ValueCaptureLimits(max_len=200, max_items=1))
    large = safe_repr(b"x" * 500, ValueCaptureLimits(max_len=200, max_items=100))
    assert small.text == large.text


def test_range_with_huge_bound_degrades_to_bounded_summary() -> None:
    # Regression: range/slice shared _repr_scalar_safe's unguarded repr(x),
    # so a huge-int bound raised the same sys.get_int_max_str_digits
    # ValueError int() is explicitly guarded against, degrading the whole
    # value to an opaque "<unreprable: range>".
    result = safe_repr(range(0, 10**5000))
    assert "unreprable" not in result.text
    assert "range(" in result.text
    assert "bits" in result.text


def test_slice_with_huge_bound_degrades_to_bounded_summary() -> None:
    result = safe_repr(slice(10**5000))
    assert "unreprable" not in result.text
    assert "slice(" in result.text
    assert "bits" in result.text
    assert "None" in result.text  # start/step are unset


def test_range_normal_case_unaffected() -> None:
    result = safe_repr(range(0, 10, 2))
    assert result.text == "range(0, 10, 2)"
    assert result.truncated is False


def test_slice_normal_case_unaffected() -> None:
    result = safe_repr(slice(1, 5, None))
    assert result.text == "slice(1, 5, None)"
    assert result.truncated is False


def test_slice_with_non_int_component_does_not_degrade_whole_value() -> None:
    # Regression: _repr_range_or_slice_safe originally assumed start/stop/
    # step were always int/None (true for range, enforced at construction,
    # but NOT for slice, which accepts any object) and called the int-only
    # guarded path unconditionally. A non-int component whose __repr__
    # raised any exception besides ValueError would propagate uncaught and
    # degrade the ENTIRE value to the generic "<unreprable: slice>"
    # fallback. Every component is now routed through the full repr1 ladder
    # instead, which handles any type safely.
    class BadRepr:
        def __repr__(self) -> str:
            raise ValueError("malformed state")

    result = safe_repr(slice(BadRepr(), 5))
    assert "unreprable" not in result.text
    assert "slice(" in result.text
    assert ", 5, None)" in result.text

    result2 = safe_repr(slice("a", "b"))
    assert result2.text == "slice('a', 'b', None)"


def test_dataclass_redaction_checked_before_field_read_call() -> None:
    # Regression: is_sensitive_name(f.name) must be checked BEFORE
    # _read_dataclass_field is even called for that field. The sibling test
    # test_dataclass_sensitive_slot_field_never_read cannot, by construction,
    # discriminate this ordering: any side-effecting read is already blocked
    # by _read_dataclass_field's own MemberDescriptorType guard, regardless
    # of check order. This test instead spies on _read_dataclass_field
    # itself to confirm it is never even CALLED for a sensitive field name,
    # independent of what it would have done if called.
    calls: list[str] = []
    original = value_repr._read_dataclass_field

    def spy(x: Any, name: str) -> Any:
        calls.append(name)
        return original(x, name)

    @dataclasses.dataclass
    class WithSecret:
        username: str
        password: str

    value_repr._read_dataclass_field = spy
    try:
        safe_repr(WithSecret("alice", "hunter2"))
    finally:
        value_repr._read_dataclass_field = original

    assert "password" not in calls
    assert "username" in calls


def test_dispatch_table_is_a_module_level_singleton_not_rebuilt_per_call() -> None:
    # Regression: the dispatch table used to be rebuilt (a fresh dict plus
    # several self-capturing lambdas) inside _SafeRepr.__init__ on every
    # safe_repr() call. It is now a module-level constant built once at
    # import time — construction identity must be stable across calls.
    dispatch_before = value_repr._DISPATCH
    safe_repr([1, 2, 3])
    safe_repr({"a": 1})
    assert value_repr._DISPATCH is dispatch_before
    assert dispatch_before[int] is value_repr._SafeRepr._repr_int_safe


def test_char_budget_does_not_double_count_nested_content() -> None:
    # Regression: repr1's character budget originally charged EVERY level's
    # own assembled string, including already-counted descendant content —
    # so a legitimately in-budget, deeply nested value could be spuriously
    # truncated (the effective charge-to-true-content ratio grew with
    # nesting depth instead of staying a small constant). Only leaf results
    # are charged now; container wrapper punctuation is unbudgeted (already
    # bounded by the pre-existing item-count/depth caps).
    limits = ValueCaptureLimits(max_len=1000, max_depth=7, max_items=10)
    leaf = "x" * 50
    structure: object = leaf
    for _ in range(6):
        structure = [structure]  # 6 levels of single-element wrapping

    result = safe_repr(structure, limits)
    assert result.truncated is False
    assert leaf in result.text
