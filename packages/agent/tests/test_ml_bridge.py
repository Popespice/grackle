"""Tests for grackle.ml_bridge — the gate, training, and prediction surface
for Phase 12.2 (`grackle learn` + `predicted_heat`).

grackle-nn is an editable dev-only dependency (ADR-0028/ADR-0030), so these
tests exercise the bridge against a REAL implementation — no fixture/mock
model. The gate-closed path is exercised by monkeypatching the import
machinery, mirroring the go_runtime/rust_runtime/node_runtime capability
test pattern (an autouse fixture that resets the cache before and after).
"""

from __future__ import annotations

import builtins
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from grackle import ml_bridge
from grackle.adapters import registry
from grackle.adapters.base import ParseOptions
from grackle.python_runtime.aggregates import TraceAggregates

if TYPE_CHECKING:
    from collections.abc import Iterator

    from grackle.adapters.base import StaticGraph

_TINY_PYTHON_APP = Path(__file__).parent.parent.parent.parent / "fixtures" / "tiny-python-app"


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    ml_bridge.reset_cache()
    yield
    ml_bridge.reset_cache()


def _parsed_graph_and_heat() -> tuple[Any, dict[str, int]]:
    graph = registry.get_static("python").parse(_TINY_PYTHON_APP, ParseOptions())  # type: ignore[union-attr]
    agg = TraceAggregates.build(_TINY_PYTHON_APP / "trace.golden.jsonl")
    heat = agg.cumulative_heat_all(len(agg))
    heat.pop("<unresolved>", None)
    return graph, heat


# ---------------------------------------------------------------------------
# learn_available / remediation_message / reset_cache
# ---------------------------------------------------------------------------


def test_learn_available_true_in_dev_env() -> None:
    """grackle-nn is an editable dev dependency, so it's importable in this env."""
    assert ml_bridge.learn_available() is True


def test_learn_available_false_when_import_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def _fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "grackle_nn.ml" or name.startswith("grackle_nn.ml."):
            raise ImportError(f"simulated: {name} not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    ml_bridge.reset_cache()
    assert ml_bridge.learn_available() is False


def test_learn_available_false_on_non_import_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A broken editable install (e.g. a numpy ABI mismatch, or a mid-edit
    SyntaxError in the actively-developed sibling package) must degrade to
    "unavailable" exactly like "not installed" — never propagate. Every
    caller (the server's static-graph push, the CLI's gate check) depends on
    this function never raising."""
    real_import = builtins.__import__

    def _fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "grackle_nn.ml" or name.startswith("grackle_nn.ml."):
            raise ValueError("simulated: numpy.dtype size changed, ABI mismatch")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    ml_bridge.reset_cache()
    assert ml_bridge.learn_available() is False  # must not raise ValueError


def test_remediation_message_names_the_package() -> None:
    msg = ml_bridge.remediation_message()
    assert "grackle-nn" in msg
    assert "packages/nn" in msg


def test_learn_available_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """The result is cached for the process lifetime until reset_cache()."""
    assert ml_bridge.learn_available() is True

    real_import = builtins.__import__

    def _fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "grackle_nn.ml":
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    # No reset_cache() call — the cached True from before monkeypatching sticks.
    assert ml_bridge.learn_available() is True


# ---------------------------------------------------------------------------
# train_and_save
# ---------------------------------------------------------------------------


def test_train_and_save_produces_loadable_model(tmp_path: Path) -> None:
    graph, heat = _parsed_graph_and_heat()
    out = tmp_path / "heat-model.npz"

    summary = ml_bridge.train_and_save(graph, heat, epochs=5, seed=0, out=out)

    assert out.exists()
    assert summary.examples == 1
    assert summary.nodes == len(graph["nodes"])
    assert summary.epochs == 5
    assert summary.seed == 0
    assert summary.model_path == str(out)
    assert -1.0 <= summary.train_spearman <= 1.0
    assert -1.0 <= summary.baseline_spearman <= 1.0


def test_train_and_save_writes_report_card(tmp_path: Path) -> None:
    import json

    graph, heat = _parsed_graph_and_heat()
    out = tmp_path / "heat-model.npz"

    ml_bridge.train_and_save(graph, heat, epochs=3, seed=0, out=out)

    history_path = out.parent / "learn-history.jsonl"
    assert history_path.exists()
    lines = history_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record.keys() == {
        "ts",
        "feature_version",
        "examples",
        "nodes",
        "epochs",
        "seed",
        "final_loss",
        "train_spearman",
        "baseline_spearman",
        "val_spearman",
    }
    assert record["val_spearman"] is None
    assert record["examples"] == 1


def test_learn_history_is_lf_terminated_bytes(tmp_path: Path) -> None:
    """CLAUDE.md: "All JSONL emitters write binary LF". Asserted on RAW BYTES
    because every text-level idiom hides the bug — ``splitlines()`` treats
    ``\\r\\n`` as one boundary and ``strip()`` eats a trailing ``\\r``, so a
    text-mode emitter (which emits CRLF on Windows) passes them all."""
    graph, heat = _parsed_graph_and_heat()
    out = tmp_path / "heat-model.npz"

    ml_bridge.train_and_save(graph, heat, epochs=2, seed=0, out=out)
    ml_bridge.train_and_save(graph, heat, epochs=2, seed=1, out=out)

    raw = (out.parent / "learn-history.jsonl").read_bytes()
    assert b"\r" not in raw, "learn-history.jsonl must be LF-only on every platform"
    assert raw.endswith(b"}\n")
    assert raw.count(b"\n") == 2


def test_learn_history_write_failure_is_logged_not_silent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Regression: the swallow was fully silent, so a dropped report-card row
    was invisible — train_and_save still returned a successful summary and
    the CLI reported success. Non-fatal is the intent; silent is not."""
    summary = ml_bridge.LearnSummary(
        examples=1,
        nodes=3,
        epochs=1,
        seed=0,
        final_loss=0.1,
        train_spearman=0.2,
        baseline_spearman=0.3,
        model_path="m.npz",
    )
    missing_dir = tmp_path / "nope" / "deeper"
    ml_bridge._append_learn_history(missing_dir / "heat-model.npz", summary)

    assert not (missing_dir / "learn-history.jsonl").exists()
    assert "learn-history append failed" in capsys.readouterr().out


def test_learn_history_unserializable_record_is_logged_not_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The `allow_nan=False` guard raises ValueError — exactly what the same
    commit's `except` was swallowing. Simulates a future field added without
    going through `_finite_or_none`."""
    monkeypatch.setattr(ml_bridge, "_finite_or_none", lambda v: v)
    summary = ml_bridge.LearnSummary(
        examples=1,
        nodes=3,
        epochs=1,
        seed=0,
        final_loss=float("nan"),
        train_spearman=0.2,
        baseline_spearman=0.3,
        model_path="m.npz",
    )
    ml_bridge._append_learn_history(tmp_path / "heat-model.npz", summary)

    assert not (tmp_path / "learn-history.jsonl").exists()
    assert "not serializable" in capsys.readouterr().out


def test_learn_history_is_valid_strict_json_for_non_finite_metrics(tmp_path: Path) -> None:
    """A non-finite metric must serialize as ``null``, not a bare ``NaN``
    token. ``NaN`` is accepted by Python's reader but rejected by every
    strict RFC-8259 parser (e.g. JavaScript's ``JSON.parse``), and one such
    line would permanently break the intended downstream consumer of this
    append-only file."""
    import json as _json

    out = tmp_path / "heat-model.npz"

    # Force a non-finite metric through the record builder.
    summary = ml_bridge.LearnSummary(
        examples=1,
        nodes=3,
        epochs=1,
        seed=0,
        final_loss=float("nan"),
        train_spearman=float("inf"),
        baseline_spearman=0.5,
        model_path=str(out),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    ml_bridge._append_learn_history(out, summary)

    raw = (out.parent / "learn-history.jsonl").read_text(encoding="utf-8").strip()
    assert "NaN" not in raw and "Infinity" not in raw
    # strict=... is not enough; parse_constant fires on the bare tokens.
    record = _json.loads(raw, parse_constant=lambda c: pytest.fail(f"non-JSON constant: {c}"))
    assert record["final_loss"] is None
    assert record["train_spearman"] is None
    assert record["baseline_spearman"] == 0.5


def test_train_and_save_appends_across_runs(tmp_path: Path) -> None:
    import json

    graph, heat = _parsed_graph_and_heat()
    out = tmp_path / "heat-model.npz"

    ml_bridge.train_and_save(graph, heat, epochs=2, seed=0, out=out)
    ml_bridge.train_and_save(graph, heat, epochs=2, seed=1, out=out)

    history_path = out.parent / "learn-history.jsonl"
    lines = history_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["seed"] == 0
    assert json.loads(lines[1])["seed"] == 1


def test_train_and_save_creates_parent_dirs(tmp_path: Path) -> None:
    graph, heat = _parsed_graph_and_heat()
    out = tmp_path / "nested" / "dir" / "heat-model.npz"

    ml_bridge.train_and_save(graph, heat, epochs=2, seed=0, out=out)

    assert out.exists()


def test_train_and_save_rejects_empty_graph(tmp_path: Path) -> None:
    empty_graph: StaticGraph = {"version": 1, "language": "python", "nodes": [], "edges": []}
    with pytest.raises(ml_bridge.MLBridgeError, match="no nodes"):
        ml_bridge.train_and_save(empty_graph, {}, epochs=2, seed=0, out=tmp_path / "m.npz")


# ---------------------------------------------------------------------------
# predict_scores
# ---------------------------------------------------------------------------


def test_predict_scores_full_coverage_and_range(tmp_path: Path) -> None:
    graph, heat = _parsed_graph_and_heat()
    out = tmp_path / "heat-model.npz"
    ml_bridge.train_and_save(graph, heat, epochs=5, seed=0, out=out)

    result = ml_bridge.predict_scores(graph, out)

    assert result["model_version"] == 1
    scores = result["scores"]
    assert len(scores) == len(graph["nodes"])
    node_ids = {n["id"] for n in graph["nodes"]}
    assert {s["node_id"] for s in scores} == node_ids
    for s in scores:
        assert 0.0 <= s["score"] <= 1.0
        # <= 4 decimal places (D12.9)
        assert s["score"] == round(s["score"], 4)


def test_predict_scores_missing_model_raises_bridge_error(tmp_path: Path) -> None:
    graph, _ = _parsed_graph_and_heat()
    with pytest.raises(ml_bridge.MLBridgeError):
        ml_bridge.predict_scores(graph, tmp_path / "does-not-exist.npz")


def test_predict_scores_truncated_file_raises_bridge_error(tmp_path: Path) -> None:
    graph, _ = _parsed_graph_and_heat()
    bad = tmp_path / "bad.npz"
    bad.write_bytes(b"not a real npz file at all")
    with pytest.raises(ml_bridge.MLBridgeError):
        ml_bridge.predict_scores(graph, bad)


def test_predict_scores_doctored_keys_raises_bridge_error(tmp_path: Path) -> None:
    """A checkpoint missing a required key must surface as MLBridgeError, not
    a leaked ModelFormatError."""
    graph, heat = _parsed_graph_and_heat()
    good = tmp_path / "good.npz"
    ml_bridge.train_and_save(graph, heat, epochs=2, seed=0, out=good)

    # Doctor the npz: drop the "w2" key by rewriting the zip without it.
    doctored = tmp_path / "doctored.npz"
    with zipfile.ZipFile(good) as zin, zipfile.ZipFile(doctored, "w") as zout:
        for item in zin.infolist():
            if item.filename == "w2.npy":
                continue
            zout.writestr(item, zin.read(item.filename))

    with pytest.raises(ml_bridge.MLBridgeError):
        ml_bridge.predict_scores(graph, doctored)


def test_predict_scores_version_mismatch_raises_bridge_error(tmp_path: Path) -> None:
    """A checkpoint saved under a different feature_version must surface as
    MLBridgeError, not a leaked ModelFormatError — the third corruption case
    (missing key, truncated file, version/shape mismatch)."""
    graph, heat = _parsed_graph_and_heat()
    good = tmp_path / "good.npz"
    ml_bridge.train_and_save(graph, heat, epochs=2, seed=0, out=good)

    # Doctor the npz: rewrite feature_version to a value the shipped code
    # will refuse to load (HeatModel.load's version check).
    doctored = tmp_path / "doctored.npz"
    with zipfile.ZipFile(good) as zin, zipfile.ZipFile(doctored, "w") as zout:
        for item in zin.infolist():
            if item.filename == "feature_version.npy":
                bogus = np.array(999, dtype=np.int64)
                buf = _npy_bytes(bogus)
                zout.writestr(item, buf)
            else:
                zout.writestr(item, zin.read(item.filename))

    with pytest.raises(ml_bridge.MLBridgeError, match="prediction failed"):
        ml_bridge.predict_scores(graph, doctored)


def _npy_bytes(arr: np.ndarray[Any, Any]) -> bytes:
    import io

    buf = io.BytesIO()
    np.save(buf, arr)
    return buf.getvalue()
