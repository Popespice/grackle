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
re-learning. What it does *not* do is make the model's answer to an unseen flag meaningful: that
column's first-layer weights were never trained, so the response is bounded but arbitrary.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import numpy as np

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


def test_marking_a_node_async_moves_no_other_row() -> None:
    graph, heat = _graph()
    model, _ = train_heat_model([build_example("root", graph, heat)], seed=0)
    _, before = extract_features(graph)
    _, after = extract_features(_graph(async_node=4)[0])
    moved = np.abs(model.predict(after) - model.predict(before))
    assert np.count_nonzero(moved) <= 1
    # Bounded, not pinned to a clip bound by a 1e8 input. The size is arbitrary (the flag's
    # first-layer weights were never trained: 0.25 at seed 0, 0.002 to 0.33 across seeds 0-5).
    assert moved[4 + 2] < 0.5


def _model_with_legacy_floor(tmp_path: Path) -> tuple[HeatModel, HeatModel, Path]:
    graph, heat = _graph()
    model, _ = train_heat_model([build_example("root", graph, heat)], epochs=2, seed=0)
    path = tmp_path / "legacy.npz"
    model.save(path)
    with np.load(path) as npz:
        arrays: dict[str, Any] = {key: np.array(npz[key]) for key in npz.files}
    constant = model.norm_std == 1.0
    assert constant.any()
    arrays["norm_std"] = np.where(constant, _LEGACY_FLOOR, arrays["norm_std"])
    with path.open("wb") as fh:
        np.savez(fh, **arrays)
    return model, HeatModel.load(path), path


def test_a_checkpoint_saved_under_the_old_floor_is_repaired_on_load(tmp_path: Path) -> None:
    fresh, legacy, path = _model_with_legacy_floor(tmp_path)
    with np.load(path) as npz:
        assert (np.asarray(npz["norm_std"]) == _LEGACY_FLOOR).any()  # really an old file
    assert np.array_equal(legacy.norm_std, fresh.norm_std)
    _, edited = extract_features(_graph(async_node=4)[0])
    assert np.array_equal(legacy.predict(edited), fresh.predict(edited))


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
