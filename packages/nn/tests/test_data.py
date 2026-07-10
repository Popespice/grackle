import numpy as np
from numpy.testing import assert_array_equal

from grackle_nn.data import make_spirals


def test_shapes_and_dtypes() -> None:
    X, y = make_spirals(rng=np.random.default_rng(0))
    assert X.shape == (384, 2)
    assert X.dtype == np.float64
    assert y.shape == (384,)
    assert y.dtype == np.int64

    X2, y2 = make_spirals(n_per_class=10, classes=5, rng=np.random.default_rng(0))
    assert X2.shape == (50, 2)
    assert y2.shape == (50,)


def test_deterministic_per_seed() -> None:
    X_a, y_a = make_spirals(rng=np.random.default_rng(42))
    X_b, y_b = make_spirals(rng=np.random.default_rng(42))
    assert_array_equal(X_a, X_b)
    assert_array_equal(y_a, y_b)

    X_c, _ = make_spirals(rng=np.random.default_rng(43))
    assert not np.array_equal(X_a, X_c)


def test_class_balance_and_row_blocking() -> None:
    _, y = make_spirals(n_per_class=20, classes=4, rng=np.random.default_rng(7))

    for k in range(4):
        assert (y == k).sum() == 20

    assert_array_equal(y[:20], np.full(20, 0, dtype=np.int64))
    assert_array_equal(y[20:40], np.full(20, 1, dtype=np.int64))
    assert_array_equal(y[40:60], np.full(20, 2, dtype=np.int64))
    assert_array_equal(y[60:80], np.full(20, 3, dtype=np.int64))
