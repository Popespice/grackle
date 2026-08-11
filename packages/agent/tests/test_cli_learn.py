"""Tests for the ``grackle learn`` CLI subcommand (Phase 12.2)."""

from __future__ import annotations

import json
import os
import shutil
import sys
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


def test_learn_broken_grackle_nn_install_clean_error_not_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-ImportError failure importing grackle_nn.ml (e.g. a numpy ABI
    mismatch) must close the gate cleanly, same as an absent install — never
    a raw traceback. Exercises the real ml_bridge.learn_available() (not a
    monkeypatched replacement) so this proves the actual fix, not just the
    test's own assumption about it."""
    import builtins

    root = _copy_golden(tmp_path)
    trace = root / "trace.golden.jsonl"

    real_import = builtins.__import__

    def _fake_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "grackle_nn.ml" or name.startswith("grackle_nn.ml."):
            raise ValueError("simulated: numpy.dtype size changed, ABI mismatch")
        return real_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", _fake_import)

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


@pytest.mark.skipif(sys.platform == "win32", reason="FIFOs are POSIX-only")
def test_learn_from_store_skips_non_regular_file(tmp_path: Path) -> None:
    """A session library is user data — a source_path pointing at a FIFO (or
    any non-regular file) must be skipped, not opened. TraceAggregates.build
    would otherwise line-iterate it and could hang indefinitely on a FIFO
    with no writer."""
    root = _copy_golden(tmp_path)
    trace = root / "trace.golden.jsonl"
    db_path = tmp_path / "sessions.db"
    fifo_path = tmp_path / "suspicious.jsonl"
    if sys.platform != "win32":
        # os.mkfifo is POSIX-only — absent from typeshed's Windows stubs, so
        # this guard also keeps `mypy --strict` green on the Windows CI leg
        # (which never reaches this branch, so never resolves the attribute),
        # mirroring cache.py's sys.platform-gated msvcrt/fcntl split.
        os.mkfifo(fifo_path)

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
            id="fifo",
            label="suspicious",
            started_ns=0,
            ended_ns=1,
            source_path=str(fifo_path),
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
    # A FIFO exists — reporting it as "missing" would send the user to debug
    # the wrong thing, so the two skip reasons must read differently.
    assert "not a regular file" in result.output
    assert "skipping missing recording" not in result.output
    assert "suspicious.jsonl" in result.output
    assert "from 1 trace(s)" in result.output


def test_learn_no_parser_detected_is_usage_error(tmp_path: Path) -> None:
    """learn's --root parsing shares _detect_and_parse with `parse` — this
    proves learn's own call site is wired correctly, not just the helper."""
    empty_root = tmp_path / "empty"
    empty_root.mkdir()
    trace = tmp_path / "trace.jsonl"
    trace.write_text('{"node_id": "x", "type": "call"}\n', encoding="utf-8")

    result = CliRunner().invoke(main, ["learn", str(trace), "--root", str(empty_root)])
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "no static parser detected" in result.output


def test_learn_empty_trace_is_click_exception(tmp_path: Path) -> None:
    """A trace with zero events (heat={}) — a distinct degenerate case from
    a trace with real events whose node_ids don't overlap (see the test
    below); both must raise the same error, but they exercise different
    code paths (the per-trace "has no overlap" warning at cli.py's `if heat
    and not any(...)` is only reachable by the latter)."""
    root = _copy_golden(tmp_path)
    empty_trace = tmp_path / "empty.jsonl"
    empty_trace.write_text("", encoding="utf-8")

    result = CliRunner().invoke(
        main, ["learn", str(empty_trace), "--root", str(root), "--epochs", "2"]
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "nothing to learn from" in result.output


def test_learn_nonoverlapping_trace_is_click_exception(tmp_path: Path) -> None:
    """A trace with real events whose node_ids don't match --root's parsed
    graph at all (as opposed to an empty trace) — this is the scenario the
    error message and the per-trace warning are actually written to
    describe: 'was it captured against a different root?'. A mutation from
    the correct `not any(nid in node_ids for nid in merged_heat)` check to
    the naive `not merged_heat` would pass the empty-trace test above but
    fail (wrongly accept) this one."""
    root = _copy_golden(tmp_path)
    foreign_trace = tmp_path / "foreign.jsonl"
    foreign_trace.write_text(
        '{"node_id": "unrelated_project/other.py:some_function"}\n'
        '{"node_id": "unrelated_project/other.py:some_function"}\n',
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        main, ["learn", str(foreign_trace), "--root", str(root), "--epochs", "2"]
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "has no overlap with the parsed graph" in result.output
    assert "nothing to learn from" in result.output


def test_learn_two_traces_of_same_root_merge_into_one_example(tmp_path: Path) -> None:
    """Two DISTINCT traces against one --root merge into a single Example
    (one root means one graph, so there is nothing to hold out)."""
    root = _copy_golden(tmp_path)
    trace_a = root / "trace.golden.jsonl"
    trace_b = tmp_path / "trace-b.jsonl"
    trace_b.write_bytes(trace_a.read_bytes())

    result = CliRunner().invoke(
        main,
        ["learn", str(trace_a), str(trace_b), "--root", str(root), "--epochs", "2"],
    )
    assert result.exit_code == 0, result.output
    assert "from 2 trace(s)" in result.output

    history_path = root / ".grackle" / "learn-history.jsonl"
    record = json.loads(history_path.read_text(encoding="utf-8").strip())
    assert record["examples"] == 1


def test_learn_deduplicates_the_same_trace_given_twice(tmp_path: Path) -> None:
    """Regression: the same trace reaching the merge twice would have its
    counts summed twice, over-weighting it against every other trace."""
    root = _copy_golden(tmp_path)
    trace = root / "trace.golden.jsonl"

    result = CliRunner().invoke(
        main,
        ["learn", str(trace), str(trace), "--root", str(root), "--epochs", "2"],
    )
    assert result.exit_code == 0, result.output
    assert "from 1 trace(s)" in result.output


def test_learn_deduplicates_positional_trace_also_in_store(tmp_path: Path) -> None:
    """Regression: the documented workflow encourages --from-store, so a user
    combining it with an ad-hoc file that the store also knows about is a
    realistic path to double-counted heat."""
    root = _copy_golden(tmp_path)
    trace = root / "trace.golden.jsonl"
    db_path = tmp_path / "sessions.db"

    store = SessionStore.open(db_path)
    store.save_session(
        SessionMeta(
            id="same",
            label="same trace as the positional arg",
            started_ns=0,
            ended_ns=1,
            source_path=str(trace),
            event_count=1,
            language="python",
        )
    )
    store.close()

    result = CliRunner().invoke(
        main,
        [
            "learn",
            str(trace),
            "--root",
            str(root),
            "--from-store",
            str(db_path),
            "--epochs",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "from 1 trace(s)" in result.output


def test_learn_exclude_warns_about_train_serve_skew(tmp_path: Path) -> None:
    """`grackle serve` has no --exclude, so a model trained on an excluded
    subset is served against the full graph — structural features shift.
    That must be surfaced, not silent."""
    root = _copy_golden(tmp_path)
    trace = root / "trace.golden.jsonl"

    result = CliRunner().invoke(
        main,
        ["learn", str(trace), "--root", str(root), "--epochs", "2", "--exclude", "nothing_*.py"],
    )
    assert result.exit_code == 0, result.output
    assert "--exclude" in result.output
    assert "will not match at inference" in result.output


def test_learn_without_exclude_does_not_warn(tmp_path: Path) -> None:
    """Non-vacuity guard for the test above: the warning must be conditional."""
    root = _copy_golden(tmp_path)
    trace = root / "trace.golden.jsonl"

    result = CliRunner().invoke(main, ["learn", str(trace), "--root", str(root), "--epochs", "2"])
    assert result.exit_code == 0, result.output
    assert "will not match at inference" not in result.output


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
