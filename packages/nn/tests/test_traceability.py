"""Traceability contract for grackle_nn's watch-it-learn demo (Phase 11.2).

Proves, straight from a real grackle trace of ``demo.py`` (no direct access to
the training loop), that: every epoch's metrics are captured under the
per-node capture budget; one training step is exactly the documented 34-event
call shape; captured values format cleanly (no numpy dtype leakage, no
accidental redaction); and the model actually learns. Also pins the per-event
capture-budget accounting itself (D4) so a tracer regression fails loudly here
rather than silently degrading the watch-it-learn experience.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from grackle.adapters import registry
from grackle.adapters.base import ParseOptions, StaticGraph, TraceEvent, TraceOptions
from grackle.python_runtime.node_resolution import NodeResolver
from grackle.python_runtime.tracer import Tracer

# Warms the import cache so demo.py's own `from grackle_nn.X import Y`
# statements are cache hits during tracing. This pins the one-time
# module/class-definition event count deterministically, regardless of test
# run order or whether this file runs in isolation.
import grackle_nn  # noqa: F401

_SRC = Path(__file__).parents[1] / "src"  # trace root -- NOT the package dir; see
# demo.py's docstring-free rationale in README.md ("why --root src"): `uv sync`
# creates packages/nn/.venv, and the walker has no default excludes, so rooting
# at packages/nn would parse+trace numpy itself.
_DEMO = _SRC / "grackle_nn" / "demo.py"
_ENV_VARS = ("NN_DEMO_LR", "NN_DEMO_EPOCHS", "NN_DEMO_SEED")

_RECORD_EPOCH = "grackle_nn/metrics.py:record_epoch"
_TRAIN_STEP = "grackle_nn/train.py:train_step"

# Matches a bare numpy-scalar fallback repr (e.g. "<numpy.float64 object>") --
# the `np.float64` dispatch gap D2 guards against -- without matching a
# legitimate, deeper-nested numpy object repr like
# "<numpy.random._generator.Generator object>" (a real `rng` arg).
_NUMPY_SCALAR_LEAK = re.compile(r"numpy\.\w+ object>")

# The exact call shape of one train_step invocation with the demo's 5-layer
# net (3x Linear, 2x ReLU): forward -> loss.forward -> loss.backward ->
# backward -> optimizer.step -> zero_grad -> return. 17 invocations x 2
# events. Holds only because Sequential.backward/zero_grad iterate
# self.layers inline and never call self.gradients()/self.parameters().
_GOLDEN_34: list[tuple[str, str]] = [
    ("call", "grackle_nn/train.py:train_step"),
    ("call", "grackle_nn/model.py:Sequential.forward"),
    ("call", "grackle_nn/layers.py:Linear.forward"),
    ("return", "grackle_nn/layers.py:Linear.forward"),
    ("call", "grackle_nn/layers.py:ReLU.forward"),
    ("return", "grackle_nn/layers.py:ReLU.forward"),
    ("call", "grackle_nn/layers.py:Linear.forward"),
    ("return", "grackle_nn/layers.py:Linear.forward"),
    ("call", "grackle_nn/layers.py:ReLU.forward"),
    ("return", "grackle_nn/layers.py:ReLU.forward"),
    ("call", "grackle_nn/layers.py:Linear.forward"),
    ("return", "grackle_nn/layers.py:Linear.forward"),
    ("return", "grackle_nn/model.py:Sequential.forward"),
    ("call", "grackle_nn/losses.py:SoftmaxCrossEntropy.forward"),
    ("return", "grackle_nn/losses.py:SoftmaxCrossEntropy.forward"),
    ("call", "grackle_nn/losses.py:SoftmaxCrossEntropy.backward"),
    ("return", "grackle_nn/losses.py:SoftmaxCrossEntropy.backward"),
    ("call", "grackle_nn/model.py:Sequential.backward"),
    ("call", "grackle_nn/layers.py:Linear.backward"),
    ("return", "grackle_nn/layers.py:Linear.backward"),
    ("call", "grackle_nn/layers.py:ReLU.backward"),
    ("return", "grackle_nn/layers.py:ReLU.backward"),
    ("call", "grackle_nn/layers.py:Linear.backward"),
    ("return", "grackle_nn/layers.py:Linear.backward"),
    ("call", "grackle_nn/layers.py:ReLU.backward"),
    ("return", "grackle_nn/layers.py:ReLU.backward"),
    ("call", "grackle_nn/layers.py:Linear.backward"),
    ("return", "grackle_nn/layers.py:Linear.backward"),
    ("return", "grackle_nn/model.py:Sequential.backward"),
    ("call", "grackle_nn/optim.py:SGD.step"),
    ("return", "grackle_nn/optim.py:SGD.step"),
    ("call", "grackle_nn/model.py:Sequential.zero_grad"),
    ("return", "grackle_nn/model.py:Sequential.zero_grad"),
    ("return", "grackle_nn/train.py:train_step"),
]


def _run(
    *, epochs: int | None = None, capture_first_n: int
) -> tuple[StaticGraph, list[TraceEvent]]:
    """Fresh parse + Tracer per call: capture-budget counters are per-Tracer.
    epochs=None leaves NN_DEMO_EPOCHS unset, tracing the demo's own default (60)."""
    with pytest.MonkeyPatch.context() as mp:
        for var in _ENV_VARS:
            mp.delenv(var, raising=False)
        if epochs is not None:
            mp.setenv("NN_DEMO_EPOCHS", str(epochs))
        static_parser = registry.get_static("python")
        assert static_parser is not None
        graph = static_parser.parse(_SRC, ParseOptions())
        resolver = NodeResolver(_SRC, graph)
        tracer = Tracer(
            resolver, TraceOptions(capture_values=True, capture_first_n=capture_first_n)
        )
        events = tracer.run(_DEMO)
    return graph, events


@pytest.fixture(scope="module")
def traced() -> tuple[StaticGraph, list[TraceEvent]]:
    return _run(capture_first_n=200)


def test_total_events_within_budget(traced: tuple[StaticGraph, list[TraceEvent]]) -> None:
    _, events = traced
    # Drift guard for the frontend's 50k time-travel cliff, and a tripwire for
    # a broken demo (e.g. an accidental generator) blowing the count up by an
    # order of magnitude. A per-batch evaluate() only moves the total to
    # ~37.6k -- still in-band -- so that specific regression is pinned
    # precisely below instead of relying on this band to catch it.
    assert 10_000 < len(events) < 40_000
    evaluate_calls = [
        e for e in events if e["node_id"] == "grackle_nn/train.py:evaluate" and e["event"] == "call"
    ]
    assert len(evaluate_calls) == 60  # once per epoch, never per-batch


def test_no_unresolved_frames_and_ids_subset_of_static_graph(
    traced: tuple[StaticGraph, list[TraceEvent]],
) -> None:
    graph, events = traced
    assert not any(e["node_id"] == "<unresolved>" for e in events)
    static_ids = {n["id"] for n in graph["nodes"]}
    traced_ids = {e["node_id"] for e in events}
    assert traced_ids <= static_ids


def test_record_epoch_return_captured_every_epoch(
    traced: tuple[StaticGraph, list[TraceEvent]],
) -> None:
    _, events = traced
    returns = [e for e in events if e["node_id"] == _RECORD_EPOCH and e["event"] == "return"]
    assert len(returns) == 60
    for e in returns:
        assert "values" in e
        assert "ret" in e["values"]


def test_record_epoch_ret_parses_as_builtin_tuple(
    traced: tuple[StaticGraph, list[TraceEvent]],
) -> None:
    _, events = traced
    returns = [e for e in events if e["node_id"] == _RECORD_EPOCH and e["event"] == "return"]
    for e in returns:
        parsed = ast.literal_eval(e["values"]["ret"])
        assert isinstance(parsed, tuple)
        epoch, loss, accuracy = parsed
        assert type(epoch) is int
        assert type(loss) is float
        assert type(accuracy) is float


def test_no_numpy_dtype_leakage(traced: tuple[StaticGraph, list[TraceEvent]]) -> None:
    _, events = traced
    captured_args = [arg for e in events if "values" in e for arg in e["values"].get("args", [])]
    captured_rets = [e["values"]["ret"] for e in events if "values" in e and "ret" in e["values"]]
    # Non-vacuous: if capture ever silently stopped producing values, the
    # scans below would pass having asserted nothing.
    assert captured_args
    assert captured_rets
    for arg in captured_args:
        assert not _NUMPY_SCALAR_LEAK.search(arg["repr"])
    for ret in captured_rets:
        assert not _NUMPY_SCALAR_LEAK.search(ret)


def test_epoch_metrics_show_learning(traced: tuple[StaticGraph, list[TraceEvent]]) -> None:
    _, events = traced
    returns = [e for e in events if e["node_id"] == _RECORD_EPOCH and e["event"] == "return"]
    epochs = [ast.literal_eval(e["values"]["ret"]) for e in returns]
    # The captured epoch index is the loss-curve x-axis the time-travel UI
    # reads (record_epoch's whole reason to exist); pin it directly rather
    # than trusting it via len(returns) alone.
    assert [e[0] for e in epochs] == list(range(60))
    first_loss = epochs[0][1]
    last_loss = epochs[-1][1]
    # Checked over the final window, not just the last epoch: accuracy
    # oscillates near convergence, so pinning only the final epoch is
    # fragile to any benign RNG-consumption reorder shifting which epoch
    # happens to land last.
    last_window_accuracy = min(e[2] for e in epochs[-5:])
    assert last_window_accuracy >= 0.95
    assert last_loss < first_loss


def test_step1_call_sequence_matches_golden(traced: tuple[StaticGraph, list[TraceEvent]]) -> None:
    _, events = traced
    start = next(
        i for i, e in enumerate(events) if e["node_id"] == _TRAIN_STEP and e["event"] == "call"
    )
    depth = events[start]["frame_depth"]
    end = next(
        i
        for i in range(start + 1, len(events))
        if events[i]["node_id"] == _TRAIN_STEP
        and events[i]["event"] == "return"
        and events[i]["frame_depth"] == depth
    )
    sequence = [(e["event"], e["node_id"]) for e in events[start : end + 1]]
    assert sequence == _GOLDEN_34


def test_ndarray_args_summarized(traced: tuple[StaticGraph, list[TraceEvent]]) -> None:
    _, events = traced
    call = next(
        e
        for e in events
        if e["node_id"] == "grackle_nn/layers.py:Linear.forward" and e["event"] == "call"
    )
    args = {a["name"]: a["repr"] for a in call["values"]["args"]}
    assert args["x"] == "<ndarray shape=(32, 2) dtype=dtype('float64')>"


def test_no_redaction_false_positives(traced: tuple[StaticGraph, list[TraceEvent]]) -> None:
    _, events = traced
    captured_args = [arg for e in events if "values" in e for arg in e["values"].get("args", [])]
    # Non-vacuous: if capture ever silently stopped producing args, the loop
    # below would pass having asserted nothing.
    assert captured_args
    for arg in captured_args:
        assert not arg.get("redacted")


def test_small_run_env_override_under_tracer() -> None:
    _, events = _run(epochs=3, capture_first_n=200)
    returns = [e for e in events if e["node_id"] == _RECORD_EPOCH and e["event"] == "return"]
    assert len(returns) == 3
    # Sizing formula for this net/dataset: E x (S x 34 + 20) + C, with S=12
    # batches/epoch and C=28 one-time (import + init) events -- empirically
    # 28 + 3x428 = 1312. Slack tolerates one-time-constant (C) drift and a
    # per-step insertion (golden-34 above catches that class precisely
    # regardless); a per-epoch insertion outside train_step/evaluate/
    # record_epoch is small enough (+/-6 at E=3) to hide inside this slack.
    # Pins the sizing formula against API drift.
    assert abs(len(events) - 1312) <= 50


def test_capture_budget_semantics_pinned() -> None:
    """Per-event budget accounting (D4): call1->1, ret1->2, call2->3, ret2->4,
    exhausted -- the third invocation's call and return both go uncaptured.
    Fails loudly if the tracer's per-event accounting ever changes."""
    _, events = _run(epochs=3, capture_first_n=4)
    node_events = [e for e in events if e["node_id"] == _RECORD_EPOCH]
    calls = [e for e in node_events if e["event"] == "call"]
    returns = [e for e in node_events if e["event"] == "return"]
    assert len(calls) == 3
    assert len(returns) == 3
    assert ["values" in e for e in calls] == [True, True, False]
    assert ["values" in e for e in returns] == [True, True, False]
