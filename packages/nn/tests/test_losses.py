import math

import numpy as np
from numpy.testing import assert_array_equal

from grackle_nn.losses import MSE, SoftmaxCrossEntropy


def test_softmax_cross_entropy_hand_computed_cases() -> None:
    logits_a = np.array([[0.0, 0.0], [0.0, 0.0]])
    labels_a = np.array([0, 1], dtype=np.int64)
    loss_a = SoftmaxCrossEntropy().forward(logits_a, labels_a)
    assert abs(loss_a - math.log(2.0)) < 1e-12

    logits_b = np.array([[1.0, 0.0], [0.0, 1.0]])
    labels_b = np.array([0, 1], dtype=np.int64)
    loss_b = SoftmaxCrossEntropy().forward(logits_b, labels_b)
    assert abs(loss_b - math.log(1.0 + math.exp(-1.0))) < 1e-12


def test_forward_return_type_is_builtin_float_not_numpy_float64() -> None:
    # np.float64 subclasses float, so `isinstance(x, float)` is True even for the
    # bug this guards against - only `type(x) is float` catches an unconverted
    # numpy scalar leaking across the traced forward() boundary.
    logits = np.array([[1.0, 0.0], [0.0, 1.0]])
    labels = np.array([0, 1], dtype=np.int64)
    ce_loss = SoftmaxCrossEntropy().forward(logits, labels)
    assert type(ce_loss) is float

    pred = np.array([[1.0, 2.0], [3.0, 4.0]])
    target = np.zeros((2, 2))
    mse_loss = MSE().forward(pred, target)
    assert type(mse_loss) is float


def test_softmax_cross_entropy_forward_stable_at_extreme_logits() -> None:
    logits = np.array([[1e4, -1e4, 0.0]])
    labels = np.array([0], dtype=np.int64)
    loss = SoftmaxCrossEntropy().forward(logits, labels)
    assert math.isfinite(loss)


def test_softmax_cross_entropy_backward_exact_value() -> None:
    logits = np.array([[0.0, 0.0], [0.0, 0.0]])
    labels = np.array([0, 1], dtype=np.int64)
    loss_fn = SoftmaxCrossEntropy()
    loss_fn.forward(logits, labels)
    grad = loss_fn.backward()
    assert_array_equal(grad, np.array([[-0.25, 0.25], [0.25, -0.25]]))


def test_mse_forward_and_backward_known_values() -> None:
    pred = np.array([[1.0, 2.0], [3.0, 4.0]])
    target = np.zeros((2, 2))
    loss_fn = MSE()
    loss = loss_fn.forward(pred, target)
    assert loss == 7.5
    grad = loss_fn.backward()
    assert_array_equal(grad, np.array([[0.5, 1.0], [1.5, 2.0]]))
