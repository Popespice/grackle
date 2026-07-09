from pathlib import Path

import numpy as np
from numpy.testing import assert_array_equal

from grackle_nn.layers import Linear, ReLU
from grackle_nn.model import Sequential


def test_sequential_forward_matches_manual_chain() -> None:
    x = np.random.default_rng(1).standard_normal((4, 2))

    model = Sequential(
        Linear(2, 3, rng=np.random.default_rng(5)),
        ReLU(),
        Linear(3, 1, rng=np.random.default_rng(6)),
    )
    layer_a = Linear(2, 3, rng=np.random.default_rng(5))
    layer_b = ReLU()
    layer_c = Linear(3, 1, rng=np.random.default_rng(6))

    out = model.forward(x)

    h = layer_a.forward(x)
    h = layer_b.forward(h)
    expected = layer_c.forward(h)

    assert_array_equal(out, expected)


def test_sequential_backward_reverses_and_matches_closed_form() -> None:
    rng = np.random.default_rng(10)
    x = rng.standard_normal((4, 2))
    model = Sequential(
        Linear(2, 3, rng=np.random.default_rng(11)),
        Linear(3, 5, rng=np.random.default_rng(12)),
    )

    model.forward(x)
    grad_out = rng.standard_normal((4, 5))
    dx = model.backward(grad_out)

    W0, b0 = model.layers[0].params
    W1, b1 = model.layers[1].params

    h = x @ W0 + b0
    expected_dx = grad_out @ W1.T @ W0.T
    assert_array_equal(dx, expected_dx)

    grad_h = grad_out @ W1.T
    assert_array_equal(model.layers[1].grads[0], h.T @ grad_out)
    assert_array_equal(model.layers[1].grads[1], grad_out.sum(axis=0))
    assert_array_equal(model.layers[0].grads[0], x.T @ grad_h)
    assert_array_equal(model.layers[0].grads[1], grad_h.sum(axis=0))


def test_sequential_parameters_and_gradients_aligned_and_ordered() -> None:
    model = Sequential(
        Linear(2, 3, rng=np.random.default_rng(20)),
        ReLU(),
        Linear(3, 1, rng=np.random.default_rng(21)),
    )

    params = model.parameters()
    grads = model.gradients()

    assert len(params) == 4
    assert len(grads) == 4

    assert params[0] is model.layers[0].params[0]
    assert params[1] is model.layers[0].params[1]
    assert params[2] is model.layers[2].params[0]
    assert params[3] is model.layers[2].params[1]

    assert grads[0] is model.layers[0].grads[0]
    assert grads[1] is model.layers[0].grads[1]
    assert grads[2] is model.layers[2].grads[0]
    assert grads[3] is model.layers[2].grads[1]


def test_sequential_zero_grad_zeros_in_place_without_rebinding() -> None:
    rng = np.random.default_rng(30)
    x = rng.standard_normal((4, 2))
    model = Sequential(
        Linear(2, 3, rng=np.random.default_rng(31)),
        ReLU(),
        Linear(3, 1, rng=np.random.default_rng(32)),
    )

    out = model.forward(x)
    model.backward(rng.standard_normal(out.shape))

    grads_before = model.gradients()
    assert any(np.any(g != 0.0) for g in grads_before)
    ids_before = [id(g) for g in grads_before]

    model.zero_grad()

    grads_after = model.gradients()
    assert [id(g) for g in grads_after] == ids_before
    for g in grads_after:
        assert np.all(g == 0.0)


def test_sequential_save_load_roundtrip_same_objects(tmp_path: Path) -> None:
    model_a = Sequential(
        Linear(2, 3, rng=np.random.default_rng(40)),
        ReLU(),
        Linear(3, 1, rng=np.random.default_rng(41)),
    )
    x = np.random.default_rng(42).standard_normal((4, 2))
    out = model_a.forward(x)
    model_a.backward(np.random.default_rng(43).standard_normal(out.shape))

    path = tmp_path / "model.npz"
    model_a.save(path)

    model_b = Sequential(
        Linear(2, 3, rng=np.random.default_rng(90)),
        ReLU(),
        Linear(3, 1, rng=np.random.default_rng(91)),
    )
    params_b = model_b.parameters()
    ids_before = [id(p) for p in params_b]

    model_b.load(path)

    assert [id(p) for p in model_b.parameters()] == ids_before
    for p_a, p_b in zip(model_a.parameters(), model_b.parameters(), strict=True):
        assert_array_equal(p_b, p_a)


def test_sequential_save_is_atomic_and_overwrite_safe(tmp_path: Path) -> None:
    path = tmp_path / "model.npz"

    model_a = Sequential(Linear(2, 2, rng=np.random.default_rng(50)))
    model_a.save(path)
    assert path.exists()
    assert list(tmp_path.glob("*.tmp")) == []

    model_c = Sequential(Linear(2, 2, rng=np.random.default_rng(51)))
    model_c.save(path)
    assert path.exists()
    assert list(tmp_path.glob("*.tmp")) == []

    model_d = Sequential(Linear(2, 2, rng=np.random.default_rng(999)))
    model_d.load(path)

    assert_array_equal(model_d.parameters()[0], model_c.parameters()[0])
    assert not np.array_equal(model_d.parameters()[0], model_a.parameters()[0])
