import math

import numpy as np
from numpy.testing import assert_allclose, assert_array_equal

from grackle_nn.layers import Linear, ReLU, Tanh


def test_linear_forward_output_shape() -> None:
    rng = np.random.default_rng(0)
    layer = Linear(3, 5, rng=rng)
    x = rng.standard_normal((4, 3))
    out = layer.forward(x)
    assert out.shape == (4, 5)


def test_linear_forward_matches_hand_computed_value() -> None:
    layer = Linear(2, 2, rng=np.random.default_rng(0))
    layer.params[0] = np.array([[1.0, 2.0], [3.0, 4.0]])
    layer.params[1] = np.array([0.5, -0.5])
    x = np.array([[1.0, 1.0], [2.0, 0.0]])
    out = layer.forward(x)
    expected = np.array([[4.5, 5.5], [2.5, 3.5]])
    assert_array_equal(out, expected)


def test_linear_backward_matches_closed_form() -> None:
    rng = np.random.default_rng(1)
    layer = Linear(3, 4, rng=rng)
    x = rng.standard_normal((5, 3))
    layer.forward(x)
    R = rng.standard_normal((5, 4))
    dx = layer.backward(R)
    W = layer.params[0]
    assert_array_equal(dx, R @ W.T)
    assert_array_equal(layer.grads[0], x.T @ R)
    assert_array_equal(layer.grads[1], R.sum(axis=0))


def test_linear_backward_accumulates_without_rebinding() -> None:
    rng = np.random.default_rng(2)
    layer = Linear(3, 2, rng=rng)
    x = rng.standard_normal((4, 3))
    layer.forward(x)
    g1 = rng.standard_normal((4, 2))
    g2 = rng.standard_normal((4, 2))
    grads0_id = id(layer.grads[0])
    grads1_id = id(layer.grads[1])
    layer.backward(g1)
    layer.backward(g2)
    assert id(layer.grads[0]) == grads0_id
    assert id(layer.grads[1]) == grads1_id
    assert_array_equal(layer.grads[0], x.T @ g1 + x.T @ g2)
    assert_array_equal(layer.grads[1], g1.sum(axis=0) + g2.sum(axis=0))


def test_linear_init_is_deterministic_given_seed() -> None:
    layer_a = Linear(5, 3, rng=np.random.default_rng(7))
    layer_b = Linear(5, 3, rng=np.random.default_rng(7))
    assert_array_equal(layer_a.params[0], layer_b.params[0])


def test_he_and_xavier_init_statistics() -> None:
    he = Linear(1000, 1000, rng=np.random.default_rng(0), init="he")
    w_he = he.params[0]
    assert abs(w_he.std() / math.sqrt(2 / 1000) - 1) < 0.2
    assert abs(w_he.mean()) < 0.005

    xavier = Linear(1000, 1000, rng=np.random.default_rng(0), init="xavier")
    w_xavier = xavier.params[0]
    a = math.sqrt(6 / 2000)
    assert w_xavier.max() <= a
    assert w_xavier.max() > 0.95 * a
    assert_allclose(w_xavier.std(), a / math.sqrt(3), rtol=0.2)


def test_relu_forward_and_backward() -> None:
    layer = ReLU()
    x = np.array([[-1.0, 2.0, -3.0, 4.0]])
    out = layer.forward(x)
    assert_array_equal(out, np.array([[0.0, 2.0, 0.0, 4.0]]))
    grad = np.array([[10.0, 20.0, 30.0, 40.0]])
    dx = layer.backward(grad)
    assert_array_equal(dx, np.array([[0.0, 20.0, 0.0, 40.0]]))


def test_tanh_forward_and_backward() -> None:
    layer = Tanh()
    x = np.array([[0.1, -0.2, 0.3]])
    out = layer.forward(x)
    assert_array_equal(out, np.tanh(x))
    grad = np.array([[1.0, 2.0, 3.0]])
    dx = layer.backward(grad)
    assert_array_equal(dx, grad * (1 - np.tanh(x) ** 2))


def test_stateless_layers_have_empty_params_and_grads() -> None:
    relu = ReLU()
    tanh = Tanh()
    assert relu.params == []
    assert relu.grads == []
    assert tanh.params == []
    assert tanh.grads == []
