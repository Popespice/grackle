"""Tests for RustRuntimeAdapter — no Rust toolchain required."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

if TYPE_CHECKING:
    from pathlib import Path

import pytest

from grackle.rust_runtime.adapter import RustRuntimeAdapter
from grackle.rust_runtime.errors import RustRuntimeError


@pytest.fixture()
def adapter() -> RustRuntimeAdapter:
    return RustRuntimeAdapter()


# ---------------------------------------------------------------------------
# Basic adapter attributes (no toolchain needed)
# ---------------------------------------------------------------------------


def test_language(adapter: RustRuntimeAdapter) -> None:
    assert adapter.language == "rust"


def test_extensions(adapter: RustRuntimeAdapter) -> None:
    assert ".rs" in adapter.extensions


def test_capabilities_no_toolchain(adapter: RustRuntimeAdapter) -> None:
    from grackle.rust_runtime import capability

    capability.reset_cache()
    with patch("shutil.which", return_value=None):
        capability.reset_cache()
        caps = adapter.capabilities()
        assert caps.runtime_tracing is False
    capability.reset_cache()


# ---------------------------------------------------------------------------
# runtime_unavailable_reason — gate logic
# ---------------------------------------------------------------------------


def test_rejects_tests_directory(adapter: RustRuntimeAdapter, tmp_path: Path) -> None:
    script = tmp_path / "tests" / "integration.rs"
    script.parent.mkdir()
    script.touch()
    reason = adapter.runtime_unavailable_reason(script)
    assert reason is not None
    assert "tests/" in reason


def test_rejects_benches_directory(adapter: RustRuntimeAdapter, tmp_path: Path) -> None:
    script = tmp_path / "benches" / "bench.rs"
    script.parent.mkdir()
    script.touch()
    reason = adapter.runtime_unavailable_reason(script)
    assert reason is not None
    assert "benches/" in reason


def test_ancestor_named_tests_not_rejected(adapter: RustRuntimeAdapter, tmp_path: Path) -> None:
    # A valid src/main.rs whose *ancestor* directory merely happens to be named
    # "tests" must not be rejected — only the immediate parent dir is a Cargo
    # test/bench convention. The reject heuristic keys on script.parent.name.
    from grackle.rust_runtime import capability

    script = tmp_path / "tests" / "myproj" / "src" / "main.rs"
    script.parent.mkdir(parents=True)
    script.touch()
    capability.reset_cache()
    with patch("shutil.which", return_value=None):
        capability.reset_cache()
        reason = adapter.runtime_unavailable_reason(script)
        # Gate is closed (no toolchain), but NOT because of the tests/ heuristic.
        assert reason is not None
        assert "tests/" not in reason
    capability.reset_cache()


def test_accepts_main_rs_when_toolchain_absent(adapter: RustRuntimeAdapter, tmp_path: Path) -> None:
    from grackle.rust_runtime import capability

    script = tmp_path / "src" / "main.rs"
    script.parent.mkdir()
    script.touch()
    capability.reset_cache()
    with patch("shutil.which", return_value=None):
        capability.reset_cache()
        reason = adapter.runtime_unavailable_reason(script)
        # Gate is closed due to missing toolchain, but the reason is about the
        # toolchain, not about rejecting the file path.
        assert reason is not None
        assert "llvm-tools" in reason.lower() or "rust" in reason.lower()
    capability.reset_cache()


def test_lib_rs_not_rejected_at_gate(adapter: RustRuntimeAdapter, tmp_path: Path) -> None:
    from grackle.rust_runtime import capability

    script = tmp_path / "src" / "lib.rs"
    script.parent.mkdir()
    script.touch()
    capability.reset_cache()
    with patch("shutil.which", return_value=None):
        capability.reset_cache()
        reason = adapter.runtime_unavailable_reason(script)
        # lib.rs is not gate-rejected by name; toolchain absence is the reason.
        assert reason is not None
        assert "tests/" not in (reason or "")
        assert "benches/" not in (reason or "")
    capability.reset_cache()


# ---------------------------------------------------------------------------
# trace_streaming always raises
# ---------------------------------------------------------------------------


def test_trace_streaming_raises(adapter: RustRuntimeAdapter, tmp_path: Path) -> None:
    from grackle.adapters.base import TraceOptions

    script = tmp_path / "src" / "main.rs"
    script.parent.mkdir()
    script.touch()
    with pytest.raises(RustRuntimeError, match="--stream"):
        adapter.trace_streaming(script, tmp_path, TraceOptions(), lambda _: None)


# ---------------------------------------------------------------------------
# Registry: .rs extension maps to "rust" runtime adapter
# ---------------------------------------------------------------------------


def test_extension_registered_in_registry() -> None:
    from grackle.adapters import registry

    ext_index = registry.runtime_extensions()
    assert ext_index.get(".rs") == "rust"
