import ast

import numpy as np
from grackle.adapters.base import TraceOptions

from grackle_nn.layers import Linear, ReLU, Tanh
from grackle_nn.metrics import (
    accuracy,
    record_architecture,
    record_epoch,
    record_layer_stats,
)
from grackle_nn.model import Sequential


def test_accuracy_known_value() -> None:
    logits = np.array([[2.0, 1.0], [0.0, 3.0], [5.0, 0.0], [1.0, 2.0]])
    labels = np.array([0, 1, 1, 1])

    acc = accuracy(logits, labels)

    assert acc == 0.75
    assert type(acc) is float


def test_record_epoch_is_identity_and_builtin_typed() -> None:
    result = record_epoch(12, 0.4321098765, 0.8697916667)

    assert result == (12, 0.4321098765, 0.8697916667)
    assert type(result[0]) is int
    assert type(result[1]) is float
    assert type(result[2]) is float
    assert ast.literal_eval(repr(result)) == result


def test_record_architecture_demo_net_string() -> None:
    rng = np.random.default_rng(0)
    model = Sequential(
        Linear(2, 32, rng=rng),
        ReLU(),
        Linear(32, 32, rng=rng),
        ReLU(),
        Linear(32, 3, rng=rng),
    )

    assert record_architecture(model) == "linear:2:32 relu linear:32:32 relu linear:32:3"


def test_record_architecture_paramless_layer_token() -> None:
    # A param-less layer (ReLU/Tanh) contributes its lowercased class name as a
    # single token; only param-carrying layers get the linear:<in>:<out> form.
    rng = np.random.default_rng(0)
    model = Sequential(Tanh(), Linear(4, 2, rng=rng))

    assert record_architecture(model) == "tanh linear:4:2"


def test_record_layer_stats_is_identity_and_builtin_typed() -> None:
    result = record_layer_stats(7, (0.5, 0.01, 0.4, 0.02, 0.3, 0.03))

    assert result == (7, 0.5, 0.01, 0.4, 0.02, 0.3, 0.03)
    assert type(result[0]) is int
    assert all(type(v) is float for v in result[1:])
    assert ast.literal_eval(repr(result)) == result


def test_record_layer_stats_repr_within_capture_budget() -> None:
    # The captured return repr is a frontend parse contract, read under the
    # tracer's real default max_value_len. A worst-case demo-scale stat tuple
    # must stay under that actual default so the values are never truncated.
    result = record_layer_stats(999, (0.00123,) * 6)

    assert len(repr(result)) < TraceOptions().max_value_len


def test_record_layer_stats_item_count_within_capture_bound() -> None:
    # A real demo-architecture stats tuple is 1 (epoch) + 2*L items for L
    # param-carrying layers; it must fit within the tracer's actual default
    # max_value_items or the captured repr elides items and breaks literal_eval.
    # Tied to the real model and the real limit (not a hardcoded literal) so a
    # demo that grew past the bound, or a lowered tracer default, is caught.
    rng = np.random.default_rng(0)
    model = Sequential(
        Linear(2, 32, rng=rng),
        ReLU(),
        Linear(32, 32, rng=rng),
        ReLU(),
        Linear(32, 3, rng=rng),
    )
    num_param_layers = sum(1 for layer in model.layers if layer.params)
    result = record_layer_stats(0, tuple(float(i) for i in range(2 * num_param_layers)))

    assert len(result) == 1 + 2 * num_param_layers  # epoch + (w_rms, dw_rms) per layer
    assert len(result) <= TraceOptions().max_value_items
