"""Tests for grackle_nn.ml.heat_model (Phase 12.1, block M5)."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from grackle_nn.ml.dataset import build_example
from grackle_nn.ml.heat_model import HeatModel, ModelFormatError, train_heat_model

if TYPE_CHECKING:
    from pathlib import Path


def _make_graph(n: int, seed: int) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    nodes = [
        {"id": f"n{i}", "kind": "function", "name": f"n{i}", "path": "a.py", "line": i}
        for i in range(n)
    ]
    edges = []
    for i in range(1, n):
        src = int(rng.integers(0, i))
        edges.append({"source": f"n{src}", "target": f"n{i}", "kind": "call"})
    return {"version": 1, "language": "python", "nodes": nodes, "edges": edges}


def _examples(count: int = 5, n: int = 20) -> list[Any]:
    out = []
    for s in range(count):
        graph = _make_graph(n, s)
        rng = np.random.default_rng(1000 + s)
        heat = {node["id"]: int(rng.integers(0, 10)) for node in graph["nodes"]}
        out.append(build_example(f"g{s}", graph, heat))
    return out


def test_seeded_loss_decreases() -> None:
    examples = _examples()
    _, history = train_heat_model(examples, epochs=20, seed=0)
    losses = [h[1] for h in history]
    assert losses[-1] < losses[0]


def test_same_seed_bit_identical_weights() -> None:
    examples = _examples()
    model1, _ = train_heat_model(examples, epochs=5, seed=0)
    model2, _ = train_heat_model(examples, epochs=5, seed=0)
    for p1, p2 in zip(model1.model.parameters(), model2.model.parameters(), strict=True):
        assert np.array_equal(p1, p2)


def test_history_entries_are_builtin_typed() -> None:
    examples = _examples()
    _, history = train_heat_model(examples, epochs=3, seed=0)
    assert len(history) == 3
    for epoch, train_loss, val_loss in history:
        assert type(epoch) is int
        assert type(train_loss) is float
        assert type(val_loss) is float
        assert math.isfinite(train_loss)
        assert math.isfinite(val_loss)
    assert [h[0] for h in history] == [0, 1, 2]


def test_val_none_falls_back_to_train_loss_exactly() -> None:
    examples = _examples()
    _, history = train_heat_model(examples, epochs=3, seed=0, val=None)
    assert all(h[1] == h[2] for h in history)


def test_val_provided_computes_loss_from_val_not_train_data() -> None:
    examples = _examples(count=6)
    train, val = examples[:4], examples[4:]
    _, history_with_val = train_heat_model(train, epochs=3, seed=0, val=val)
    _, history_without_val = train_heat_model(train, epochs=3, seed=0, val=None)
    # Same train data + same seed -> an identical train_loss trajectory
    # either way (verified: train losses match exactly between the two
    # calls). If val_loss were silently computed from train data too (the
    # bug this discriminates), history_with_val's val_losses would exactly
    # equal history_without_val's train_losses instead of genuinely
    # reflecting the disjoint val set.
    val_losses = [h[2] for h in history_with_val]
    train_losses_alone = [h[1] for h in history_without_val]
    assert [h[1] for h in history_with_val] == train_losses_alone  # sanity: same trajectory
    assert val_losses != train_losses_alone
    assert all(math.isfinite(loss) for loss in val_losses)


def test_predict_clip_bounds() -> None:
    examples = _examples()
    model, _ = train_heat_model(examples, epochs=5, seed=0)
    pred = model.predict(examples[0].x)
    assert pred.shape == (examples[0].x.shape[0],)
    assert pred.min() >= 0.0
    assert pred.max() <= 1.0


def test_save_load_predict_bit_identical(tmp_path: Path) -> None:
    examples = _examples()
    model, _ = train_heat_model(examples, epochs=5, seed=0)
    path = tmp_path / "heat-model.npz"
    model.save(path)
    loaded = HeatModel.load(path)
    pred_before = model.predict(examples[0].x)
    pred_after = loaded.predict(examples[0].x)
    assert np.array_equal(pred_before, pred_after)
    for p1, p2 in zip(model.model.parameters(), loaded.model.parameters(), strict=True):
        assert np.array_equal(p1, p2)
    assert np.array_equal(model.norm_mean, loaded.norm_mean)
    assert np.array_equal(model.norm_std, loaded.norm_std)


def test_save_overwrite_clean_no_stray_tmp(tmp_path: Path) -> None:
    examples = _examples()
    model, _ = train_heat_model(examples, epochs=2, seed=0)
    path = tmp_path / "heat-model.npz"
    model.save(path)
    model.save(path)  # overwrite
    assert path.exists()
    assert not (tmp_path / "heat-model.npz.tmp").exists()
    assert list(tmp_path.iterdir()) == [path]


def test_load_doctored_feature_version_raises_with_both_versions(tmp_path: Path) -> None:
    examples = _examples()
    model, _ = train_heat_model(examples, epochs=2, seed=0)
    path = tmp_path / "heat-model.npz"
    model.save(path)

    with np.load(path) as npz:
        data = dict(npz)
    data["feature_version"] = np.array(999)
    np.savez(path, **data)

    with pytest.raises(ModelFormatError) as excinfo:
        HeatModel.load(path)
    assert excinfo.value.found_version == 999
    assert excinfo.value.expected_version == 1


def test_load_missing_key_raises(tmp_path: Path) -> None:
    examples = _examples()
    model, _ = train_heat_model(examples, epochs=2, seed=0)
    path = tmp_path / "heat-model.npz"
    model.save(path)

    with np.load(path) as npz:
        data = dict(npz)
    del data["w1"]
    np.savez(path, **data)

    with pytest.raises(ModelFormatError, match="w1") as excinfo:
        HeatModel.load(path)
    # feature_version itself is present and valid in this checkpoint, so the
    # missing-key error must report the real found_version, not the -1
    # "unknown" sentinel reserved for when feature_version is also absent.
    assert excinfo.value.found_version == 1


def test_load_wrong_shape_raises(tmp_path: Path) -> None:
    examples = _examples()
    model, _ = train_heat_model(examples, epochs=2, seed=0)
    path = tmp_path / "heat-model.npz"
    model.save(path)

    with np.load(path) as npz:
        data = dict(npz)
    data["w0"] = np.zeros((3, 3))
    np.savez(path, **data)

    with pytest.raises(ModelFormatError, match="w0"):
        HeatModel.load(path)


def test_load_doctored_hidden_raises(tmp_path: Path) -> None:
    examples = _examples()
    model, _ = train_heat_model(examples, epochs=2, seed=0)
    path = tmp_path / "heat-model.npz"
    model.save(path)

    with np.load(path) as npz:
        data = dict(npz)
    data["hidden"] = np.array([1, 2])
    np.savez(path, **data)

    with pytest.raises(ModelFormatError, match="hidden"):
        HeatModel.load(path)


def test_load_wrong_norm_mean_shape_raises(tmp_path: Path) -> None:
    examples = _examples()
    model, _ = train_heat_model(examples, epochs=2, seed=0)
    path = tmp_path / "heat-model.npz"
    model.save(path)

    with np.load(path) as npz:
        data = dict(npz)
    data["norm_mean"] = np.zeros(3)
    np.savez(path, **data)

    with pytest.raises(ModelFormatError, match="norm_mean"):
        HeatModel.load(path)


def test_predict_uses_stored_norm_stats_not_recomputed_from_input() -> None:
    examples = _examples()
    model, _ = train_heat_model(examples, epochs=5, seed=0)
    # A single-row input: if predict() ever recomputed mean/std from just
    # this one row instead of using the stored train-time stats, std would
    # collapse toward the input itself and standardization would diverge
    # from this manually-computed-with-stored-stats expectation.
    single = examples[0].x[:1]
    standardized = (single - model.norm_mean) / model.norm_std
    expected = np.clip(model.model.forward(standardized).squeeze(axis=1), 0.0, 1.0)
    assert np.array_equal(model.predict(single), expected)


def test_predict_wrong_feature_width_raises() -> None:
    examples = _examples()
    model, _ = train_heat_model(examples, epochs=2, seed=0)
    with pytest.raises(ValueError, match="35"):
        model.predict(np.zeros((3, 10)))
