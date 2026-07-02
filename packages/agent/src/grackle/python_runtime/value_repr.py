"""Bounded, security-hardened value formatting for captured call args/returns.

Part of Phase 10 value capture (chunk 10.1; the wire-schema field and tracer
wiring that consume this module land in chunk 10.2, along with the forthcoming
ADR-0025). This module is pure — no tracer, wire, or CLI dependency — so the
most security-sensitive code of the phase is reviewable in isolation.

Why stock ``reprlib.Repr`` is not enough. Reading the stdlib source surfaces
five real traps for a hostile/pathological captured value:

1. ``repr1`` dispatches on ``type(x).__name__`` (a *string*) — a user class
   merely *named* ``"list"`` reaches ``repr_list`` and its ``__iter__``/
   ``__len__`` run. Name-spoofing hole.
2. ``repr_dict``/``repr_set``/``repr_frozenset`` call ``_possibly_sorted`` ->
   ``sorted(x)``, invoking user ``__lt__`` on keys — even for an exact
   builtin ``dict``, since keys can be any hashable object.
3. ``repr_instance`` calls ``builtins.repr(x)`` — an arbitrary, unbounded-time,
   possibly side-effecting user ``__repr__`` — and its exception fallback
   reads ``x.__class__`` (spoofable via a ``__class__`` property).
4. ``repr_int`` builds the full decimal string first; on Python >= 3.11 a
   huge int makes this **raise** ``ValueError`` (``sys.get_int_max_str_digits``,
   default 4300 digits), uncaught by stock reprlib.
5. ``bytes``/``bytearray`` have no ``repr_<typename>`` handler, so they fall
   to ``repr_instance`` and the FULL repr of e.g. 100 MB of bytes is
   materialized before any truncation is applied.

Design: subclass ``reprlib.Repr`` (its level-driven recursion is safe and
gives cycle-safety for free — a level of 0 always renders ``'...'``), but
override ``repr1`` itself with an **exact-``type(x)``** dispatch table built
ONCE at import time (module-level ``_DISPATCH``, not rebuilt per instance).
Exact-type dispatch kills the entire name-spoofing class in one move: a
container *subclass* (or a class merely named ``"list"``) never matches an
exact-type key, so it falls all the way to the fallback rung and is never
iterated, sorted, or ``repr()``-called.

``repr1`` also enforces a **total character budget** across the whole
recursive tree (``_chars_remaining``): without one, a ``max_depth``-deep,
``max_items``-wide structure of near-``max_len`` leaves can assemble orders
of magnitude more data than the final (correctly clamped) output ever shows.
Only LEAF results are charged against the budget — a container's own
assembled string already contains its children's content, which those
children's own ``repr1`` calls already charged, so charging the container's
full joined string too would double- (or, at depth N, N-times-) count the
same characters. Container/dataclass wrapper punctuation (brackets,
separators, ``'...'``) is left unbudgeted; it's already bounded by the
pre-existing ``maxitems``/``maxlevel`` item-count and depth caps, so it
can't itself blow up. Once the budget is exhausted, every further ``repr1``
call short-circuits to ``'...'`` for free, bounding total work to a small
constant multiple of ``max_len`` regardless of nesting depth or shape.

The dispatch ladder (in ``_SafeRepr.repr1`` / ``_DISPATCH``), most-specific
first:

1. **Never-consume guard.** Generators/coroutines/async-generators, and any
   ``collections.abc.Iterator`` without ``__len__``, are rendered as a
   placeholder (``'<generator>'`` etc.) WITHOUT being iterated. Consuming a
   lazy iterator would change the *traced program's* behaviour — a Heisenbug
   in the debugger itself, not merely a repr concern.
2. **Exact scalars** (``int``, ``float``, ``complex``, ``bool``, ``None``,
   ``str``) — safe C-level ``repr()`` calls, bounded/truncated; the int path
   is additionally guarded against the digit-limit ``ValueError``. ``range``/
   ``slice`` get their own handler that formats each of ``start``/``stop``/
   ``step`` through the same guarded int path (rather than calling
   ``repr(x)`` directly), since a huge-int bound hits the identical trap.
3. **Exact containers** (``list``, ``tuple``, ``dict``, ``set``,
   ``frozenset``) — bounded item count and nesting depth via ``itertools.
   islice`` (never a full ``list(x)`` materialization, even for ``set``/
   ``frozenset``); dict/set/frozenset are rendered in **native iteration
   order**, never ``sorted()``, and a ``str`` dict key matching
   :func:`is_sensitive_name` redacts its value without touching it.
4. **Bytes-likes** (``bytes``, ``bytearray``, ``memoryview``) — sliced
   *before* any repr call (bounded work), never decoded (bytes are a
   redaction hotspot for tokens/keys).
5. **Dataclasses** — rendered field-by-field WITHOUT ever calling the
   dataclass's own ``__repr__``. A sensitive-named field is redacted
   **before its value is read at all** — the read never happens, not merely
   its output. Field values are read via ``object.__getattribute__``
   (bypassing a hostile ``__getattr__``); the ``__slots__`` fallback reads
   the class-level descriptor ONLY when it is a genuine
   ``types.MemberDescriptorType`` (the C-level descriptor CPython generates
   for slots) — never a ``property``, ``cached_property``, or other
   user-defined descriptor, since those run arbitrary Python code.
6. **Enum members** — ``ClassName.MEMBER`` via the ``_name_`` slot.
7. **numpy/pandas, by type name only (no import)** — a small shape/dtype
   summary for ``numpy.ndarray`` / ``pandas.DataFrame`` / ``pandas.Series``,
   built ONLY from ``.shape``/``.dtype``. Unlike every other rung, this one
   DOES read named attributes via normal attribute access, so a class that
   deliberately spoofs one of these module/type names with a malicious
   ``@property`` can execute code here. This is an accepted, narrow
   trade-off (grackle traces the user's own local code — the traced program
   already runs with full privileges; this rung does not change that
   boundary), scoped to exactly ``.shape``/``.dtype`` (no ``len(x)``
   fallback — without a ``.shape`` there is nothing more to safely add, so
   this rung falls through to rung 8 rather than reaching for a third,
   undocumented attribute), and wrapped in ``try/except`` so a raising
   property still degrades cleanly.
8. **Fallback** — ``'<module.ClassName object>'`` assembled purely from
   ``type(x)`` attributes. Replaces stock ``repr_instance`` entirely: safe
   mode never invokes an arbitrary ``__repr__``, which is the only
   in-process answer to a slow/hanging/side-effecting one (a timeout
   watchdog is out of scope).

Redaction: a value is replaced with ``'<redacted>'`` **before** any repr
machinery ever touches it, so a secret with a pathological ``__repr__`` (or a
descriptor-backed field) is never invoked. Name-based only (see
:data:`SENSITIVE_NAME_PARTS`) — matched case-insensitively with ``-``
normalized to ``_`` first (an HTTP-header-style name like ``"X-Api-Key"``
must redact exactly like ``"x_api_key"``). A secret held in an innocuously
named field is not caught; content/entropy scanning is out of scope.

Thread safety: :func:`safe_repr` constructs a fresh ``_SafeRepr`` instance
per call. Tracer callbacks fire on multiple threads (ADR-0013) and both the
truncation flag and the character budget are per-call mutable state; a
shared formatter instance would race.
"""

from __future__ import annotations

import dataclasses
import enum
import itertools
import reprlib
from collections.abc import Iterator
from types import AsyncGeneratorType, CoroutineType, GeneratorType, MemberDescriptorType
from typing import TYPE_CHECKING, Any, NamedTuple, NotRequired, TypedDict

if TYPE_CHECKING:
    from collections.abc import Callable

# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

# Case-insensitive substring match against a parameter/key/field name (after
# normalizing "-" to "_", so HTTP-header-style names like "X-Api-Key" match
# the same literal as "api_key"). Bare "auth" is deliberately excluded (would
# false-positive on "author"); over-redaction is the accepted failure
# direction, not under-redaction.
SENSITIVE_NAME_PARTS: tuple[str, ...] = (
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "api_key",
    "apikey",
    "private_key",
    "secret_key",
    "access_key",
    "signing_key",
    "authorization",
    "auth_token",
    "bearer",
)


def is_sensitive_name(name: str) -> bool:
    """True if *name* looks like it holds a credential.

    Case-insensitive, separator-insensitive substring match: "-" is
    normalized to "_" before matching so a header-style name like
    "X-Api-Key" redacts exactly like "x_api_key".
    """
    normalized = name.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_NAME_PARTS)


# ---------------------------------------------------------------------------
# Limits & results
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class ValueCaptureLimits:
    """Bounds applied when formatting one captured value.

    Attributes:
        max_len: Hard clamp on the final assembled repr string. Applied even
            when every nested piece is individually within bounds — many
            short items can still add up to a long string.
        max_items: Collection items shown per container (maps to reprlib's
            ``max*`` attributes) and dataclass fields shown per instance.
        max_depth: Nesting levels shown before elision (maps to reprlib's
            ``maxlevel``).
    """

    max_len: int = 120
    max_items: int = 10
    max_depth: int = 3


DEFAULT_LIMITS = ValueCaptureLimits()

# Total-character budget = max(max_len * _CHAR_BUDGET_MULTIPLE,
# _CHAR_BUDGET_FLOOR). Generous enough that no legitimate small/medium
# output is affected, while capping a pathological max_items**max_depth
# blowup to a small constant multiple of max_len.
_CHAR_BUDGET_MULTIPLE = 10
_CHAR_BUDGET_FLOOR = 1000


class ReprResult(NamedTuple):
    """The formatted text and whether any content was elided to produce it."""

    text: str
    truncated: bool


class ArgValue(TypedDict):
    """One formatted argument, shaped for the wire (chunk 10.2).

    ``redacted``/``truncated`` are omitted (not set to ``False``) when they
    don't apply — every absent key is bytes saved through the JSONL pipeline.
    """

    name: str
    repr: str
    redacted: NotRequired[bool]
    truncated: NotRequired[bool]


# ---------------------------------------------------------------------------
# Never-consume guard (generators, coroutines, lazy iterators)
# ---------------------------------------------------------------------------

_LAZY_TYPES: tuple[type, ...] = (GeneratorType, CoroutineType, AsyncGeneratorType)


def _lazy_placeholder(x: object) -> str:
    if isinstance(x, CoroutineType):
        return "<coroutine>"
    if isinstance(x, AsyncGeneratorType):
        return "<async_generator>"
    return "<generator>"


# Exact types whose repr1 dispatch recurses into self.repr1 for children
# (so their own assembled string must NOT be separately charged against the
# character budget — see repr1's is_recursive check). Dataclasses are
# checked separately (they're user-defined types, not a fixed set).
_RECURSIVE_DISPATCH_TYPES: frozenset[type] = frozenset(
    {list, tuple, dict, set, frozenset, range, slice}
)


# ---------------------------------------------------------------------------
# Module-level helpers (no formatter state needed)
# ---------------------------------------------------------------------------

# (module, type name) pairs recognised for the array-like summary rung.
# Matched by name only — numpy/pandas are never imported (no new deps).
_ARRAY_LIKE_TYPES: frozenset[tuple[str, str]] = frozenset(
    {
        ("numpy", "ndarray"),
        ("pandas.core.frame", "DataFrame"),
        ("pandas.core.series", "Series"),
        ("pandas", "DataFrame"),
        ("pandas", "Series"),
    }
)

# Sentinel distinguishing "field absent/unreadable" from a real ``None`` value.
_UNREADABLE = object()


def _clamp_middle(s: str, limit: int) -> tuple[str, bool]:
    """Truncate *s* to at most *limit* chars, eliding the middle with '...'.

    Shared by every scalar handler that needs the "keep the ends, elide the
    middle" truncation shape (int/float/complex/str/bytes previews), so the
    slice math lives in exactly one place. Returns ``(text, truncated)``.
    """
    if len(s) <= limit:
        return s, False
    i = max(0, (limit - 3) // 2)
    j = max(0, limit - 3 - i)
    return s[:i] + "..." + s[len(s) - j :], True


def _repr_fallback(t: type) -> str:
    """The rung-8 fallback: assembled from ``type(x)`` attributes only, never
    from an instance method call."""
    module = getattr(t, "__module__", "")
    qualname = getattr(t, "__qualname__", getattr(t, "__name__", "object"))
    if module in ("builtins", "__main__", ""):
        return f"<{qualname} object>"
    return f"<{module}.{qualname} object>"


def _repr_enum_safe(x: Any, t: type) -> str:
    try:
        # object.__getattribute__ bypasses a hostile instance __getattr__;
        # _name_ is the plain instance attribute Enum itself relies on.
        member_name = object.__getattribute__(x, "_name_")
    except Exception:
        return _repr_fallback(t)
    qualname = getattr(t, "__qualname__", getattr(t, "__name__", "Enum"))
    return f"{qualname}.{member_name}"


def _repr_module_special(x: Any, t: type) -> str | None:
    """Best-effort numpy/pandas summary. Returns ``None`` (caller falls
    through to rung 8) on any failure, including a raising ``shape``/``dtype``
    property on a class that spoofs the module/type name.

    Unlike every other rung this one does read instance attributes by name
    (``shape``, ``dtype``), so — narrowly — a deliberately spoofed class can
    execute a property getter here. See the module docstring, rung 7. Reads
    ONLY these two names: without a ``shape`` there is nothing more to
    safely summarize, so this returns ``None`` rather than reaching for
    ``len(x)`` as a third, undocumented attribute access.
    """
    key = (getattr(t, "__module__", ""), getattr(t, "__name__", ""))
    if key not in _ARRAY_LIKE_TYPES:
        return None
    name = t.__name__
    try:
        shape = getattr(x, "shape", None)
        if shape is None:
            return None
        dtype = getattr(x, "dtype", None)
        return f"<{name} shape={tuple(shape)!r} dtype={dtype!r}>"
    except Exception:
        return None


def _read_dataclass_field(x: Any, name: str) -> Any:
    """Read one dataclass field without invoking instance ``__getattr__``.

    Tries the instance ``__dict__`` first (the common, non-``__slots__``
    case), read via ``object.__getattribute__`` so a hostile
    ``__getattr__``/``__getattribute__`` override can't intercept it. Falls
    back to the class-level slot descriptor for ``slots=True`` dataclasses,
    but ONLY when it is a genuine ``types.MemberDescriptorType`` (the exact
    C-level type CPython uses for ``__slots__`` attributes) — a ``property``,
    ``cached_property``, or any other descriptor with a Python-level
    ``__get__`` is rejected, since calling it would run arbitrary user code.
    Returns :data:`_UNREADABLE` if neither works.

    Callers must check :func:`is_sensitive_name` on the field name BEFORE
    calling this — redaction has to happen before the value is read, not
    just before it's shown, so a sensitive descriptor-backed field is never
    invoked at all.
    """
    try:
        d = object.__getattribute__(x, "__dict__")
    except AttributeError:
        d = None
    if d is not None:
        try:
            return d[name]
        except KeyError:
            return _UNREADABLE
    descriptor = type(x).__dict__.get(name)
    if type(descriptor) is MemberDescriptorType:
        try:
            return descriptor.__get__(x, type(x))
        except Exception:
            return _UNREADABLE
    return _UNREADABLE


# ---------------------------------------------------------------------------
# The formatter
# ---------------------------------------------------------------------------


class _SafeRepr(reprlib.Repr):
    """Bounded, exact-type-dispatched repr. See the module docstring for the
    full ladder and the traps this replaces in stock ``reprlib.Repr``."""

    def __init__(self, limits: ValueCaptureLimits) -> None:
        super().__init__()
        self.maxlevel = limits.max_depth
        self.maxstring = limits.max_len
        self.maxlong = limits.max_len
        self.maxother = limits.max_len
        self.maxlist = limits.max_items
        self.maxtuple = limits.max_items
        self.maxdict = limits.max_items
        self.maxset = limits.max_items
        self.maxfrozenset = limits.max_items
        self._truncated = False
        # Total characters this formatter will assemble across the whole
        # recursive tree (see module docstring) — bounds total work to a
        # small constant multiple of max_len instead of max_items**max_depth.
        self._chars_remaining = max(limits.max_len * _CHAR_BUDGET_MULTIPLE, _CHAR_BUDGET_FLOOR)

    # -- entry point (overrides reprlib.Repr.repr1) ------------------------

    def repr1(self, x: Any, level: int) -> str:
        if self._chars_remaining <= 0:
            self._truncated = True
            return "..."
        result = self._repr1_dispatch(x, level)
        # Only charge LEAF results against the budget. A container's own
        # assembled string already contains its children's content, which
        # those children's own repr1 calls already charged — charging the
        # container's full joined string here too would double- (or, at
        # depth N, N-times-) count the same characters, making the budget's
        # effective depth-independence claim false. Container/dataclass
        # wrapper punctuation (brackets, separators, "...") is left
        # unbudgeted — it's already bounded by the pre-existing maxitems/
        # maxlevel item-count and depth caps, so it can't itself blow up.
        t = type(x)
        is_recursive = t in _RECURSIVE_DISPATCH_TYPES or (
            dataclasses.is_dataclass(t) and not isinstance(x, type)
        )
        if not is_recursive:
            self._chars_remaining -= len(result)
        return result

    def _repr1_dispatch(self, x: Any, level: int) -> str:
        if isinstance(x, _LAZY_TYPES):
            return _lazy_placeholder(x)
        t = type(x)
        if isinstance(x, Iterator) and not hasattr(t, "__len__"):
            return "<iterator>"
        handler = _DISPATCH.get(t)
        if handler is not None:
            return handler(self, x, level)
        if dataclasses.is_dataclass(t) and not isinstance(x, type):
            return self._repr_dataclass_safe(x, level)
        if isinstance(x, enum.Enum):
            return _repr_enum_safe(x, t)
        special = _repr_module_special(x, t)
        if special is not None:
            return special
        return _repr_fallback(t)

    # -- per-type handlers --------------------------------------------------

    def _repr_iterable_safe(
        self, x: Any, level: int, left: str, right: str, maxiter: int, trail: str = ""
    ) -> str:
        """Own implementation of reprlib's ``_repr_iterable`` (exact list/tuple
        only, reached here via exact-type dispatch): bounded item count and
        nesting depth, recursing through :meth:`repr1`. Reimplemented rather
        than calling the inherited private method because typeshed's
        ``reprlib.Repr`` stub does not declare it.
        """
        n = len(x)
        if level <= 0 and n:
            self._truncated = True
            return f"{left}...{right}"
        newlevel = level - 1
        pieces = [self.repr1(elem, newlevel) for elem in itertools.islice(x, maxiter)]
        if n > maxiter:
            pieces.append("...")
            self._truncated = True
        s = ", ".join(pieces)
        if n == 1 and trail:
            right = trail + right
        return f"{left}{s}{right}"

    def _repr_dict_safe(self, x: Any, level: int) -> str:
        n = len(x)
        if n == 0:
            return "{}"
        if level <= 0:
            self._truncated = True
            return "{...}"
        newlevel = level - 1
        pieces: list[str] = []
        for shown, (key, val) in enumerate(x.items()):
            if shown >= self.maxdict:
                pieces.append("...")
                self._truncated = True
                break
            keyrepr = self.repr1(key, newlevel)
            if isinstance(key, str) and is_sensitive_name(key):
                valrepr = "<redacted>"
            else:
                valrepr = self.repr1(val, newlevel)
            pieces.append(f"{keyrepr}: {valrepr}")
        return "{" + ", ".join(pieces) + "}"

    def _repr_set_safe(
        self, x: Any, level: int, empty: str, left: str, right: str, maxitems: int
    ) -> str:
        if not x:
            return empty
        if level <= 0:
            self._truncated = True
            return f"{left}...{right}"
        newlevel = level - 1
        # x is guaranteed an exact set/frozenset (reached only via exact-type
        # dispatch), so len(x) is O(1)/C-level and itertools.islice never
        # materializes more than maxitems elements — no list(x) copy of the
        # whole collection before truncating.
        n = len(x)
        pieces = [self.repr1(v, newlevel) for v in itertools.islice(x, maxitems)]
        if n > maxitems:
            pieces.append("...")
            self._truncated = True
        return f"{left}{', '.join(pieces)}{right}"

    def _repr_int_safe(self, x: Any, level: int) -> str:
        try:
            s = repr(x)
        except ValueError:
            # sys.get_int_max_str_digits (3.11+): building the decimal string
            # of a huge int raises. bit_length() is O(1) and safe.
            self._truncated = True
            return f"<int ~{x.bit_length()} bits>"
        s, truncated = _clamp_middle(s, self.maxlong)
        if truncated:
            self._truncated = True
        return s

    def _repr_scalar_safe(self, x: Any, level: int) -> str:
        s, truncated = _clamp_middle(repr(x), self.maxother)
        if truncated:
            self._truncated = True
        return s

    def _repr_range_or_slice_safe(self, name: str, x: Any) -> str:
        """Safe, bounded repr for range/slice.

        Formats each of start/stop/step through the full ``repr1`` ladder
        rather than calling ``repr(x)`` directly (which would hit the same
        ``sys.get_int_max_str_digits`` trap ``int`` is guarded against for a
        huge-int bound) and rather than assuming every component is an
        ``int``/``None`` — true for ``range`` (enforced at construction),
        but NOT for ``slice``, which accepts an object of any type. Routing
        every component through ``self.repr1`` handles a real int via the
        guarded int path and anything else through the same safe ladder as
        every other value, instead of assuming a type that could be wrong.
        """
        parts = []
        for component in (x.start, x.stop, x.step):
            parts.append("None" if component is None else self.repr1(component, 0))
        return f"{name}({', '.join(parts)})"

    def _repr_str_safe(self, x: Any, level: int) -> str:
        pre_truncated = len(x) > self.maxstring
        s, post_truncated = _clamp_middle(repr(x[: self.maxstring]), self.maxstring)
        if pre_truncated or post_truncated:
            self._truncated = True
        return s

    def _repr_bytes_safe(self, x: Any, level: int) -> str:
        n = len(x)
        # Reserve room for the " (len=N)" suffix based on N's ACTUAL digit
        # count (not a fixed guess) so the top-level max_len clamp never has
        # to cut into it.
        suffix = f" (len={n})"
        limit = max(0, self.maxstring - len(suffix))
        pre_truncated = n > limit
        preview, post_truncated = _clamp_middle(repr(x[:limit]), limit)
        if pre_truncated or post_truncated:
            self._truncated = True
        result = f"{preview}{suffix}"
        if len(result) > self.maxstring:
            # The suffix alone is longer than maxstring (an even empty
            # preview can't make room for it) — omit it entirely rather
            # than let a partial " (len=" fragment survive.
            self._truncated = True
            preview_only, _ = _clamp_middle(repr(x[: self.maxstring]), self.maxstring)
            return preview_only
        return result

    def _repr_memoryview_safe(self, x: Any, level: int) -> str:
        try:
            fmt = x.format
            shape = x.shape
        except Exception:
            return "<memoryview>"
        return f"<memoryview format={fmt!r} shape={shape!r}>"

    def _repr_dataclass_safe(self, x: Any, level: int) -> str:
        t = type(x)
        name = getattr(t, "__qualname__", getattr(t, "__name__", "object"))
        if level <= 0:
            self._truncated = True
            return f"{name}(...)"
        try:
            fields = dataclasses.fields(t)
        except Exception:
            return _repr_fallback(t)
        newlevel = level - 1
        pieces: list[str] = []
        for shown, f in enumerate(fields):
            if shown >= self.maxdict:
                pieces.append("...")
                self._truncated = True
                break
            if is_sensitive_name(f.name):
                # Redact BEFORE reading the field at all — a sensitive
                # field backed by a descriptor must never have its __get__
                # invoked, not merely have its output hidden.
                pieces.append(f"{f.name}=<redacted>")
                continue
            value = _read_dataclass_field(x, f.name)
            if value is _UNREADABLE:
                pieces.append(f"{f.name}=<unreadable>")
            else:
                pieces.append(f"{f.name}={self.repr1(value, newlevel)}")
        return f"{name}({', '.join(pieces)})"


# ---------------------------------------------------------------------------
# Dispatch table (built ONCE at import time — not per _SafeRepr instance).
# Every handler reads per-instance config off the passed-in formatter at call
# time, so the type -> handler mapping itself is instance-invariant.
# ---------------------------------------------------------------------------


def _repr_bool_safe(formatter: _SafeRepr, x: Any, level: int) -> str:
    return "True" if x else "False"


def _repr_none_safe(formatter: _SafeRepr, x: Any, level: int) -> str:
    return "None"


def _repr_list_dispatch(formatter: _SafeRepr, x: Any, level: int) -> str:
    return formatter._repr_iterable_safe(x, level, "[", "]", formatter.maxlist)


def _repr_tuple_dispatch(formatter: _SafeRepr, x: Any, level: int) -> str:
    return formatter._repr_iterable_safe(x, level, "(", ")", formatter.maxtuple, ",")


def _repr_set_dispatch(formatter: _SafeRepr, x: Any, level: int) -> str:
    return formatter._repr_set_safe(x, level, "set()", "{", "}", formatter.maxset)


def _repr_frozenset_dispatch(formatter: _SafeRepr, x: Any, level: int) -> str:
    return formatter._repr_set_safe(
        x, level, "frozenset()", "frozenset({", "})", formatter.maxfrozenset
    )


def _repr_range_dispatch(formatter: _SafeRepr, x: Any, level: int) -> str:
    return formatter._repr_range_or_slice_safe("range", x)


def _repr_slice_dispatch(formatter: _SafeRepr, x: Any, level: int) -> str:
    return formatter._repr_range_or_slice_safe("slice", x)


_DISPATCH: dict[type, Callable[[Any, Any, int], str]] = {
    bool: _repr_bool_safe,
    type(None): _repr_none_safe,
    int: _SafeRepr._repr_int_safe,
    float: _SafeRepr._repr_scalar_safe,
    complex: _SafeRepr._repr_scalar_safe,
    range: _repr_range_dispatch,
    slice: _repr_slice_dispatch,
    str: _SafeRepr._repr_str_safe,
    bytes: _SafeRepr._repr_bytes_safe,
    bytearray: _SafeRepr._repr_bytes_safe,
    memoryview: _SafeRepr._repr_memoryview_safe,
    list: _repr_list_dispatch,
    tuple: _repr_tuple_dispatch,
    dict: _SafeRepr._repr_dict_safe,
    set: _repr_set_dispatch,
    frozenset: _repr_frozenset_dispatch,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def safe_repr(value: object, limits: ValueCaptureLimits = DEFAULT_LIMITS) -> ReprResult:
    """Format *value* into a bounded string that never calls arbitrary user
    code and never consumes a lazy iterator.

    Never raises: any unexpected internal failure degrades to a generic
    ``<unreprable: ClassName>`` result rather than propagating and disrupting
    the tracer's hot path.
    """
    formatter = _SafeRepr(limits)
    try:
        text = formatter.repr(value)
        truncated = formatter._truncated
    except Exception:
        return ReprResult(f"<unreprable: {type(value).__name__}>", True)
    if len(text) > limits.max_len:
        truncated = True
        cut = max(0, limits.max_len - 3)
        text = text[:cut] + "..." if limits.max_len > 3 else text[: limits.max_len]
    return ReprResult(text, truncated)


def format_arg(
    name: str,
    value: object,
    *,
    limits: ValueCaptureLimits = DEFAULT_LIMITS,
    redact: bool = True,
) -> ArgValue:
    """Format one named argument for the wire, redacting by name before any
    repr machinery runs (a secret with a pathological ``__repr__`` is never
    invoked)."""
    if redact and is_sensitive_name(name):
        return {"name": name, "repr": "<redacted>", "redacted": True}
    text, truncated = safe_repr(value, limits)
    result: ArgValue = {"name": name, "repr": text}
    if truncated:
        result["truncated"] = True
    return result
