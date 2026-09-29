"""Standardization of features that were constant in training (campaign T9-8, fixed).

Surfaced by the T10-2 tolerance study (``test_checkpoint_pins.py``). ``train_heat_model``
used to store ``norm_std = max(x.std(axis=0), 1e-8)``. The floor kept a constant column from
dividing by zero during training, but a column that is constant in training is ordinary:
``grackle learn`` trains on one project graph (D12.7), and a project with no ``async``
functions, no decorators, no classes or no cross-language edges has those columns all zero.
When a node later showed a value training never saw (an ``async def`` added under ``serve
--watch``, or a model scored against another project), its standardized input was
``(1 - 0) / 1e-8 = 1e8``, and the prediction for that node was driven to a clip bound
regardless of everything else about it.

Observed with ``grackle learn``'s defaults (200 epochs) on a single graph: marking one node
``async`` moved its predicted heat from 0.44 to 0.0, and no other row changed. The same floor
turned the one-ulp rounding residual of a constant column's mean into a 2e-8 input, which was
most of the cross-platform drift the T10-2 golden has to tolerate.

The fix scales a column that was constant in training by 1.0 instead (a std at or below
``_CONSTANT_STD``, which includes that one-ulp residual). Training is unchanged, because
``x - mean`` is 0 on such a column; an unseen 0/1 flag now arrives as exactly 1.0. The same
rule runs when a checkpoint is loaded, so one saved under the old floor is repaired without
re-learning.

That alone left the answer to an unseen flag bounded but arbitrary, because the column's
first-layer weights were never trained: they sat at their random init, and the edited node's
prediction moved by 0.002 to 0.33 across seeds. Those rows are now zeroed (before training, with
the column's training input pinned to exactly 0 so Adam cannot move them, and again on load for a
checkpoint saved before the fix), so an unseen value is ignored outright.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from grackle_nn.losses import MSE
from grackle_nn.ml.dataset import build_example, stack
from grackle_nn.ml.features import FEATURE_NAMES, extract_features
from grackle_nn.ml.heat_model import HeatModel, train_heat_model

if TYPE_CHECKING:
    from pathlib import Path

_IS_ASYNC = FEATURE_NAMES.index("is_async")
_LEGACY_FLOOR = 1e-8


def _graph(async_node: int | None = None) -> tuple[dict[str, Any], dict[str, int]]:
    """Twelve functions in two files, none async unless *async_node* is given."""
    nodes: list[dict[str, Any]] = [
        {"id": f, "kind": "file", "name": f, "path": f} for f in ("a.py", "b.py")
    ]
    for i in range(12):
        path = "a.py" if i % 2 else "b.py"
        node: dict[str, Any] = {
            "id": f"{path}:f{i}",
            "kind": "method" if i % 3 == 0 else "function",
            "name": f"f{i}",
            "path": path,
            "line": 10 * i + 1,
        }
        if i == async_node:
            node["metadata"] = {"is_async": True}
        nodes.append(node)
    ids = [n["id"] for n in nodes[2:]]
    edges = [
        {"source": ids[i], "target": ids[(i * 5 + 1) % i], "kind": "call"} for i in range(1, 12)
    ]
    edges += [{"source": "a.py", "target": ids[i], "kind": "import"} for i in (0, 3, 6)]
    heat = {ids[i]: 1 + (i * 7) % 11 for i in range(12)}
    return {"version": 1, "language": "python", "nodes": nodes, "edges": edges}, heat


def test_precondition_is_async_is_constant_in_training() -> None:
    # Keeps the ledgered test below honest: if the fixture ever grew an async
    # node, that test could pass for the wrong reason.
    graph, heat = _graph()
    ex = build_example("root", graph, heat)
    assert ex.x[:, _IS_ASYNC].std() == 0.0
    _, edited = extract_features(_graph(async_node=4)[0])
    assert edited[4 + 2, _IS_ASYNC] == 1.0  # +2: the two file nodes come first


def test_an_unseen_binary_feature_value_reaches_the_model_bounded() -> None:
    graph, heat = _graph()
    model, _ = train_heat_model([build_example("root", graph, heat)], epochs=20, seed=0)
    _, edited = extract_features(_graph(async_node=4)[0])
    standardized = (edited[4 + 2] - model.norm_mean) / model.norm_std
    # A 0/1 flag that training only ever saw as 0 arrives as itself: exactly 1.0, where the
    # 1e-8 floor made it 1e8.
    assert float(standardized[_IS_ASYNC]) == 1.0
    assert float(np.abs(standardized).max()) < 1e4


def test_only_columns_constant_in_training_get_scale_one() -> None:
    graph, heat = _graph()
    example = build_example("root", graph, heat)
    model, _ = train_heat_model([example], epochs=2, seed=0)
    raw_std = stack([example])[0].std(axis=0)
    constant = raw_std <= _LEGACY_FLOOR
    # The fixture has both kinds, or this would prove nothing about the split.
    assert constant.any()
    assert not constant.all()
    assert np.array_equal(model.norm_std[constant], np.ones(int(constant.sum())))
    assert np.array_equal(model.norm_std[~constant], raw_std[~constant])


@pytest.mark.parametrize("seed", [0, 1, 4])
def test_marking_a_node_async_changes_no_prediction(seed: int) -> None:
    graph, heat = _graph()
    model, _ = train_heat_model([build_example("root", graph, heat)], seed=seed)
    _, before = extract_features(graph)
    _, after = extract_features(_graph(async_node=4)[0])
    # Ignored outright, not merely bounded. With only the 1.0 scale the edited row moved by
    # 0.25, 0.002 and 0.33 at these three seeds, and 0.44 -> 0.0 before T9-8's first fix.
    assert np.array_equal(model.predict(after), model.predict(before))


def test_constant_columns_have_exactly_zero_first_layer_rows() -> None:
    graph, heat = _graph()
    example = build_example("root", graph, heat)
    # The default 200 epochs: what Adam does to a row is only visible after many steps.
    model, _ = train_heat_model([example], seed=0)
    constant = stack([example])[0].std(axis=0) <= _LEGACY_FLOOR
    first_layer = model.model.parameters()[0]
    assert constant.any()
    assert not constant.all()
    assert np.array_equal(first_layer[constant], np.zeros((int(constant.sum()), 64)))
    # Only those rows: a column that varied keeps the weights training gave it.
    assert np.all(np.any(first_layer[~constant] != 0.0, axis=1))


def test_a_constant_column_with_a_one_ulp_residual_stays_out_of_the_model() -> None:
    """The residual case that pinning the training input to 0 exists for. ``log1p(2)`` on every
    row has a mean one ulp off its value, so the standardized column is 2.2e-16, not 0; Adam
    normalizes a gradient of any size, so left alone it walks the zeroed rows away from 0."""
    graph, heat = _graph()
    example = build_example("root", graph, heat)
    column = FEATURE_NAMES.index("log1p_path_depth")
    x = example.x.copy()
    x[:, column] = np.log1p(2.0)
    assert 0.0 < x[:, column].std() <= _LEGACY_FLOOR  # constant, yet the mean is not exact
    model, _ = train_heat_model([dataclasses.replace(example, x=x)], seed=0)
    assert np.array_equal(model.model.parameters()[0][column], np.zeros(64))


def test_the_val_loss_still_belongs_to_the_returned_model_when_val_varies_a_constant_column() -> (
    None
):
    """Zeroing must not make ``val_loss`` stale: a val set in which a training-constant column
    varies is exactly where a model whose rows were zeroed only at the end would disagree with
    its own history."""
    graph, heat = _graph()
    train = build_example("train", graph, heat)
    val = build_example("val", _graph(async_node=4)[0], heat)
    model, history = train_heat_model([train], epochs=3, seed=0, val=[val])
    val_x, val_y = stack([val])
    standardized = (val_x - model.norm_mean) / model.norm_std
    expected = MSE().forward(model.model.forward(standardized), val_y.reshape(-1, 1))
    assert history[-1][2] == pytest.approx(expected, rel=1e-12, abs=0)


_UNTRAINED = 0.3  # what a pre-fix checkpoint holds in a constant column's rows: random init


def _rewritten(path: Path, model: HeatModel, **changes: Any) -> HeatModel:
    """Save *model*, replace some arrays in the file, and load the result."""
    model.save(path)
    with np.load(path) as npz:
        arrays: dict[str, Any] = {key: np.array(npz[key]) for key in npz.files}
    arrays.update(changes)
    with path.open("wb") as fh:
        np.savez(fh, **arrays)
    return HeatModel.load(path)


def _trained_fixture() -> tuple[HeatModel, Any]:
    graph, heat = _graph()
    example = build_example("root", graph, heat)
    model, _ = train_heat_model([example], epochs=2, seed=0)
    return model, stack([example])[0].std(axis=0) <= _LEGACY_FLOOR


def _pre_fix_checkpoint(tmp_path: Path) -> tuple[HeatModel, HeatModel, Path]:
    """A checkpoint as v0.12.0 wrote it: 1e-8 for a constant column, untrained rows for it."""
    model, constant = _trained_fixture()
    assert constant.any()
    w0 = model.model.parameters()[0].copy()
    w0[constant] = _UNTRAINED
    path = tmp_path / "legacy.npz"
    legacy = _rewritten(
        path, model, norm_std=np.where(constant, _LEGACY_FLOOR, model.norm_std), w0=w0
    )
    return model, legacy, path


def test_a_checkpoint_saved_under_the_old_floor_is_repaired_on_load(tmp_path: Path) -> None:
    fresh, legacy, path = _pre_fix_checkpoint(tmp_path)
    with np.load(path) as npz:  # really an old file: the floor, and rows that were never zeroed
        assert (np.asarray(npz["norm_std"]) == _LEGACY_FLOOR).any()
        assert (np.asarray(npz["w0"]) == _UNTRAINED).any()
    assert np.array_equal(legacy.norm_std, fresh.norm_std)
    assert np.array_equal(legacy.model.parameters()[0], fresh.model.parameters()[0])
    _, edited = extract_features(_graph(async_node=4)[0])
    assert np.array_equal(legacy.predict(edited), fresh.predict(edited))


def test_load_zeroes_rows_only_up_to_the_old_floor(tmp_path: Path) -> None:
    model, _ = _trained_fixture()
    trained = model.model.parameters()[0]
    varied = [i for i in range(trained.shape[0]) if np.any(trained[i] != 0.0)][:3]
    assert len(varied) == 3
    just_above = np.nextafter(_LEGACY_FLOOR, 1.0)
    std = model.norm_std.copy()
    std[varied] = [_LEGACY_FLOOR, just_above, 1.0]
    loaded = _rewritten(tmp_path / "edge.npz", model, norm_std=std)
    rows = loaded.model.parameters()[0]
    # The old floor itself means "constant" and is repaired. Anything above it is a real std,
    # including exactly 1.0, which is also what a constant column now stores: that must not be
    # taken for one, or a trained row would be thrown away.
    assert np.array_equal(rows[varied[0]], np.zeros(64))
    assert np.array_equal(rows[varied[1]], trained[varied[1]])
    assert np.array_equal(rows[varied[2]], trained[varied[2]])


def test_the_constant_column_boundary_is_inclusive_of_the_old_floor() -> None:
    graph, heat = _graph()
    model, _ = train_heat_model([build_example("root", graph, heat)], epochs=1, seed=0)
    just_above = np.nextafter(_LEGACY_FLOOR, 1.0)
    std = np.array([0.0, 2.2e-16, _LEGACY_FLOOR, just_above, 1e-6, 0.5] + [3.0] * 29)
    rebuilt = HeatModel(model.model, model.norm_mean, std)
    assert rebuilt.norm_std[:3].tolist() == [1.0, 1.0, 1.0]
    # A column with real (if tiny) variance keeps its own scale.
    assert rebuilt.norm_std[3:6].tolist() == [just_above, 1e-6, 0.5]
    # Idempotent: repairing an already-repaired vector changes nothing.
    again = HeatModel(model.model, model.norm_mean, rebuilt.norm_std)
    assert np.array_equal(again.norm_std, rebuilt.norm_std)


def test_a_nan_std_is_not_hidden_by_the_constant_column_rule() -> None:
    graph, heat = _graph()
    model, _ = train_heat_model([build_example("root", graph, heat)], epochs=1, seed=0)
    std = model.norm_std.copy()
    std[_IS_ASYNC] = np.nan
    rebuilt = HeatModel(model.model, model.norm_mean, std)
    assert np.isnan(rebuilt.norm_std[_IS_ASYNC])
