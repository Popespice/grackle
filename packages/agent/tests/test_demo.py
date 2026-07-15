"""Unit tests for the demo-branch fixture wiring in ``grackle.demo``.

Covers the Phase 11 trace-override decoupling (a fixture's golden trace can
live somewhere other than co-located with its source root — see
``_resolve_trace``) and the cosmetic label override, both of which had no
prior coverage.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from grackle.demo import _label_for, _resolve_trace, _seed_session_store

if TYPE_CHECKING:
    from pathlib import Path


def _make_event(node_id: str, i: int) -> dict[str, Any]:
    return {
        "event": "call",
        "node_id": node_id,
        "ts_ns": i * 1_000_000,
        "thread_id": 1,
        "frame_depth": 0,
        "metadata": {},
    }


def _write_trace(path: Path, n: int = 3) -> None:
    events = [_make_event("a.py:fn", i) for i in range(n)]
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")


def test_label_for_default_capitalizes() -> None:
    assert _label_for("python") == "Python"


def test_label_for_uses_override() -> None:
    assert _label_for("nn") == "NN"


def test_resolve_trace_override_present_and_exists(tmp_path: Path) -> None:
    override = tmp_path / "elsewhere.jsonl"
    _write_trace(override)
    root = tmp_path / "src"
    root.mkdir()
    assert _resolve_trace("nn", root, {"nn": override}) == override


def test_resolve_trace_override_present_but_missing_does_not_fall_back(
    tmp_path: Path,
) -> None:
    """A registered-but-missing override must not silently fall back to a co-located trace."""
    root = tmp_path / "src"
    root.mkdir()
    co_located = root / "trace.golden.jsonl"
    _write_trace(co_located)
    missing_override = tmp_path / "does-not-exist.jsonl"
    assert _resolve_trace("nn", root, {"nn": missing_override}) is None


def test_resolve_trace_no_override_uses_co_located(tmp_path: Path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    co_located = root / "trace.golden.jsonl"
    _write_trace(co_located)
    assert _resolve_trace("python", root, {}) == co_located


def test_resolve_trace_no_override_no_co_located_returns_none(tmp_path: Path) -> None:
    root = tmp_path / "src"
    root.mkdir()
    assert _resolve_trace("python", root, {}) is None


def test_seed_session_store_uses_override_trace(tmp_path: Path) -> None:
    """A fixture whose trace lives outside its source root (the nn case) still seeds."""
    root = tmp_path / "nn_src"
    root.mkdir()
    override = tmp_path / "nn-training" / "trace.golden.jsonl"
    override.parent.mkdir()
    _write_trace(override, n=5)

    store = _seed_session_store({"nn": root}, {"nn": override})
    try:
        sessions = store.list_sessions()
        assert len(sessions) == 1
        assert sessions[0].label == "NN"
        assert sessions[0].event_count == 5
        assert sessions[0].started_ns == 0
        assert sessions[0].ended_ns == 4_000_000
        assert sessions[0].source_path == str(override.resolve())
    finally:
        store.close()


def test_seed_session_store_skips_fixture_without_any_trace(tmp_path: Path) -> None:
    root = tmp_path / "poly"
    root.mkdir()
    store = _seed_session_store({"poly": root}, {})
    try:
        assert store.list_sessions() == []
    finally:
        store.close()
