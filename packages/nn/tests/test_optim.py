from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pytest
from numpy.testing import assert_allclose

from grackle_nn.optim import SGD, Adam

if TYPE_CHECKING:
    from grackle_nn._types import Array


def test_sgd_vanilla_two_step_exact() -> None:
    p = np.array([1.0])
    g = np.array([0.5])
    grads = [g]
    sgd = SGD([p], grads, lr=0.1, momentum=0.0)

    grads[0][...] = 0.5
    sgd.step()
    grads[0][...] = 0.5
    sgd.step()

    assert_allclose(p[0], 0.9, rtol=1e-12)


def test_sgd_momentum_two_step_exact_algebra() -> None:
    p = np.array([1.0])
    g = np.array([0.5])
    grads = [g]
    sgd = SGD([p], grads, lr=0.1, momentum=0.9)

    grads[0][...] = 0.5
    sgd.step()
    assert_allclose(p[0], 0.95, rtol=1e-12)

    grads[0][...] = 0.5
    sgd.step()
    assert_allclose(p[0], 0.855, rtol=1e-12)


def test_adam_two_step_matches_reference() -> None:
    p = np.array([1.0])
    g = np.array([0.5])
    grads = [g]
    adam = Adam([p], grads)

    delta = 0.001 * 0.5 / (0.5 + 1e-8)
    expected_p1 = 1.0 - delta
    expected_p2 = expected_p1 - delta

    grads[0][...] = 0.5
    adam.step()
    assert_allclose(p[0], expected_p1, rtol=1e-12)

    grads[0][...] = 0.5
    adam.step()
    assert_allclose(p[0], expected_p2, rtol=1e-12)


def _adam_reference(
    params0: list[Array],
    grad_seq: list[list[Array]],
    *,
    lr: float,
    beta1: float,
    beta2: float,
    eps: float,
) -> list[list[Array]]:
    """Adam as written in Kingma & Ba (2015), Algorithm 1, kept separate from
    ``grackle_nn.optim``: fresh arrays each step, no in-place updates.

    Returns the parameter values after every step (``result[t - 1]`` is the
    state after step ``t``).
    """
    theta = [p.copy() for p in params0]
    m = [np.zeros_like(p) for p in params0]
    v = [np.zeros_like(p) for p in params0]
    trajectory: list[list[Array]] = []
    for t, grads in enumerate(grad_seq, start=1):
        for i, g in enumerate(grads):
            m[i] = beta1 * m[i] + (1.0 - beta1) * g
            v[i] = beta2 * v[i] + (1.0 - beta2) * (g * g)
            m_hat = m[i] / (1.0 - beta1**t)
            v_hat = v[i] / (1.0 - beta2**t)
            theta[i] = theta[i] - lr * m_hat / (np.sqrt(v_hat) + eps)
        trajectory.append([th.copy() for th in theta])
    return trajectory


# Per-element magnitudes spanning 1 .. 1e-5. The small ones matter: with
# |g| ~ 1e-5, sqrt(v_hat) is comparable to sqrt(eps), so where eps sits in
# the denominator is visible in the parameter update.
_ADAM_MAGNITUDES = np.array([[1.0, 3e-1, 1e-2], [1e-3, 1e-4, 1e-5]])
_ADAM_STEPS = 15


def _adam_params0() -> list[Array]:
    # Two parameter arrays of different shapes, as in a real model. The
    # optimizer's step counter must advance once per step, not once per array.
    return [np.array([[1.0, -2.0, 0.5], [3.0, -1.5, 2.5]]), np.array([0.75, -0.25])]


def _adam_grad_seq() -> list[list[Array]]:
    """A deterministic gradient sequence that is non-constant in both sign and magnitude.

    A constant gradient makes bias correction cancel exactly (m_hat == g and
    v_hat == g**2 for any betas), which is why the constant-gradient test
    above cannot tell swapped betas or a misplaced eps from correct Adam.
    """
    seq: list[list[Array]] = []
    for t in range(1, _ADAM_STEPS + 1):
        sign = -1.0 if t % 3 == 0 else 1.0  # flips on a period that isn't even/odd
        wobble = 1.0 + 0.5 * np.sin(t)  # varying magnitude, always positive
        g0 = sign * wobble * _ADAM_MAGNITUDES
        g1 = np.array([np.cos(0.7 * t), 1.0 - 0.2 * t])  # second entry crosses zero
        seq.append([g0, g1])
    return seq


@pytest.mark.parametrize(
    "hyper",
    [
        # train_heat_model's defaults (it passes only lr, and lr=1e-3 is the default).
        {"lr": 1e-3, "beta1": 0.9, "beta2": 0.999, "eps": 1e-8},
        # Non-default values, so every constructor kwarg is proven to be honored.
        {"lr": 5e-2, "beta1": 0.8, "beta2": 0.95, "eps": 1e-6},
    ],
    ids=["defaults", "custom"],
)
def test_adam_matches_reference_on_varying_gradients(hyper: dict[str, float]) -> None:
    """Test campaign T4-5 / T9-3 (docs/test-campaigns/phase-12.md): Adam checked
    step by step against an independent reference under a gradient sequence that
    flips sign and varies in magnitude, across two parameter arrays. Adam is the
    optimizer ``train_heat_model`` ships with."""
    params = _adam_params0()
    grads = [np.zeros_like(p) for p in params]
    adam = Adam(params, grads, **hyper)
    grad_seq = _adam_grad_seq()
    expected = _adam_reference(_adam_params0(), grad_seq, **hyper)

    for step, (step_grads, step_expected) in enumerate(zip(grad_seq, expected, strict=True), 1):
        for g_slot, g_value in zip(grads, step_grads, strict=True):
            g_slot[...] = g_value  # written in place, the way Sequential.backward does
        adam.step()
        for p, p_expected in zip(params, step_expected, strict=True):
            assert_allclose(p, p_expected, rtol=1e-12, atol=0, err_msg=f"step {step}")


def test_adam_varying_gradient_sequence_can_discriminate_betas() -> None:
    """Guards the test above against being simplified into the vacuous form: under
    this gradient sequence, swapping beta1/beta2 in the reference must move the
    trajectory by far more than that test's rtol. Under a constant gradient the
    two trajectories are identical."""
    correct_betas = {"lr": 1e-3, "beta1": 0.9, "beta2": 0.999, "eps": 1e-8}
    swapped_betas = {"lr": 1e-3, "beta1": 0.999, "beta2": 0.9, "eps": 1e-8}

    grad_seq = _adam_grad_seq()
    correct = _adam_reference(_adam_params0(), grad_seq, **correct_betas)
    swapped = _adam_reference(_adam_params0(), grad_seq, **swapped_betas)
    rel = max(
        float(np.max(np.abs(a - b) / np.abs(a)))
        for a, b in zip(correct[-1], swapped[-1], strict=True)
    )
    assert rel > 1e-6

    constant = [[np.full_like(g, 0.5) for g in grads] for grads in grad_seq]
    c_correct = _adam_reference(_adam_params0(), constant, **correct_betas)
    c_swapped = _adam_reference(_adam_params0(), constant, **swapped_betas)
    for a, b in zip(c_correct[-1], c_swapped[-1], strict=True):
        assert_allclose(a, b, rtol=1e-12, atol=0)


def test_inplace_invariant() -> None:
    sgd_params = [np.array([1.0, 2.0]), np.array([3.0])]
    sgd_grads = [np.array([0.1, 0.1]), np.array([0.1])]
    w = sgd_params[0]
    sgd = SGD(sgd_params, sgd_grads, lr=0.1, momentum=0.9)
    sgd_param_ids = [id(param) for param in sgd.params]
    sgd_velocity_ids = [id(v) for v in sgd.velocities]

    sgd_grads[0][...] = 0.2
    sgd_grads[1][...] = 0.2
    sgd.step()
    sgd_grads[0][...] = 0.3
    sgd_grads[1][...] = 0.3
    sgd.step()

    assert [id(param) for param in sgd.params] == sgd_param_ids
    assert [id(v) for v in sgd.velocities] == sgd_velocity_ids
    assert w is sgd.params[0]
    assert not np.allclose(w, [1.0, 2.0])

    adam_params = [np.array([1.0, 2.0]), np.array([3.0])]
    adam_grads = [np.array([0.1, 0.1]), np.array([0.1])]
    w2 = adam_params[0]
    adam = Adam(adam_params, adam_grads)
    adam_param_ids = [id(param) for param in adam.params]
    adam_m_ids = [id(m) for m in adam.m]
    adam_v_ids = [id(v) for v in adam.v]

    adam_grads[0][...] = 0.2
    adam_grads[1][...] = 0.2
    adam.step()
    adam_grads[0][...] = 0.3
    adam_grads[1][...] = 0.3
    adam.step()

    assert [id(param) for param in adam.params] == adam_param_ids
    assert [id(m) for m in adam.m] == adam_m_ids
    assert [id(v) for v in adam.v] == adam_v_ids
    assert w2 is adam.params[0]
    assert not np.allclose(w2, [1.0, 2.0])
