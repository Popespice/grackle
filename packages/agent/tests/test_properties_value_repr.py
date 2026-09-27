"""Campaign T8-1 (docs/test-campaigns/phase-12.md): property battery for
``value_repr.safe_repr`` / ``format_arg`` over generated adversarial values.

The module makes four promises, all checked here over generated input rather
than hand-picked examples:

1. **Bounded.** ``len(text) <= max_len``, depth and width capped, and it never
   raises (an ``Exception`` from anywhere degrades to a placeholder).
2. **Honest.** For a value built only from exact builtins, an untruncated
   result is *exactly* ``repr(value)``, and a truncated one is not — so the
   ``truncated`` flag can be trusted both ways. At the exact limits a value
   fits; one below any limit it does not.
3. **Never runs user code.** No ``__repr__``, ``__len__``, ``__iter__``,
   ``__getattr__``, ``__lt__``, … of a generated hostile class is ever called,
   no lazy iterator is advanced, and a sensitive-named value is never touched.
4. **Redaction** is by name, case- and separator-insensitive.

Hostile values are generated as *blueprints* (plain data, so Hypothesis can
print and shrink them) and materialized into instances of freshly built
classes inside each test. Every hook appends to ``_LOG``; the log is cleared
after materialization (building a set hashes its members) and must still be
empty after ``safe_repr``.

Finding T8-1 is ledgered as four strict xfails at the bottom: three
instance hooks that *are* called today (a–c), and an error path that skips
the ``max_len`` clamp (d). The passing properties exclude exactly the hooks of
a–c, and nothing else; without them, d's path is unreachable in a single
thread.
"""

from __future__ import annotations

import dataclasses
import enum
import inspect
import re
from typing import TYPE_CHECKING, Any
from unittest import mock

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

import grackle.python_runtime.value_repr as value_repr
from grackle.python_runtime.value_repr import (
    SENSITIVE_NAME_PARTS,
    ReprResult,
    ValueCaptureLimits,
    format_arg,
    is_sensitive_name,
    safe_repr,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

_LEDGER = "(docs/test-campaigns/phase-12.md)"

# ---------------------------------------------------------------------------
# Limits — the CLI's own domain (click.IntRange(min=1) on all three flags)
# ---------------------------------------------------------------------------

limits_st = st.builds(
    ValueCaptureLimits,
    max_len=st.integers(1, 160),
    max_items=st.integers(1, 12),
    max_depth=st.integers(1, 5),
)

# ---------------------------------------------------------------------------
# Plain builtin values (the exactness oracle's domain)
# ---------------------------------------------------------------------------

# No huge ints (> 4300 digits: repr() itself raises, so there is no oracle),
# no bytes (safe_repr appends " (len=N)" by design), no range/slice (rendered
# with an explicit step by design), and no sensitive str dict keys (redaction
# is not truncation — covered separately below).
_plain_scalar = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(-(10**30), 10**30),
    st.floats(),
    st.complex_numbers(),
    st.text(max_size=12),
)
_plain_key = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(-(10**6), 10**6),
    st.floats(allow_nan=False),
    st.text(max_size=8).filter(lambda s: not is_sensitive_name(s)),
)


def _plain_children(children: st.SearchStrategy[Any]) -> st.SearchStrategy[Any]:
    hashable = st.one_of(_plain_key, st.tuples(_plain_key, _plain_key))
    return st.one_of(
        st.lists(children, max_size=5),
        st.lists(children, max_size=5).map(tuple),
        st.dictionaries(_plain_key, children, max_size=5),
        st.sets(hashable, max_size=5),
        st.frozensets(hashable, max_size=5),
    )


plain_values = st.recursive(_plain_scalar, _plain_children, max_leaves=30)


def _children(v: Any) -> list[Any]:
    t = type(v)
    if t is dict:
        return [*v.keys(), *v.values()]
    if t in (list, tuple, set, frozenset):
        return list(v)
    return []


def _nesting(v: Any) -> int:
    """Levels of non-empty exact containers along the deepest path."""
    kids = _children(v)
    if not kids:
        return 0
    return 1 + max(_nesting(k) for k in kids)


def _width(v: Any) -> int:
    """Largest item count of any exact container in *v* (dict: pairs)."""
    t = type(v)
    own = len(v) if t in (list, tuple, dict, set, frozenset) else 0
    return max([own, *(_width(k) for k in _children(v))])


# ---------------------------------------------------------------------------
# Hostile blueprints
# ---------------------------------------------------------------------------

_LOG: list[str] = []


class _HookError(Exception):
    """What a raising hook raises — an ordinary ``Exception``, the class
    ``safe_repr`` promises to absorb."""


# Every instance-level hook the module promises never to call. Excluded (they
# are called today — finding T8-1, ledgered at the bottom): __getattribute__
# and a __class__ property (a), str.lower on a str-subclass dict key (b), a
# dataclass __dict__ property (c).
_SAFE_HOOKS: tuple[str, ...] = (
    "__repr__",
    "__str__",
    "__format__",
    "__len__",
    "__iter__",
    "__next__",
    "__getitem__",
    "__contains__",
    "__getattr__",
    "__eq__",
    "__lt__",
    "__hash__",
    "__bool__",
    "__index__",
    "__reversed__",
    "keys",
    "items",
    "values",
)
# Hashing/equality happen while *building* a set or dict key; they record but
# never raise, so a raising spec can still be placed there.
_NEVER_RAISE = frozenset({"__hash__", "__eq__"})

_BASES: tuple[str, ...] = (
    "object",
    "iterator",
    "list",
    "tuple",
    "dict",
    "set",
    "frozenset",
    "str",
    "int",
    "float",
    "bytes",
    "dataclass",
    "slots_dataclass",
    "shadowed_dataclass",
    "shadowed_slots_dataclass",
)


def _hook_default(name: str, self: object, *args: Any) -> Any:
    if name in ("__repr__", "__str__", "__format__"):
        return "hooked"
    if name == "__len__":
        return 1
    if name in ("__iter__", "__reversed__"):
        return iter(())
    if name == "__next__":
        raise StopIteration
    if name == "__getitem__":
        raise IndexError(args[0] if args else None)
    if name == "__contains__":
        return False
    if name == "__getattr__":
        raise AttributeError(args[0] if args else "")
    if name == "__eq__":
        return self is (args[0] if args else None)
    if name == "__lt__":
        return False
    if name == "__hash__":
        return id(self) >> 4
    if name == "__bool__":
        return True
    if name == "__index__":
        return 0
    return []  # keys / items / values


def _make_hook(label: str, name: str, raises: bool) -> Callable[..., Any]:
    def hook(self: object, *args: Any) -> Any:
        _LOG.append(f"{label}.{name}")
        if raises and name not in _NEVER_RAISE:
            raise _HookError(name)
        return _hook_default(name, self, *args)

    return hook


def _plain_hash(self: object) -> int:
    return id(self) >> 4


@dataclasses.dataclass(frozen=True, eq=False)
class HostileSpec:
    """A hostile value to materialize: a base kind, the hooks its class
    records, whether those hooks raise, and (dataclass bases) two field
    values — the second under the sensitive name ``token``."""

    base: str
    hooks: tuple[str, ...]
    raises: bool
    fields: tuple[Any, Any] = (None, None)


class _HostileEnum(enum.Enum):
    """Rung 6 (enum members): rendered from ``_name_`` alone, so none of these
    hooks may fire."""

    A = 1
    B = 2

    def __repr__(self) -> str:
        _LOG.append("enum.__repr__")
        raise _HookError("__repr__")

    def __str__(self) -> str:
        _LOG.append("enum.__str__")
        raise _HookError("__str__")

    def __format__(self, spec: str) -> str:
        _LOG.append("enum.__format__")
        raise _HookError("__format__")

    def __getattr__(self, name: str) -> Any:
        _LOG.append(f"enum.__getattr__({name})")
        raise AttributeError(name)

    def __bool__(self) -> bool:
        _LOG.append("enum.__bool__")
        raise _HookError("__bool__")


@dataclasses.dataclass(frozen=True)
class _EnumMember:
    """Blueprint marker for a ``_HostileEnum`` member (the member itself can't
    sit in a strategy: Hypothesis would repr() it)."""

    name: str


class _Gen:
    """Blueprint marker: materializes to a fresh, never-started generator."""


class _ListIter:
    """Blueprint marker: materializes to a fresh ``iter([1, 2, 3])``."""


def _gen() -> Iterator[int]:
    yield 1
    yield 2


def _build_class(spec: HostileSpec, *, hashable: bool) -> type:
    label = f"{spec.base}{spec.hooks}"
    ns: dict[str, Any] = {h: _make_hook(label, h, spec.raises) for h in spec.hooks}
    if spec.base == "iterator":
        ns.setdefault("__iter__", _make_hook(label, "__iter__", spec.raises))
        ns.setdefault("__next__", _make_hook(label, "__next__", spec.raises))
    if hashable and "__hash__" not in ns:
        ns["__hash__"] = _plain_hash
    if "__eq__" in ns and "__hash__" not in ns:
        ns["__hash__"] = _plain_hash
    if spec.base in ("dataclass", "slots_dataclass"):
        return dataclasses.make_dataclass(
            "HostileDC",
            [
                ("a", object, dataclasses.field(default=None)),
                ("token", object, dataclasses.field(default=None)),
            ],
            namespace=ns,
            repr=False,
            eq=False,
            slots=spec.base == "slots_dataclass",
        )
    if spec.base in ("shadowed_dataclass", "shadowed_slots_dataclass"):
        slots = spec.base == "shadowed_slots_dataclass"
        base = dataclasses.make_dataclass(
            "ShadowBase",
            [
                ("a", object, dataclasses.field(default=None)),
                ("token", object, dataclasses.field(default=None)),
            ],
            repr=False,
            eq=False,
            slots=slots,
        )
        for field_name in ("a", "token"):
            getter = _make_hook(label, f"property:{field_name}", spec.raises)
            ns[field_name] = property(getter)
        if slots:
            ns["__slots__"] = ()
        return type("ShadowedDC", (base,), ns)
    builtin = {"object": object, "iterator": object}.get(spec.base)
    base_type = (
        builtin
        or {
            "list": list,
            "tuple": tuple,
            "dict": dict,
            "set": set,
            "frozenset": frozenset,
            "str": str,
            "int": int,
            "float": float,
            "bytes": bytes,
        }[spec.base]
    )
    return type(f"Hostile_{spec.base}", (base_type,), ns)


_CTOR_ARGS: dict[str, tuple[Any, ...]] = {
    "object": (),
    "iterator": (),
    "list": ([1, 2],),
    "tuple": ((1, 2),),
    "dict": ({"k": 1},),
    "set": ({1, 2},),
    "frozenset": (frozenset({1, 2}),),
    "str": ("s",),
    "int": (7,),
    "float": (1.5,),
    "bytes": (b"b",),
}


class _Materializer:
    """Turns a blueprint into a live value, remembering every lazy iterator it
    created so a test can check none was advanced."""

    def __init__(self) -> None:
        self.generators: list[Any] = []
        self.list_iters: list[Any] = []

    def __call__(self, bp: Any, *, hashable: bool = False) -> Any:
        if isinstance(bp, HostileSpec):
            return self._hostile(bp, hashable=hashable)
        if isinstance(bp, _EnumMember):
            return _HostileEnum[bp.name]
        if isinstance(bp, _Gen):
            g = _gen()
            self.generators.append(g)
            return g
        if isinstance(bp, _ListIter):
            it = iter([1, 2, 3])
            self.list_iters.append(it)
            return it
        t = type(bp)
        if t is list:
            return [self(x) for x in bp]
        if t is tuple:
            return tuple(self(x, hashable=hashable) for x in bp)
        if t is dict:
            return {self(k, hashable=True): self(v) for k, v in bp.items()}
        if t is set:
            return {self(x, hashable=True) for x in bp}
        if t is frozenset:
            return frozenset(self(x, hashable=True) for x in bp)
        return bp

    def _hostile(self, spec: HostileSpec, *, hashable: bool) -> Any:
        cls = _build_class(spec, hashable=hashable)
        if spec.base in ("dataclass", "slots_dataclass"):
            return cls(a=self(spec.fields[0]), token=self(spec.fields[1]))
        if spec.base in ("shadowed_dataclass", "shadowed_slots_dataclass"):
            return object.__new__(cls)
        return cls(*_CTOR_ARGS[spec.base])


_scalar_bp = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    # Past the int-digit limit, where repr() itself raises (trap #4).
    st.integers(4301, 4400).map(lambda digits: 10**digits),
    st.floats(),
    st.text(max_size=12),
    st.binary(max_size=12),
)
_hooks_st = st.lists(st.sampled_from(_SAFE_HOOKS), max_size=6, unique=True).map(tuple)
_simple_hostile = st.builds(
    HostileSpec,
    base=st.sampled_from([b for b in _BASES if "dataclass" not in b]),
    hooks=_hooks_st,
    raises=st.booleans(),
)
_field_bp = st.one_of(_scalar_bp, _simple_hostile, st.just(_Gen()), st.just(_ListIter()))
hostile_spec = st.one_of(
    _simple_hostile,
    st.builds(
        HostileSpec,
        base=st.sampled_from([b for b in _BASES if "dataclass" in b]),
        hooks=_hooks_st,
        raises=st.booleans(),
        fields=st.tuples(_field_bp, _field_bp),
    ),
)
_leaf_bp = st.one_of(
    _scalar_bp,
    hostile_spec,
    st.sampled_from(["A", "B"]).map(_EnumMember),
    st.builds(_Gen),
    st.builds(_ListIter),
)
_key_bp = st.one_of(st.text(max_size=8), st.integers(), hostile_spec)


def _bp_children(children: st.SearchStrategy[Any]) -> st.SearchStrategy[Any]:
    return st.one_of(
        st.lists(children, max_size=5),
        st.lists(children, max_size=5).map(tuple),
        st.dictionaries(_key_bp, children, max_size=5),
        st.sets(st.one_of(st.integers(), hostile_spec), max_size=5),
        st.frozensets(st.one_of(st.text(max_size=4), hostile_spec), max_size=5),
    )


hostile_blueprints = st.recursive(_leaf_bp, _bp_children, max_leaves=25)


def _materialize(bp: Any) -> tuple[Any, _Materializer]:
    mat = _Materializer()
    value = mat(bp)
    _LOG.clear()  # building sets/dict keys hashes their members — not safe_repr's doing
    return value, mat


# ===========================================================================
# 1. Bounded, and never raises
# ===========================================================================


@given(bp=hostile_blueprints, limits=limits_st)
def test_output_is_bounded_and_never_raises(bp: Any, limits: ValueCaptureLimits) -> None:
    value, _ = _materialize(bp)
    result = safe_repr(value, limits)
    assert isinstance(result, ReprResult)
    assert isinstance(result.text, str)
    assert isinstance(result.truncated, bool)
    assert len(result.text) <= limits.max_len


@given(value=plain_values, limits=limits_st)
def test_plain_output_is_bounded(value: Any, limits: ValueCaptureLimits) -> None:
    """A shorter-than-``repr`` limit is exactly where the clamp must hold."""
    text, _ = safe_repr(value, limits)
    assert len(text) <= limits.max_len


# ===========================================================================
# 2. Honest: the truncated flag, exactness, and the limits' exact boundaries
# ===========================================================================


@given(value=plain_values, limits=limits_st)
def test_untruncated_output_is_exactly_repr(value: Any, limits: ValueCaptureLimits) -> None:
    """``truncated`` is False exactly when nothing was elided: the text is then
    byte-identical to ``repr(value)``, and otherwise it differs."""
    text, truncated = safe_repr(value, limits)
    assert (text == repr(value)) is (not truncated)


@given(value=plain_values, max_items=st.integers(1, 6), max_depth=st.integers(1, 4))
def test_untruncated_output_is_exactly_repr_when_only_items_and_depth_bind(
    value: Any, max_items: int, max_depth: int
) -> None:
    """The same, with a length limit that never binds: an item or depth
    elision must set the flag itself, not lean on the final length clamp
    (whose own flag would otherwise mask a missing one — ``'[0, ...]'`` is
    longer than ``'[0, 1]'``)."""
    limits = ValueCaptureLimits(max_len=100_000, max_items=max_items, max_depth=max_depth)
    text, truncated = safe_repr(value, limits)
    assert (text == repr(value)) is (not truncated)


@given(value=plain_values)
def test_value_fits_at_its_exact_limits_and_not_one_below(value: Any) -> None:
    """Limits equal to the value's own length / nesting / widest container
    reproduce it exactly; lowering any one of them by one truncates it."""
    full = repr(value)
    depth, width = _nesting(value), _width(value)
    exact = ValueCaptureLimits(max_len=len(full), max_items=max(1, width), max_depth=max(1, depth))
    assert safe_repr(value, exact) == ReprResult(full, False)
    if len(full) >= 2:
        short = dataclasses.replace(exact, max_len=len(full) - 1)
        text, truncated = safe_repr(value, short)
        assert truncated
        assert len(text) <= len(full) - 1
    if width >= 2:
        assert safe_repr(value, dataclasses.replace(exact, max_items=width - 1)).truncated
    if depth >= 2:
        assert safe_repr(value, dataclasses.replace(exact, max_depth=depth - 1)).truncated


# --- depth / width: nothing past a limit ever reaches the text ----------------


def _shape_children(ch: st.SearchStrategy[Any]) -> st.SearchStrategy[Any]:
    return st.one_of(
        st.tuples(st.just("list"), st.lists(ch, max_size=5)),
        st.tuples(st.just("tuple"), st.lists(ch, max_size=5)),
        st.tuples(st.just("dict"), st.lists(ch, max_size=5)),
        st.tuples(st.just("set"), st.integers(0, 5)),
        st.tuples(st.just("frozenset"), st.integers(0, 5)),
    )


_shapes = st.recursive(st.just("leaf"), _shape_children, max_leaves=40)


class _LeafNumberer:
    """Builds a value from a shape with unique 7-digit int leaves, recording
    which leaves sit within the depth/width limits."""

    def __init__(self, limits: ValueCaptureLimits) -> None:
        self.next = 1_000_000
        self.limits = limits
        self.visible: set[str] = set()

    def leaf(self, depth: int, in_width: bool) -> int:
        n = self.next
        self.next += 1
        if in_width and depth <= self.limits.max_depth:
            self.visible.add(str(n))
        return n

    def build(self, shape: Any, depth: int = 0, in_width: bool = True) -> Any:
        if shape == "leaf":
            return self.leaf(depth, in_width)
        kind, body = shape
        w = self.limits.max_items
        if kind in ("set", "frozenset"):
            members = [self.next + i for i in range(body)]
            self.next += body
            container = set(members) if kind == "set" else frozenset(members)
            # Width is judged in the container's own iteration order.
            for i, m in enumerate(container):
                if in_width and i < w and depth + 1 <= self.limits.max_depth:
                    self.visible.add(str(m))
            return container
        if kind == "dict":
            out: dict[int, Any] = {}
            for i, child in enumerate(body):
                ok = in_width and i < w
                key = self.leaf(depth + 1, ok)
                out[key] = self.build(child, depth + 1, ok)
            return out
        items = [self.build(child, depth + 1, in_width and i < w) for i, child in enumerate(body)]
        return items if kind == "list" else tuple(items)


@given(shape=_shapes, limits=limits_st)
def test_no_leaf_beyond_depth_or_width_reaches_the_text(
    shape: Any, limits: ValueCaptureLimits
) -> None:
    numberer = _LeafNumberer(limits)
    value = numberer.build(shape)
    text, _ = safe_repr(value, dataclasses.replace(limits, max_len=10_000))
    shown = set(re.findall(r"(?<!\d)\d{7}(?!\d)", text))
    assert shown <= numberer.visible, shown - numberer.visible


# ===========================================================================
# 3. Never runs user code; never advances a lazy iterator
# ===========================================================================


@given(bp=hostile_blueprints, limits=limits_st)
def test_no_user_hook_is_ever_called(bp: Any, limits: ValueCaptureLimits) -> None:
    value, mat = _materialize(bp)
    safe_repr(value, limits)
    assert _LOG == []
    assert all(inspect.getgeneratorstate(g) == inspect.GEN_CREATED for g in mat.generators)
    assert all(list(it) == [1, 2, 3] for it in mat.list_iters)


@given(spec=hostile_spec, limits=limits_st, redact=st.booleans())
def test_format_arg_never_calls_a_user_hook(
    spec: HostileSpec, limits: ValueCaptureLimits, redact: bool
) -> None:
    value, _ = _materialize(spec)
    format_arg("value", value, limits=limits, redact=redact)
    assert _LOG == []


# ===========================================================================
# 4. Redaction: by name, before the value is ever touched
# ===========================================================================


def _spell(part: str, flips: list[bool], dash: bool) -> str:
    """One spelling of a sensitive part: per-character case flips, and "_"
    optionally written as "-" (the header style is_sensitive_name normalizes)."""
    chars = [c.upper() if flip else c for c, flip in zip(part, flips, strict=False)]
    word = "".join(chars) + part[len(chars) :]
    return word.replace("_", "-") if dash else word


_sensitive_name = st.builds(
    lambda part, flips, dash, pre, post: pre + _spell(part, flips, dash) + post,
    st.sampled_from(SENSITIVE_NAME_PARTS),
    st.lists(st.booleans(), max_size=16),
    st.booleans(),
    st.text(max_size=6),
    st.text(max_size=6),
)

# Values under a sensitive name may carry *any* hook, including the two
# instance hooks the rest of the module still calls (finding T8-1a): a
# redacted value is never touched at all, so not even those may fire.
_ALL_HOOKS = (*_SAFE_HOOKS, "__getattribute__", "__class__")


def _secret_value(hooks: tuple[str, ...]) -> Any:
    ns: dict[str, Any] = {}
    for h in hooks:
        if h == "__class__":
            ns[h] = property(_make_hook("secret", h, True))
        elif h == "__getattribute__":

            def __getattribute__(self: object, name: str) -> Any:  # noqa: N807
                _LOG.append(f"secret.__getattribute__({name})")
                raise _HookError(name)

            ns[h] = __getattribute__
        else:
            ns[h] = _make_hook("secret", h, True)
    if "__eq__" in ns:
        ns["__hash__"] = _plain_hash
    return type("Secret", (), ns)()


@given(
    name=_sensitive_name,
    hooks=st.lists(st.sampled_from(_ALL_HOOKS), max_size=5, unique=True).map(tuple),
    limits=limits_st,
    where=st.sampled_from(["dict_value", "dataclass_field", "format_arg"]),
)
def test_a_sensitive_name_redacts_before_the_value_is_touched(
    name: str, hooks: tuple[str, ...], limits: ValueCaptureLimits, where: str
) -> None:
    assert is_sensitive_name(name)
    secret = _secret_value(hooks)
    _LOG.clear()
    if where == "format_arg":
        assert format_arg(name, secret, limits=limits) == {
            "name": name,
            "repr": "<redacted>",
            "redacted": True,
        }
    elif where == "dict_value":
        text, _ = safe_repr({name: secret}, dataclasses.replace(limits, max_len=10_000))
        assert text.endswith(": <redacted>}")
    else:
        # Two fields: both must be within max_items to reach the secret one.
        wide = dataclasses.replace(limits, max_len=10_000, max_items=max(2, limits.max_items))
        text, _ = safe_repr(_SecretHolder(public=1, api_token=secret), wide)
        assert "api_token=<redacted>" in text
    assert _LOG == []


@dataclasses.dataclass
class _SecretHolder:
    public: object
    api_token: object


@given(name=st.text(max_size=20), value=plain_values, limits=limits_st, redact=st.booleans())
def test_format_arg_is_safe_repr_plus_name_based_redaction(
    name: str, value: Any, limits: ValueCaptureLimits, redact: bool
) -> None:
    arg = format_arg(name, value, limits=limits, redact=redact)
    if redact and is_sensitive_name(name):
        assert arg == {"name": name, "repr": "<redacted>", "redacted": True}
        return
    text, truncated = safe_repr(value, limits)
    expected: dict[str, Any] = {"name": name, "repr": text}
    if truncated:
        expected["truncated"] = True
    assert arg == expected


# ===========================================================================
# Finding T8-1 (ledgered): four ways user code still runs, or the bound slips
# ===========================================================================

_PLACEMENTS = ("bare", "list", "tuple", "dict_value", "dict_key", "set", "dataclass_field")


@dataclasses.dataclass
class _Box:
    item: object


def _place(obj: object, placement: str) -> object:
    if placement == "list":
        return [obj]
    if placement == "tuple":
        return (obj,)
    if placement == "dict_value":
        return {"k": obj}
    if placement == "dict_key":
        return {obj: 1}
    if placement == "set":
        return {obj}
    if placement == "dataclass_field":
        return _Box(obj)
    return obj


def _class_hook_object(hook: str) -> object:
    ns: dict[str, Any] = {"__hash__": _plain_hash}
    if hook == "__class__":

        def _class(self: object) -> type:
            _LOG.append("instance.__class__")
            return type(self)

        ns["__class__"] = property(_class)
    else:

        def __getattribute__(self: object, name: str) -> Any:  # noqa: N807
            _LOG.append(f"instance.__getattribute__({name})")
            return object.__getattribute__(self, name)

        ns["__getattribute__"] = __getattribute__
    return type("ClassHook", (), ns)()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T8-1(a): isinstance() on a captured value reads x.__class__ through the "
        "instance's own attribute lookup, so a __class__ property or a "
        "__getattribute__ override runs (5 times per value) — the very trap #3 "
        f"the module docstring says it avoids {_LEDGER}"
    ),
)
@given(
    hook=st.sampled_from(["__class__", "__getattribute__"]),
    placement=st.sampled_from(_PLACEMENTS),
    limits=limits_st,
)
@example(hook="__class__", placement="bare", limits=ValueCaptureLimits(1, 1, 1))
def test_instance_class_lookup_hooks_are_never_called(
    hook: str, placement: str, limits: ValueCaptureLimits
) -> None:
    value = _place(_class_hook_object(hook), placement)
    _LOG.clear()
    safe_repr(value, limits)
    assert _LOG == []


class _LoweringKey(str):
    __slots__ = ()

    def lower(self) -> str:
        _LOG.append("key.lower")
        return str.lower(self)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T8-1(b): the sensitive-key check calls key.lower() on a str-subclass "
        f"dict key, running the subclass's override {_LEDGER}"
    ),
)
@given(key=st.text(max_size=12), limits=limits_st)
@example(key="", limits=ValueCaptureLimits(1, 1, 1))
def test_str_subclass_dict_key_methods_are_never_called(
    key: str, limits: ValueCaptureLimits
) -> None:
    value = {_LoweringKey(key): 1}
    _LOG.clear()
    safe_repr(value, limits)
    assert _LOG == []


@dataclasses.dataclass
class _DictPropertyDC:
    a: int = 0

    @property
    def __dict__(self) -> dict[str, Any]:  # type: ignore[override]
        _LOG.append("dataclass.__dict__")
        return {"a": -1}


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T8-1(c): a dataclass field is read via object.__getattribute__(x, "
        "'__dict__'), which runs a __dict__ data descriptor defined on the "
        f"class (and trusts what it returns) {_LEDGER}"
    ),
)
@given(a=st.integers(), limits=limits_st)
@example(a=0, limits=ValueCaptureLimits(1, 1, 1))
def test_dataclass_dict_descriptor_is_never_called(a: int, limits: ValueCaptureLimits) -> None:
    value = _DictPropertyDC(a)
    _LOG.clear()
    safe_repr(value, limits)
    assert _LOG == []


def _internal_failure(*_args: Any) -> str:
    raise RuntimeError("dictionary changed size during iteration")


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T8-1(d): safe_repr's '<unreprable: TypeName>' fallback returns before "
        "the max_len clamp, so any internal failure (a raising hook, a dict "
        "mutated by another thread mid-capture) can exceed max_len — by the "
        f"full length of a user-controlled class name {_LEDGER}"
    ),
)
@given(value=plain_values.filter(lambda v: type(v) in value_repr._DISPATCH), limits=limits_st)
@example(value=None, limits=ValueCaptureLimits(1, 1, 1))
def test_an_internal_failure_still_honours_max_len(value: Any, limits: ValueCaptureLimits) -> None:
    """Fault injection: the dispatch handler for the value's type raises, the
    shape of a concurrent ``dict``/``set`` mutation during capture."""
    with mock.patch.dict(value_repr._DISPATCH, {type(value): _internal_failure}):
        text, truncated = safe_repr(value, limits)
    assert truncated
    assert len(text) <= limits.max_len
