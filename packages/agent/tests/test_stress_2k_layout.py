"""Stress test: parse the stress-2k fixture and assert node count + wall time."""

import time
from pathlib import Path

import pytest

FIXTURE_ROOT = Path(__file__).parent.parent.parent.parent / "fixtures" / "stress-2k" / "src"
MIN_NODES = 1_500
MAX_WALL_SECONDS = 20


@pytest.mark.skipif(
    not FIXTURE_ROOT.exists(),
    reason="fixtures/stress-2k/src not present — run fixtures/stress-2k/generate.py first",
)
def test_stress_2k_node_count_and_wall_time() -> None:
    from grackle.adapters.base import ParseOptions
    from grackle.python_parser.adapter import PythonStaticParser

    adapter = PythonStaticParser()
    t0 = time.monotonic()
    graph = adapter.parse(FIXTURE_ROOT, ParseOptions())
    elapsed = time.monotonic() - t0

    node_count = len(graph["nodes"])
    assert node_count >= MIN_NODES, (
        f"Expected ≥{MIN_NODES} nodes but got {node_count}. "
        "Re-run fixtures/stress-2k/generate.py if fixture is missing or stale."
    )
    assert elapsed <= MAX_WALL_SECONDS, (
        f"Parse took {elapsed:.2f}s — exceeds {MAX_WALL_SECONDS}s budget"
    )
