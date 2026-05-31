"""End-to-end Node/V8 runtime tests (ADR-0022) — gated on a real Node toolchain.

These spawn Node, drive it over CDP, and assert the produced ``TraceEvent`` stream.
They are skipped when Node >= 22.6 is unavailable; CI installs Node 22 for the
frontend job, so they run there (Ubuntu + Windows), not only locally.

Assertions are robust to sampling non-determinism: structure (which nodes appear,
call/return balance, depth, no leaked non-project frames) rather than exact counts.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from grackle.adapters.base import TraceOptions
from grackle.cli import main
from grackle.node_runtime import capability
from grackle.node_runtime.adapter import NodeRuntimeAdapter

if TYPE_CHECKING:
    from grackle.adapters.base import TraceEvent

pytestmark = pytest.mark.skipif(
    not capability.node_runtime_available(),
    reason="Node >= 22.6 (for --experimental-strip-types) not available",
)

_FIXTURE = Path(__file__).parents[4] / "fixtures" / "tiny-node-app"
_SCRIPT = _FIXTURE / "src" / "main.ts"

# Function/file nodes the static graph indexes for the fixture.
_KNOWN_NODES = {
    "src/main.ts",
    "src/main.ts:run",
    "src/math.ts",
    "src/math.ts:add",
    "src/math.ts:fib",
    "src/math.ts:busy",
}


def test_fixture_present() -> None:
    assert _SCRIPT.exists(), "tiny-node-app fixture missing"


def test_sampling_reconstructs_call_return_stream() -> None:
    events = list(NodeRuntimeAdapter().trace(_SCRIPT, _FIXTURE, TraceOptions()))

    assert events, "expected sampled events"
    assert all(e["event"] in ("call", "return", "exception") for e in events)
    assert all(e["thread_id"] == 0 for e in events)

    # Every resolved node is a known project node (or the unresolved sentinel) —
    # no node: internals or pseudo-frames leak through.
    node_ids = {e["node_id"] for e in events}
    assert node_ids <= (_KNOWN_NODES | {"<unresolved>"}), node_ids
    # The hot recursive function is reliably sampled.
    assert "src/math.ts:fib" in node_ids

    # call/return balance (all open frames are closed at profile end).
    calls = sum(1 for e in events if e["event"] == "call")
    returns = sum(1 for e in events if e["event"] == "return")
    assert calls == returns

    # Recursion produces real nesting, and timestamps are non-decreasing.
    assert max(e["frame_depth"] for e in events) > 1
    timestamps = [e["ts_ns"] for e in events]
    assert timestamps == sorted(timestamps)


def test_coverage_emits_live_heat() -> None:
    events: list[TraceEvent] = []
    NodeRuntimeAdapter().trace_streaming(_SCRIPT, _FIXTURE, TraceOptions(), events.append)

    assert events, "expected live coverage events"
    assert all(e["event"] == "call" for e in events)
    assert all(e["frame_depth"] == 0 for e in events)
    assert all(
        isinstance(e["metadata"], dict) and e["metadata"].get("live") is True for e in events
    )

    node_ids = {e["node_id"] for e in events}
    assert node_ids <= _KNOWN_NODES, node_ids
    # Both functions are definitely called → exact coverage records them.
    assert {"src/math.ts:fib", "src/math.ts:add"} <= node_ids

    # The exact call count rides along in metadata for fidelity-aware consumers.
    add_event = next(e for e in events if e["node_id"] == "src/math.ts:add")
    metadata = add_event["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["count"] == 2_000_000


def test_cli_trace_typescript_stdout() -> None:
    import json

    result = CliRunner().invoke(main, ["trace", str(_SCRIPT), "--root", str(_FIXTURE)])
    assert result.exit_code == 0, result.output
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert lines
    first = json.loads(lines[0])
    assert "event" in first
    assert "node_id" in first


def test_cli_trace_typescript_output_file(tmp_path: Path) -> None:
    out = tmp_path / "node.jsonl"
    result = CliRunner().invoke(
        main, ["trace", str(_SCRIPT), "--root", str(_FIXTURE), "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    assert out.read_text(encoding="utf-8").strip(), "expected JSONL events in output file"
