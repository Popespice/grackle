"""End-to-end tests for the Rust runtime adapter (requires Rust toolchain + llvm-tools-preview).

Skipped automatically when the toolchain is not available. Tests both a
single-crate fixture (fixtures/tiny-rust-bin) and a Cargo workspace fixture
(fixtures/tiny-rust-app, crates/app/src/main.rs).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from grackle.rust_runtime import capability
from grackle.rust_runtime.adapter import RustRuntimeAdapter
from grackle.rust_runtime.errors import RustRuntimeError

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(
    not capability.rust_runtime_available(),
    reason="Rust toolchain with llvm-tools-preview not available",
)

from pathlib import Path as _Path  # noqa: E402 — guarded by pytestmark above

_BIN_FIXTURE = _Path(__file__).parents[4] / "fixtures" / "tiny-rust-bin"
_BIN_SCRIPT = _BIN_FIXTURE / "src" / "main.rs"

_WS_FIXTURE = _Path(__file__).parents[4] / "fixtures" / "tiny-rust-app"
_WS_SCRIPT = _WS_FIXTURE / "crates" / "app" / "src" / "main.rs"

# Functions that must appear in the single-crate trace.
_BIN_EXPECTED_EXECUTED = {
    "src/main.rs:main",
    "src/main.rs:greet",
    "src/calc.rs:add",
}
_BIN_EXPECTED_COLD = {"src/calc.rs:sub"}

# Functions that must appear in the workspace trace.
_WS_EXPECTED_EXECUTED = {
    "crates/app/src/main.rs:main",
    "crates/api/src/lib.rs:UserRepository.new",
    "crates/api/src/lib.rs:create_user_in_repo",
}
_WS_EXPECTED_COLD = {
    "crates/models/src/lib.rs:User.deactivate",
    "crates/models/src/lib.rs:default_user",
}


# ---------------------------------------------------------------------------
# Module-scoped fixtures — run each binary once per test session.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def bin_events() -> list[dict[str, Any]]:
    from grackle.adapters.base import TraceOptions

    adapter = RustRuntimeAdapter()
    return list(adapter.trace(_BIN_SCRIPT, _BIN_FIXTURE, TraceOptions()))  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def ws_events() -> list[dict[str, Any]]:
    from grackle.adapters.base import TraceOptions

    adapter = RustRuntimeAdapter()
    return list(adapter.trace(_WS_SCRIPT, _WS_FIXTURE, TraceOptions()))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Single-crate fixture assertions
# ---------------------------------------------------------------------------


def test_bin_all_events_are_call(bin_events: list[dict[str, Any]]) -> None:
    for e in bin_events:
        assert e["event"] == "call", f"unexpected event type: {e['event']}"


def test_bin_frame_depth_is_zero(bin_events: list[dict[str, Any]]) -> None:
    for e in bin_events:
        assert e["frame_depth"] == 0, f"unexpected frame_depth: {e['frame_depth']}"


def test_bin_metadata_count_positive_integer(bin_events: list[dict[str, Any]]) -> None:
    for e in bin_events:
        count = e.get("metadata", {}).get("count")
        assert isinstance(count, int) and count >= 1, f"bad count in {e}"


def test_bin_expected_nodes_present(bin_events: list[dict[str, Any]]) -> None:
    ids = {e["node_id"] for e in bin_events}
    missing = _BIN_EXPECTED_EXECUTED - ids
    assert not missing, f"expected node IDs missing from trace: {missing}"


def test_bin_cold_nodes_absent(bin_events: list[dict[str, Any]]) -> None:
    ids = {e["node_id"] for e in bin_events}
    leaked = _BIN_EXPECTED_COLD & ids
    assert not leaked, f"cold nodes unexpectedly present: {leaked}"


def test_bin_no_stdlib_leaks(bin_events: list[dict[str, Any]]) -> None:
    for e in bin_events:
        nid = e["node_id"]
        # Non-project paths are filtered to None by _normalize and never emitted.
        # An unresolved in-project path would appear as "<unresolved>", not a bare
        # project-relative ID — both indicate a resolution failure.
        assert not nid.startswith("<"), f"unresolved node leaked into trace: {nid}"
        assert not nid.startswith("/"), f"absolute path leaked as node ID: {nid}"


# ---------------------------------------------------------------------------
# Workspace fixture assertions
# ---------------------------------------------------------------------------


def test_ws_all_events_are_call(ws_events: list[dict[str, Any]]) -> None:
    for e in ws_events:
        assert e["event"] == "call", f"unexpected event type: {e['event']}"


def test_ws_frame_depth_is_zero(ws_events: list[dict[str, Any]]) -> None:
    for e in ws_events:
        assert e["frame_depth"] == 0, f"unexpected frame_depth: {e['frame_depth']}"


def test_ws_metadata_count_positive_integer(ws_events: list[dict[str, Any]]) -> None:
    for e in ws_events:
        count = e.get("metadata", {}).get("count")
        assert isinstance(count, int) and count >= 1, f"bad count in {e}"


def test_ws_expected_nodes_present(ws_events: list[dict[str, Any]]) -> None:
    ids = {e["node_id"] for e in ws_events}
    missing = _WS_EXPECTED_EXECUTED - ids
    assert not missing, f"expected node IDs missing from workspace trace: {missing}"


def test_ws_cold_nodes_absent(ws_events: list[dict[str, Any]]) -> None:
    ids = {e["node_id"] for e in ws_events}
    leaked = _WS_EXPECTED_COLD & ids
    assert not leaked, f"cold nodes unexpectedly present in workspace trace: {leaked}"


def test_ws_no_stdlib_leaks(ws_events: list[dict[str, Any]]) -> None:
    for e in ws_events:
        nid = e["node_id"]
        assert not nid.startswith("<"), f"unresolved node leaked into workspace trace: {nid}"
        assert not nid.startswith("/"), f"absolute path leaked as node ID: {nid}"


# ---------------------------------------------------------------------------
# trace_streaming always raises (even when toolchain is present)
# ---------------------------------------------------------------------------


def test_trace_streaming_raises() -> None:
    from grackle.adapters.base import TraceOptions

    adapter = RustRuntimeAdapter()
    with pytest.raises(RustRuntimeError, match="--stream"):
        adapter.trace_streaming(_BIN_SCRIPT, _BIN_FIXTURE, TraceOptions(), lambda _: None)


# ---------------------------------------------------------------------------
# CLI smoke test — grackle trace src/main.rs --root . --language rust -o out.jsonl
# ---------------------------------------------------------------------------


def test_cli_trace_single_crate_produces_jsonl(tmp_path: Path) -> None:
    from click.testing import CliRunner

    from grackle.cli import main

    out = tmp_path / "rust.jsonl"
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "trace",
            str(_BIN_SCRIPT),
            "--root",
            str(_BIN_FIXTURE),
            "--language",
            "rust",
            "-o",
            str(out),
        ],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    # Must not have fallen through to the generic "trace error:" catch-all.
    assert not result.output.startswith("trace error:"), result.output
    assert out.exists()
    lines = [json.loads(ln) for ln in out.read_text().splitlines() if ln.strip()]
    assert len(lines) > 0
    assert all(e["event"] == "call" for e in lines)
