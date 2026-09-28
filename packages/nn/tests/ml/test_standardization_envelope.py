"""Standardization of features that were constant in training (campaign T9-8, new in C5).

Surfaced by the T10-2 tolerance study (``test_checkpoint_pins.py``).
``train_heat_model`` stores ``norm_std = max(x.std(axis=0), 1e-8)``. The floor
keeps a constant column from dividing by zero during training, but a column
that is constant in training is ordinary: ``grackle learn`` trains on one
project graph (D12.7), and a project with no ``async`` functions, no
decorators, no classes or no cross-language edges has those columns all zero.
When a node later shows a value training never saw (an ``async def`` added
under ``serve --watch``, or a model scored against another project), its
standardized input is ``(1 - 0) / 1e-8 = 1e8``, and the prediction for that
node is driven to a clip bound regardless of everything else about it.

Observed with ``grackle learn``'s defaults (200 epochs) on a single graph:
marking one node ``async`` moved its predicted heat from 0.44 to 0.0, and no
other row changed. The same floor turns the one-ulp rounding residual of a
constant column's mean into a 2e-8 input, which is most of the
cross-platform drift the T10-2 golden has to tolerate.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from grackle_nn.ml.dataset import build_example
from grackle_nn.ml.features import FEATURE_NAMES, extract_features
from grackle_nn.ml.heat_model import train_heat_model

_IS_ASYNC = FEATURE_NAMES.index("is_async")
_T98 = (
    "T9-8: a feature constant in training is standardized with std floored at 1e-8, "
    "so an unseen value reaches the MLP as ~1e8 (docs/test-campaigns/phase-12.md)"
)


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


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_T98)
def test_an_unseen_binary_feature_value_reaches_the_model_bounded() -> None:
    graph, heat = _graph()
    model, _ = train_heat_model([build_example("root", graph, heat)], epochs=20, seed=0)
    _, edited = extract_features(_graph(async_node=4)[0])
    standardized = (edited[4 + 2] - model.norm_mean) / model.norm_std
    # A 0/1 flag that training only ever saw as 0. Any sane scale for a
    # zero-variance column keeps this O(1); the 1e-8 floor makes it 1e8.
    assert float(np.abs(standardized).max()) < 1e4
