"""Tests for the ``grackle learn`` CLI subcommand (Phase 12.2)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner

from grackle import ml_bridge
from grackle.cli import main
from grackle.session_store import SessionMeta, SessionStore

if TYPE_CHECKING:
    from collections.abc import Iterator

_GOLDEN_ROOT = Path(__file__).parent.parent.parent.parent / "fixtures" / "tiny-python-app"


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    ml_bridge.reset_cache()
    yield
    ml_bridge.reset_cache()


def _copy_golden(tmp_path: Path) -> Path:
    dest = tmp_path / "proj"
    shutil.copytree(_GOLDEN_ROOT, dest)
    return dest


def test_learn_happy_path_default_output(tmp_path: Path) -> None:
    root = _copy_golden(tmp_path)
    trace = root / "trace.golden.jsonl"

    result = CliRunner().invoke(main, ["learn", str(trace), "--root", str(root), "--epochs", "2"])
    assert result.exit_code == 0, result.output
    assert "wrote model →" in result.output

    model_path = root / ".grackle" / "heat-model.npz"
    assert model_path.exists()

    from grackle_nn.ml import HeatModel

    loaded = HeatModel.load(model_path)  # loadable — raises if malformed
    assert loaded is not None

    history_path = root / ".grackle" / "learn-history.jsonl"
    assert history_path.exists()
    record = json.loads(history_path.read_text(encoding="utf-8").strip())
    assert record["val_spearman"] is None
    assert record["examples"] == 1


def test_learn_explicit_output(tmp_path: Path) -> None:
    root = _copy_golden(tmp_path)
    trace = root / "trace.golden.jsonl"
    out = tmp_path / "custom" / "model.npz"

    result = CliRunner().invoke(
        main,
        ["learn", str(trace), "--root", str(root), "--epochs", "2", "--output", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    assert not (root / ".grackle" / "heat-model.npz").exists()


def test_learn_gate_closed_clean_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _copy_golden(tmp_path)
    trace = root / "trace.golden.jsonl"

    monkeypatch.setattr(ml_bridge, "learn_available", lambda: False)

    result = CliRunner().invoke(main, ["learn", str(trace), "--root", str(root)])
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "grackle-nn" in result.output


def test_learn_zero_traces_is_usage_error(tmp_path: Path) -> None:
    root = _copy_golden(tmp_path)
    result = CliRunner().invoke(main, ["learn", "--root", str(root)])
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "no traces given" in result.output


def test_learn_from_store_nonexistent_path_no_db_created(tmp_path: Path) -> None:
    root = _copy_golden(tmp_path)
    trace = root / "trace.golden.jsonl"
    missing_db = tmp_path / "nonexistent.db"

    result = CliRunner().invoke(
        main,
        ["learn", str(trace), "--root", str(root), "--from-store", str(missing_db)],
    )
    assert result.exit_code != 0
    assert not missing_db.exists()


def test_learn_from_store_with_missing_recording_warns_and_skips(tmp_path: Path) -> None:
    root = _copy_golden(tmp_path)
    trace = root / "trace.golden.jsonl"
    db_path = tmp_path / "sessions.db"

    store = SessionStore.open(db_path)
    store.save_session(
        SessionMeta(
            id="present",
            label="ok",
            started_ns=0,
            ended_ns=1,
            source_path=str(trace),
            event_count=1,
            language="python",
        )
    )
    store.save_session(
        SessionMeta(
            id="missing",
            label="gone",
            started_ns=0,
            ended_ns=1,
            source_path=str(tmp_path / "does-not-exist.jsonl"),
            event_count=1,
            language="python",
        )
    )
    store.close()

    result = CliRunner().invoke(
        main,
        ["learn", "--root", str(root), "--from-store", str(db_path), "--epochs", "2"],
    )
    assert result.exit_code == 0, result.output
    assert "skipping missing recording" in result.output
    assert "does-not-exist.jsonl" in result.output
    assert "trained on" in result.output
    assert "from 1 trace(s)" in result.output


def test_learn_zero_overlap_is_click_exception(tmp_path: Path) -> None:
    root = _copy_golden(tmp_path)
    empty_trace = tmp_path / "empty.jsonl"
    empty_trace.write_text("", encoding="utf-8")

    result = CliRunner().invoke(
        main, ["learn", str(empty_trace), "--root", str(root), "--epochs", "2"]
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "nothing to learn from" in result.output


def test_learn_two_traces_of_same_root_merge_into_one_example(tmp_path: Path) -> None:
    root = _copy_golden(tmp_path)
    trace = root / "trace.golden.jsonl"

    result = CliRunner().invoke(
        main,
        ["learn", str(trace), str(trace), "--root", str(root), "--epochs", "2"],
    )
    assert result.exit_code == 0, result.output
    assert "from 2 trace(s)" in result.output

    history_path = root / ".grackle" / "learn-history.jsonl"
    record = json.loads(history_path.read_text(encoding="utf-8").strip())
    assert record["examples"] == 1


def test_learn_help_documents_shared_root_and_train_only_metrics() -> None:
    result = CliRunner().invoke(main, ["learn", "--help"])
    assert result.exit_code == 0
    assert "SAME --root" in result.output
    assert "train-set metrics" in result.output


def test_learn_training_failure_is_click_exception_not_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A domain-level ml_bridge failure (empty graph, etc.) must surface as a
    clean ClickException, never a raw traceback in the CLI."""
    root = _copy_golden(tmp_path)
    trace = root / "trace.golden.jsonl"

    def _boom(*args: Any, **kwargs: Any) -> Any:
        raise ml_bridge.MLBridgeError("simulated training failure")

    monkeypatch.setattr(ml_bridge, "train_and_save", _boom)

    result = CliRunner().invoke(main, ["learn", str(trace), "--root", str(root)])
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "simulated training failure" in result.output
