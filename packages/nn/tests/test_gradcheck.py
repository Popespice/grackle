from collections.abc import Callable

import numpy as np
from numpy.testing import assert_allclose

from grackle_nn._types import Array
from grackle_nn.layers import Linear, ReLU, Tanh
from grackle_nn.losses import MSE, SoftmaxCrossEntropy


def numeric_grad(
    f: Callable[[], float], param: Array, idx: tuple[int, ...], eps: float = 1e-5
) -> float:
    orig = float(param[idx])
    param[idx] = orig + eps
    f_plus = f()
    param[idx] = orig - eps
    f_minus = f()
    param[idx] = orig
    return (f_plus - f_minus) / (2.0 * eps)


def test_linear_gradcheck() -> None:
    rng = np.random.default_rng(100)
    layer = Linear(3, 2, rng=rng)
    x = rng.standard_normal((4, 3))
    R = rng.standard_normal((4, 2))  # fixed upstream gradient / "probe" direction

    def objective() -> float:
        return float(np.sum(layer.forward(x) * R))

    layer.grads[0][...] = 0.0
    layer.grads[1][...] = 0.0
    layer.forward(x)
    dx_analytic = layer.backward(R)
    dW_analytic = layer.grads[0].copy()
    db_analytic = layer.grads[1].copy()

    idx_rng = np.random.default_rng(101)
    W = layer.params[0]
    for _ in range(8):
        idx = tuple(int(idx_rng.integers(0, s)) for s in W.shape)
        assert_allclose(dW_analytic[idx], numeric_grad(objective, W, idx), rtol=1e-4, atol=1e-6)

    b = layer.params[1]
    for _ in range(8):
        idx = (int(idx_rng.integers(0, b.shape[0])),)
        assert_allclose(db_analytic[idx], numeric_grad(objective, b, idx), rtol=1e-4, atol=1e-6)

    for _ in range(8):
        idx = tuple(int(idx_rng.integers(0, s)) for s in x.shape)
        assert_allclose(dx_analytic[idx], numeric_grad(objective, x, idx), rtol=1e-4, atol=1e-6)


def test_relu_gradcheck() -> None:
    rng = np.random.default_rng(110)
    layer = ReLU()
    x = rng.standard_normal((4, 3))
    x[np.abs(x) < 1e-2] = 0.1
    R = rng.standard_normal((4, 3))

    def objective() -> float:
        return float(np.sum(layer.forward(x) * R))

    layer.forward(x)
    dx_analytic = layer.backward(R)

    idx_rng = np.random.default_rng(111)
    for _ in range(8):
        idx = tuple(int(idx_rng.integers(0, s)) for s in x.shape)
        assert_allclose(dx_analytic[idx], numeric_grad(objective, x, idx), rtol=1e-4, atol=1e-6)


def test_tanh_gradcheck() -> None:
    rng = np.random.default_rng(120)
    layer = Tanh()
    x = rng.standard_normal((4, 3))
    R = rng.standard_normal((4, 3))

    def objective() -> float:
        return float(np.sum(layer.forward(x) * R))

    layer.forward(x)
    dx_analytic = layer.backward(R)

    idx_rng = np.random.default_rng(121)
    for _ in range(8):
        idx = tuple(int(idx_rng.integers(0, s)) for s in x.shape)
        assert_allclose(dx_analytic[idx], numeric_grad(objective, x, idx), rtol=1e-4, atol=1e-6)


def test_softmax_cross_entropy_gradcheck() -> None:
    rng = np.random.default_rng(200)
    logits = rng.standard_normal((4, 3))
    labels = rng.integers(0, 3, size=4)
    loss_fn = SoftmaxCrossEntropy()

    def objective() -> float:
        return loss_fn.forward(logits, labels)

    loss_fn.forward(logits, labels)
    grad_analytic = loss_fn.backward()

    idx_rng = np.random.default_rng(201)
    for _ in range(8):
        idx = tuple(int(idx_rng.integers(0, s)) for s in logits.shape)
        assert_allclose(
            grad_analytic[idx], numeric_grad(objective, logits, idx), rtol=1e-4, atol=1e-6
        )


def test_mse_gradcheck() -> None:
    rng = np.random.default_rng(210)
    pred = rng.standard_normal((4, 3))
    target = rng.standard_normal((4, 3))
    loss_fn = MSE()

    def objective() -> float:
        return loss_fn.forward(pred, target)

    loss_fn.forward(pred, target)
    grad_analytic = loss_fn.backward()

    idx_rng = np.random.default_rng(211)
    for _ in range(8):
        idx = tuple(int(idx_rng.integers(0, s)) for s in pred.shape)
        assert_allclose(
            grad_analytic[idx], numeric_grad(objective, pred, idx), rtol=1e-4, atol=1e-6
        )
