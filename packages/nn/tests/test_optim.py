import numpy as np
from numpy.testing import assert_allclose

from grackle_nn.optim import SGD, Adam


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
