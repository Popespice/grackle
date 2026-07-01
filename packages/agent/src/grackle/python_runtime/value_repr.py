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

Design: subclass ``reprlib.Repr`` (its ``_repr_iterable`` bounding and
level-driven recursion are safe and give cycle-safety for free — a level of 0
always renders ``'...'``), but override ``repr1`` itself with an
**exact-``type(x)``** dispatch table. Exact-type dispatch kills the entire
name-spoofing class in one move: a container *subclass* (or a class merely
named ``"list"``) never matches an exact-type key, so it falls all the way to
the fallback rung and is never iterated, sorted, or ``repr()``-called.

The dispatch ladder (in ``_SafeRepr.repr1``), most-specific first:

1. **Never-consume guard.** Generators/coroutines/async-generators, and any
   ``collections.abc.Iterator`` without ``__len__``, are rendered as a
   placeholder (``'<generator>'`` etc.) WITHOUT being iterated. Consuming a
   lazy iterator would change the *traced program's* behaviour — a Heisenbug
   in the debugger itself, not merely a repr concern.
2. **Exact scalars** (``int``, ``float``, ``complex``, ``bool``, ``None``,
   ``range``, ``slice``, ``str``) — safe C-level ``repr()`` calls, bounded/
   truncated; the int path is additionally guarded against the digit-limit
   ``ValueError``.
3. **Exact containers** (``list``, ``tuple``, ``dict``, ``set``,
   ``frozenset``) — bounded item count and nesting depth; dict/set/frozenset
   are rendered in **native iteration order**, never ``sorted()``, and a
   ``str`` dict key matching :func:`is_sensitive_name` redacts its value
   without touching it.
4. **Bytes-likes** (``bytes``, ``bytearray``, ``memoryview``) — sliced
   *before* any repr call (bounded work), never decoded (bytes are a
   redaction hotspot for tokens/keys).
5. **Dataclasses** — rendered field-by-field WITHOUT ever calling the
   dataclass's own ``__repr__``; field values are read via
   ``object.__getattribute__`` (bypassing a hostile ``__getattr__``) with a
   ``__slots__`` fallback through the class-level descriptor.
6. **Enum members** — ``ClassName.MEMBER`` via the ``_name_`` slot.
7. **numpy/pandas, by type name only (no import)** — a small shape/dtype
   summary for ``numpy.ndarray`` / ``pandas.DataFrame`` / ``pandas.Series``.
   Unlike every other rung, this one DOES read a few named attributes
   (``shape``, ``dtype``) via normal attribute access, so a class that
   deliberately spoofs one of these module/type names with a malicious
   ``@property`` can execute code here. This is an accepted, narrow
   trade-off (grackle traces the user's own local code — the traced program
   already runs with full privileges; this rung does not change that
   boundary) and is wrapped in ``try/except`` so a raising property still
   degrades cleanly to rung 8.
8. **Fallback** — ``'<module.ClassName object>'`` assembled purely from
   ``type(x)`` attributes. Replaces stock ``repr_instance`` entirely: safe
   mode never invokes an arbitrary ``__repr__``, which is the only
   in-process answer to a slow/hanging/side-effecting one (a timeout
   watchdog is out of scope).

Redaction: a value is replaced with ``'<redacted>'`` **before** any repr
machinery ever touches it, so a secret with a pathological ``__repr__`` is
never invoked. Name-based only (see :data:`SENSITIVE_NAME_PARTS`) — a secret
held in an innocuously-named field is not caught; content/entropy scanning is
out of scope.

Thread safety: :func:`safe_repr` constructs a fresh ``_SafeRepr`` instance
per call. Tracer callbacks fire on multiple threads (ADR-0013) and the
truncation flag is per-call mutable state; a shared formatter instance would
race.
"""

from __future__ import annotations

import dataclasses
import enum
import itertools
import reprlib
from collections.abc import Iterator
from types import AsyncGeneratorType, CoroutineType, GeneratorType
from typing import TYPE_CHECKING, Any, NamedTuple, NotRequired, TypedDict

if TYPE_CHECKING:
    from collections.abc import Callable

# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

# Case-insensitive substring match against a parameter/key/field name. Bare
# "auth" is deliberately excluded (would false-positive on "author");
# over-redaction is the accepted failure direction, not under-redaction.
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
    """True if *name* looks like it holds a credential (case-insensitive substring)."""
    lowered = name.lower()
    return any(part in lowered for part in SENSITIVE_NAME_PARTS)


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
    execute a property getter here. See the module docstring, rung 7.
    """
    key = (getattr(t, "__module__", ""), getattr(t, "__name__", ""))
    if key not in _ARRAY_LIKE_TYPES:
        return None
    name = t.__name__
    try:
        shape = getattr(x, "shape", None)
        dtype = getattr(x, "dtype", None)
        if shape is not None:
            return f"<{name} shape={tuple(shape)!r} dtype={dtype!r}>"
        length = len(x)
        return f"<{name} len={length} dtype={dtype!r}>"
    except Exception:
        return None


def _read_dataclass_field(x: Any, name: str) -> Any:
    """Read one dataclass field without invoking instance ``__getattr__``.

    Tries the instance ``__dict__`` first (the common, non-``__slots__``
    case), read via ``object.__getattribute__`` so a hostile
    ``__getattr__``/``__getattribute__`` override can't intercept it. Falls
    back to the class-level slot descriptor for ``slots=True`` dataclasses
    (a C-level ``member_descriptor.__get__`` — direct slot access, no user
    Python code runs). Returns :data:`_UNREADABLE` if neither works.
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
    if descriptor is not None and hasattr(type(descriptor), "__get__"):
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
        # bytes/bytearray append a " (len=N)" suffix after the preview; reserve
        # headroom so the top-level max_len clamp in safe_repr() doesn't clip
        # the suffix off a preview that was already at the full budget.
        self._bytes_preview_max = max(10, limits.max_len - 20)
        self._truncated = False
        self._dispatch: dict[type, Callable[[Any, int], str]] = {
            bool: lambda x, level: "True" if x else "False",
            type(None): lambda x, level: "None",
            int: self._repr_int_safe,
            float: self._repr_scalar_safe,
            complex: self._repr_scalar_safe,
            range: self._repr_scalar_safe,
            slice: self._repr_scalar_safe,
            str: self._repr_str_safe,
            bytes: self._repr_bytes_safe,
            bytearray: self._repr_bytes_safe,
            memoryview: self._repr_memoryview_safe,
            list: lambda x, level: self._repr_iterable_safe(x, level, "[", "]", self.maxlist),
            tuple: lambda x, level: self._repr_iterable_safe(
                x, level, "(", ")", self.maxtuple, ","
            ),
            dict: self._repr_dict_safe,
            set: lambda x, level: self._repr_set_safe(x, level, "set()", "{", "}", self.maxset),
            frozenset: lambda x, level: self._repr_set_safe(
                x, level, "frozenset()", "frozenset({", "})", self.maxfrozenset
            ),
        }

    # -- entry point (overrides reprlib.Repr.repr1) ------------------------

    def repr1(self, x: Any, level: int) -> str:
        if isinstance(x, _LAZY_TYPES):
            return _lazy_placeholder(x)
        t = type(x)
        if isinstance(x, Iterator) and not hasattr(t, "__len__"):
            return "<iterator>"
        handler = self._dispatch.get(t)
        if handler is not None:
            return handler(x, level)
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
        items = list(x)  # native order — never sorted() (no user __lt__ call)
        truncated_here = len(items) > maxitems
        pieces = [self.repr1(v, newlevel) for v in items[:maxitems]]
        if truncated_here:
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
        if len(s) > self.maxlong:
            self._truncated = True
            i = max(0, (self.maxlong - 3) // 2)
            j = max(0, self.maxlong - 3 - i)
            s = s[:i] + "..." + s[len(s) - j :]
        return s

    def _repr_scalar_safe(self, x: Any, level: int) -> str:
        s = repr(x)
        if len(s) > self.maxother:
            self._truncated = True
            i = max(0, (self.maxother - 3) // 2)
            j = max(0, self.maxother - 3 - i)
            s = s[:i] + "..." + s[len(s) - j :]
        return s

    def _repr_str_safe(self, x: Any, level: int) -> str:
        truncated = len(x) > self.maxstring
        s = repr(x[: self.maxstring])
        if len(s) > self.maxstring:
            truncated = True
            i = max(0, (self.maxstring - 3) // 2)
            j = max(0, self.maxstring - 3 - i)
            s = s[:i] + "..." + s[len(s) - j :]
        if truncated:
            self._truncated = True
        return s

    def _repr_bytes_safe(self, x: Any, level: int) -> str:
        n = len(x)
        limit = self._bytes_preview_max
        sliced = x[:limit]  # slice BEFORE repr — bounded work
        s = repr(sliced)
        truncated = n > limit
        if len(s) > limit:
            # Escaping (e.g. b'\xff') can expand the sliced preview well past
            # `limit` in chars — bound the rendered string too, same
            # double-check pattern as _repr_str_safe.
            truncated = True
            i = max(0, (limit - 3) // 2)
            j = max(0, limit - 3 - i)
            s = s[:i] + "..." + s[len(s) - j :]
        if truncated:
            self._truncated = True
        return f"{s} (len={n})"

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
            value = _read_dataclass_field(x, f.name)
            if value is _UNREADABLE:
                pieces.append(f"{f.name}=<unreadable>")
            elif is_sensitive_name(f.name):
                pieces.append(f"{f.name}=<redacted>")
            else:
                pieces.append(f"{f.name}={self.repr1(value, newlevel)}")
        return f"{name}({', '.join(pieces)})"


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
