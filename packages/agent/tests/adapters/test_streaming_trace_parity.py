"""Tests for RuntimeAdapter.streaming_trace_parity (Phase 12.0, D12.0.2).

Python's trace() and trace_streaming() emit the same per-call event stream, so
the CLI may substitute streaming delivery for collect-then-write under -o.
Node/Go/Rust must stay False: for Node, trace() (sampling profiler) and
trace_streaming() (precise-coverage polling) are different instruments; for
Go/Rust, trace_streaming() is unsupported entirely.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from grackle.adapters.base import Capabilities, RuntimeAdapter
from grackle.go_runtime.adapter import GoRuntimeAdapter
from grackle.node_runtime.adapter import NodeRuntimeAdapter
from grackle.python_runtime.adapter import PythonRuntimeAdapter
from grackle.rust_runtime.adapter import RustRuntimeAdapter

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from grackle.adapters.base import TraceEvent, TraceOptions


@pytest.mark.parametrize(
    ("adapter", "expected"),
    [
        (PythonRuntimeAdapter(), True),
        (NodeRuntimeAdapter(), False),
        (GoRuntimeAdapter(), False),
        (RustRuntimeAdapter(), False),
    ],
    ids=["python", "node", "go", "rust"],
)
def test_streaming_trace_parity_per_adapter(adapter: RuntimeAdapter, expected: bool) -> None:
    assert adapter.streaming_trace_parity is expected


def test_streaming_trace_parity_not_on_capabilities() -> None:
    """Deliberately not a Capabilities field (that dataclass is schema-mirrored
    with additionalProperties: false — see adapters/base.py)."""
    assert not hasattr(Capabilities(), "streaming_trace_parity")


class _MissingParityStub:
    """Satisfies every RuntimeAdapter member except streaming_trace_parity."""

    language: str = "stub"
    extensions: tuple[str, ...] = ()

    def capabilities(self) -> Capabilities:
        return Capabilities()

    def trace(self, script: Path, root: Path, options: TraceOptions) -> Iterator[TraceEvent]:
        yield from ()

    def trace_streaming(
        self,
        script: Path,
        root: Path,
        options: TraceOptions,
        sink: Callable[[TraceEvent], None],
    ) -> None:
        pass

    def runtime_unavailable_reason(self, script: Path) -> str | None:
        return None


def test_isinstance_check_rejects_adapter_missing_streaming_trace_parity() -> None:
    """A runtime_checkable Protocol isinstance check looks for attribute
    presence — an adapter that never sets streaming_trace_parity fails it,
    which is the enforcement mechanism for this attribute (registration
    itself does not isinstance-check, per adapters/registry.py)."""
    assert not isinstance(_MissingParityStub(), RuntimeAdapter)
