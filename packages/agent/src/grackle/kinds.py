"""Kind registries for graph node and edge types.

Two module-level singletons (``node_kinds``, ``edge_kinds``) are pre-populated
with the default kinds at import time. Adapters may register additional kinds
by calling ``node_kinds.register(...)`` or ``edge_kinds.register(...)``
before or after import — the registries are thread-safe.

See ADR-0005 (kind registry design) for rationale.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

_INVALID_NAME_CHARS = frozenset("\n\r\t\0\v\f")


def _canonical_name(name: str) -> str:
    """Strip + lowercase ``name`` for use as a registry key.

    Raises ``ValueError`` if the result is empty or contains control characters.
    """
    key = name.strip().lower()
    if not key:
        raise ValueError("kind name must be non-empty after stripping whitespace")
    if any(c in _INVALID_NAME_CHARS for c in key):
        raise ValueError(
            f"kind name {name!r} contains control characters; use printable characters only"
        )
    return key


@dataclass(frozen=True, slots=True)
class NodeKind:
    """Metadata for a graph node kind."""

    name: str
    display_name: str
    color: str  # CSS custom property, e.g. "--color-node-file"
    shape: str  # "rounded-square" | "circle" | "diamond" | "dot"
    icon: str | None = None


@dataclass(frozen=True, slots=True)
class EdgeKind:
    """Metadata for a graph edge kind."""

    name: str
    display_name: str
    color: str  # CSS custom property, e.g. "--color-edge-import"
    style: str  # "solid" | "dashed" | "double"


class KindRegistry[T: (NodeKind, EdgeKind)]:
    """Thread-safe registry mapping canonical kind names to kind metadata.

    Generic over ``T`` — instantiate as ``KindRegistry[NodeKind]`` or
    ``KindRegistry[EdgeKind]``. The two module-level singletons are the
    intended entry points; creating additional registries is allowed but
    unusual.

    Note: the PEP 695 constraint ``T: (NodeKind, EdgeKind)`` is enforced by
    static type checkers only — at runtime, ``KindRegistry[NodeKind]`` and
    ``KindRegistry`` are the same class, and ``register()`` accepts any
    object with the duck-typed ``.name`` and ``.color`` attributes the
    method reads. Don't rely on isinstance enforcement.
    """

    def __init__(self) -> None:
        self._kinds: dict[str, T] = {}
        self._lock = threading.Lock()

    def register(self, kind: T) -> None:
        """Register ``kind`` under its canonical name.

        Raises:
            ValueError: if the name is invalid, the color is not a CSS custom
                property (``--color-*``), or a kind with the same canonical
                name is already registered.
        """
        key = _canonical_name(kind.name)
        if not kind.color.startswith("--color-"):
            raise ValueError(
                f"color {kind.color!r} must be a CSS custom property starting with '--color-'"
            )
        with self._lock:
            if key in self._kinds:
                raise ValueError(f"kind {key!r} already registered")
            self._kinds[key] = kind

    def get(self, name: str) -> T | None:
        """Return the kind for ``name`` (case-insensitive, strip-normalised), or ``None``."""
        with self._lock:
            return self._kinds.get(name.strip().lower())

    def known_names(self) -> list[str]:
        """Return sorted list of all registered canonical kind names."""
        with self._lock:
            return sorted(self._kinds)


node_kinds: KindRegistry[NodeKind] = KindRegistry()
edge_kinds: KindRegistry[EdgeKind] = KindRegistry()

# Default node kinds — tokens defined in packages/frontend/src/styles/tokens.css §13
node_kinds.register(
    NodeKind(name="file", display_name="File", color="--color-node-file", shape="rounded-square")
)
node_kinds.register(
    NodeKind(name="class", display_name="Class", color="--color-node-class", shape="circle")
)
node_kinds.register(
    NodeKind(
        name="function", display_name="Function", color="--color-node-function", shape="diamond"
    )
)
node_kinds.register(
    NodeKind(name="method", display_name="Method", color="--color-node-method", shape="dot")
)

# Phase 4 node kinds — TS/Go cross-language extension
node_kinds.register(
    NodeKind(
        name="interface", display_name="Interface", color="--color-node-interface", shape="circle"
    )
)
node_kinds.register(
    NodeKind(
        name="type_alias",
        display_name="Type Alias",
        color="--color-node-type_alias",
        shape="diamond",
    )
)
node_kinds.register(
    NodeKind(name="enum", display_name="Enum", color="--color-node-enum", shape="dot")
)
node_kinds.register(
    NodeKind(
        name="struct", display_name="Struct", color="--color-node-struct", shape="rounded-square"
    )
)

# Default edge kinds
edge_kinds.register(
    EdgeKind(name="import", display_name="Import", color="--color-edge-import", style="dashed")
)
edge_kinds.register(
    EdgeKind(name="call", display_name="Call", color="--color-edge-call", style="solid")
)
edge_kinds.register(
    EdgeKind(name="inherit", display_name="Inherit", color="--color-edge-inherit", style="double")
)
edge_kinds.register(
    EdgeKind(
        name="implements",
        display_name="Implements",
        color="--color-edge-implements",
        style="double",
    )
)
