"""Real-pair integration test for grackle_nn.ml (Phase 12.1, block M7).

Builds Examples from real committed fixture data (no synthetic corpus) via
the grackle **dev** dependency (11.2 precedent — allowed in tests only;
``grackle_nn.ml`` itself never imports it, see M8). Two Python pairs prove
the on-disk round trip (parse -> trace -> write_jsonl -> load_pair) and that
training actually learns; a polyglot sweep over the committed Go/Rust/Node
goldens proves the label pipeline is language-agnostic (count weighting via
``metadata.count`` for Go/Rust, default weight 1 for Node/Python) with zero
toolchain requirement -- these are all pre-parsed committed files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pytest

from grackle_nn.ml.dataset import build_example, load_pair
from grackle_nn.ml.heat_model import train_heat_model
from grackle_nn.ml.labels import heat_from_jsonl
from grackle_nn.ml.metrics_rank import spearman

if TYPE_CHECKING:
    from grackle_nn.ml.dataset import Example

_FIXTURES = Path(__file__).parents[4] / "fixtures"
_NN_SRC = Path(__file__).parents[2] / "src"
_NN_DEMO = _NN_SRC / "grackle_nn" / "demo.py"
_ENV_VARS = ("NN_DEMO_LR", "NN_DEMO_EPOCHS", "NN_DEMO_SEED")


@pytest.fixture(scope="module")
def tiny_python_app_example() -> Example:
    from grackle.adapters import registry
    from grackle.adapters.base import ParseOptions

    root = _FIXTURES / "tiny-python-app"
    static_parser = registry.get_static("python")
    assert static_parser is not None
    graph = static_parser.parse(root, ParseOptions())
    heat = heat_from_jsonl(root / "trace.golden.jsonl")
    return build_example("tiny-python-app", graph, heat)


@pytest.fixture(scope="module")
def nn_demo_example(tmp_path_factory: pytest.TempPathFactory) -> Example:
    from grackle.adapters import registry
    from grackle.adapters.base import ParseOptions, TraceOptions
    from grackle.python_runtime.node_resolution import NodeResolver
    from grackle.python_runtime.tracer import Tracer
    from grackle.python_runtime.writer import write_jsonl

    with pytest.MonkeyPatch.context() as mp:
        for var in _ENV_VARS:
            mp.delenv(var, raising=False)
        mp.setenv("NN_DEMO_EPOCHS", "3")

        static_parser = registry.get_static("python")
        assert static_parser is not None
        graph = static_parser.parse(_NN_SRC, ParseOptions())
        resolver = NodeResolver(_NN_SRC, graph)
        tracer = Tracer(resolver, TraceOptions(capture_values=True, capture_first_n=200))
        events = tracer.run(_NN_DEMO)

    tmp_path = tmp_path_factory.mktemp("nn-demo-pair")
    graph_path = tmp_path / "nn-demo.graph.json"
    trace_path = tmp_path / "nn-demo.trace.jsonl"
    graph_path.write_text(json.dumps(graph), encoding="utf-8")
    write_jsonl(events, trace_path)

    return load_pair("nn-demo", graph_path, trace_path)


def test_tiny_python_app_example_has_touched_nodes_and_normalized_max(
    tiny_python_app_example: Example,
) -> None:
    assert (tiny_python_app_example.counts > 0).any()
    assert tiny_python_app_example.y.max() == 1.0
    assert not np.isnan(tiny_python_app_example.y).any()


def test_nn_demo_example_has_touched_nodes_and_normalized_max(
    nn_demo_example: Example,
) -> None:
    assert (nn_demo_example.counts > 0).any()
    assert nn_demo_example.y.max() == 1.0
    assert not np.isnan(nn_demo_example.y).any()


def test_train_heat_model_on_demo_loss_decreases(nn_demo_example: Example) -> None:
    _, history = train_heat_model([nn_demo_example], epochs=50, seed=0)
    losses = [h[1] for h in history]
    assert losses[-1] < losses[0]
    assert all(np.isfinite(loss) for loss in losses)


def test_train_set_spearman_on_demo_exceeds_half(nn_demo_example: Example) -> None:
    model, _ = train_heat_model([nn_demo_example], epochs=50, seed=0)
    pred = model.predict(nn_demo_example.x)
    counts = nn_demo_example.counts.astype(np.float64)
    assert spearman(pred, counts) > 0.5


def test_no_nans_anywhere(tiny_python_app_example: Example, nn_demo_example: Example) -> None:
    for ex in (tiny_python_app_example, nn_demo_example):
        assert not np.isnan(ex.x).any()
        assert not np.isnan(ex.y).any()
    model, _ = train_heat_model([nn_demo_example], epochs=10, seed=0)
    assert not np.isnan(model.predict(nn_demo_example.x)).any()


@pytest.mark.parametrize(
    ("fixture", "language"),
    [
        ("tiny-go-app", "go"),
        ("tiny-rust-app", "rust"),
        ("tiny-node-app", "typescript"),
    ],
)
def test_polyglot_corpus_examples_are_language_agnostic(fixture: str, language: str) -> None:
    """Real committed goldens for three non-Python languages -- no toolchain
    needed, they're pre-parsed static files. Proves build_example's label
    pipeline is language-agnostic, including metadata.count weighting for
    Go/Rust's coarse coverage-derived events."""
    from grackle.adapters import registry
    from grackle.adapters.base import ParseOptions

    root = _FIXTURES / fixture
    static_parser = registry.get_static(language)
    assert static_parser is not None
    graph = static_parser.parse(root, ParseOptions())
    heat = heat_from_jsonl(root / "trace.golden.jsonl")
    example = build_example(fixture, graph, heat)

    assert (example.counts > 0).any()
    assert example.y.max() == 1.0
    assert not np.isnan(example.y).any()
