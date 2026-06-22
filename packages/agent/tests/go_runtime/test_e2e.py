"""End-to-end tests for the Go runtime adapter (requires Go >= 1.20).

Skipped automatically when Go is not available. Reuses fixtures/tiny-go-app/.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from grackle.go_runtime import capability
from grackle.go_runtime.adapter import GoRuntimeAdapter
from grackle.go_runtime.errors import GoRuntimeError

pytestmark = pytest.mark.skipif(
    not capability.go_runtime_available(),
    reason="Go >= 1.20 not available",
)

_FIXTURE = Path(__file__).parents[4] / "fixtures" / "tiny-go-app"
_SCRIPT = _FIXTURE / "main.go"

_EXPECTED_EXECUTED = {
    "main.go:main",
    "models/user.go:NewUser",
    "models/user.go:User.Print",
    "services/service.go:NewUserService",
    "services/service.go:UserService.AddUser",
}

_EXPECTED_COLD = {
    "utils/helpers.go:Contains",
    "utils/helpers.go:Reverse",
}


@pytest.fixture(scope="module")
def trace_events() -> list[dict[str, Any]]:
    """Run the Go adapter against the tiny-go-app fixture once per test session."""
    from grackle.adapters.base import TraceOptions

    adapter = GoRuntimeAdapter()
    options = TraceOptions()
    return list(adapter.trace(_SCRIPT, _FIXTURE, options))  # type: ignore[arg-type]


def test_all_events_are_call(trace_events: list[dict[str, Any]]) -> None:
    for e in trace_events:
        assert e["event"] == "call", f"unexpected event type: {e['event']}"


def test_frame_depth_is_zero(trace_events: list[dict[str, Any]]) -> None:
    for e in trace_events:
        assert e["frame_depth"] == 0, f"unexpected frame_depth: {e['frame_depth']}"


def test_metadata_count_positive_integer(trace_events: list[dict[str, Any]]) -> None:
    for e in trace_events:
        count = e.get("metadata", {}).get("count")
        assert isinstance(count, int) and count >= 1, f"bad count in {e}"


def test_expected_nodes_present(trace_events: list[dict[str, Any]]) -> None:
    ids = {e["node_id"] for e in trace_events}
    missing = _EXPECTED_EXECUTED - ids
    assert not missing, f"expected node IDs missing from trace: {missing}"


def test_cold_nodes_absent(trace_events: list[dict[str, Any]]) -> None:
    ids = {e["node_id"] for e in trace_events}
    leaked = _EXPECTED_COLD & ids
    assert not leaked, f"cold nodes unexpectedly present: {leaked}"


def test_no_external_stdlib_nodes(trace_events: list[dict[str, Any]]) -> None:
    for e in trace_events:
        nid = e["node_id"]
        assert not nid.startswith("fmt"), f"stdlib node leaked: {nid}"
        assert not nid.startswith("example.com/"), f"import-path leaked as node ID: {nid}"


def test_trace_streaming_raises(tmp_path: Path) -> None:
    from grackle.adapters.base import TraceOptions

    adapter = GoRuntimeAdapter()
    with pytest.raises(GoRuntimeError, match="--stream"):
        adapter.trace_streaming(_SCRIPT, _FIXTURE, TraceOptions(), lambda _: None)


def test_cli_trace_produces_jsonl(tmp_path: Path) -> None:
    """Smoke-test the full CLI path: grackle trace main.go -o out.jsonl."""
    from click.testing import CliRunner

    from grackle.cli import main

    out = tmp_path / "go.jsonl"
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["trace", str(_SCRIPT), "--root", str(_FIXTURE), "--language", "go", "-o", str(out)],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    lines = [json.loads(ln) for ln in out.read_text().splitlines() if ln.strip()]
    assert len(lines) > 0
    assert all(e["event"] == "call" for e in lines)
