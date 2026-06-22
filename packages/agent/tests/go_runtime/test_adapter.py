"""Non-gated adapter-behaviour tests (no Go toolchain required).

These exercise GoRuntimeAdapter paths that do not invoke the toolchain: the
input gate (`runtime_unavailable_reason`) and the unsupported streaming channel.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from grackle.adapters.base import TraceOptions
from grackle.go_runtime import capability
from grackle.go_runtime.adapter import GoRuntimeAdapter
from grackle.go_runtime.errors import GoRuntimeError

if TYPE_CHECKING:
    from pathlib import Path


def test_language_and_extensions() -> None:
    adapter = GoRuntimeAdapter()
    assert adapter.language == "go"
    assert adapter.extensions == (".go",)


def test_test_go_rejected(tmp_path: Path) -> None:
    """A *_test.go input is rejected at the gate before any toolchain probe."""
    adapter = GoRuntimeAdapter()
    # Force the toolchain gate OPEN so we prove the _test.go branch fires first.
    with patch.object(capability, "go_runtime_available", return_value=True):
        reason = adapter.runtime_unavailable_reason(tmp_path / "user_test.go")
    assert reason is not None
    assert "test" in reason.lower()
    assert "go test" in reason


def test_plain_go_passes_gate_when_toolchain_present(tmp_path: Path) -> None:
    adapter = GoRuntimeAdapter()
    with patch.object(capability, "go_runtime_available", return_value=True):
        assert adapter.runtime_unavailable_reason(tmp_path / "main.go") is None


def test_gate_closed_returns_remediation(tmp_path: Path) -> None:
    adapter = GoRuntimeAdapter()
    with (
        patch.object(capability, "go_runtime_available", return_value=False),
        patch.object(capability, "remediation_message", return_value="install go"),
    ):
        assert adapter.runtime_unavailable_reason(tmp_path / "main.go") == "install go"


def test_trace_streaming_raises(tmp_path: Path) -> None:
    """The live-stream channel is unsupported for Go and raises a typed error."""
    adapter = GoRuntimeAdapter()
    with pytest.raises(GoRuntimeError, match="--stream"):
        adapter.trace_streaming(tmp_path / "main.go", tmp_path, TraceOptions(), lambda _: None)
