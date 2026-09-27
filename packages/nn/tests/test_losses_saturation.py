"""SoftmaxCrossEntropy.backward() at saturating logits (campaign T9-2).

``test_losses.py`` checks the forward pass at ``|logits| ~ 1e4`` for
finiteness only, and the gradcheck in ``test_gradcheck.py`` runs at
``standard_normal`` magnitude. Nothing exercised ``backward()`` where the
softmax saturates, and finite differences cannot: at 1e4 a probe step either
changes nothing or flips a probability from 0 to 1.

The oracle here is the analytic gradient ``(softmax(logits) - onehot) / B``,
evaluated in 60-digit ``decimal`` arithmetic from the exact float inputs, so it
shares no code and no rounding with the implementation under test (which
shifts by the row max in float64).

Tolerance. Each float64 probability can be off by about
``(|shift| + K + 3) * eps`` relative: the shift ``l - max`` rounds when the
operands are not within a factor of two (``|shift| * eps``), ``exp`` and the
division are within an ulp or two, and the K-term sum adds up to ``K * eps``.
Any shift past about -745 underflows to 0 on both sides, so the relative
error stays below ``750 * eps ~ 1.7e-13``; ``rtol=1e-12`` covers it. The label
entry ``p - 1`` cancels when ``p`` is near 1, which leaves the absolute error
of ``p`` itself, up to ``(K + 2) * eps`` (``1.8e-15`` at K=6), and subnormal
probabilities lose relative precision but not absolute; ``atol=4e-15`` covers
both for up to 16 classes. Measured on macOS/arm64 over 3000 random saturating
batches: the worst non-label relative error was 5.7e-14 and the worst label
absolute error 4.2e-16, so the band has 17x and 9x headroom for other
platforms' ``exp``. A wrong gradient at this scale is off by something of order
``1 / B``, far outside either band.
"""

from __future__ import annotations

import decimal
import math
from typing import TYPE_CHECKING

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from numpy.testing import assert_allclose, assert_array_equal

from grackle_nn.losses import SoftmaxCrossEntropy

if TYPE_CHECKING:
    from grackle_nn._types import Array, IntArray

_RTOL = 1e-12
_ATOL = 4e-15
_CTX = decimal.Context(prec=60, Emin=-(10**9), Emax=10**9)


def _oracle_grad(logits: Array, labels: IntArray) -> Array:
    """``(softmax(logits) - onehot) / B`` in 60-digit decimal, rounded once to float64."""
    batch, classes = logits.shape
    out = np.empty_like(logits)
    for i in range(batch):
        row = [decimal.Decimal(float(v)) for v in logits[i]]  # exact conversions
        top = max(row)
        exps = [_CTX.exp(_CTX.subtract(v, top)) for v in row]
        total = decimal.Decimal(0)
        for e in exps:  # _CTX.add, not sum(): sum() would round at the ambient 28 digits
            total = _CTX.add(total, e)
        for j in range(classes):
            p = _CTX.divide(exps[j], total)
            if j == int(labels[i]):
                p = _CTX.subtract(p, decimal.Decimal(1))
            out[i, j] = float(_CTX.divide(p, decimal.Decimal(batch)))
    return out


def _backward(logits: Array, labels: IntArray) -> Array:
    loss_fn = SoftmaxCrossEntropy()
    loss = loss_fn.forward(logits, labels)
    assert math.isfinite(loss), f"forward loss not finite: {loss}"
    return loss_fn.backward()


def _check(logits: Array, labels: IntArray) -> Array:
    grad = _backward(logits, labels)
    assert grad.shape == logits.shape
    assert np.isfinite(grad).all(), f"non-finite gradient:\n{grad}"
    assert_allclose(grad, _oracle_grad(logits, labels), rtol=_RTOL, atol=_ATOL)
    # Each row of (p - onehot) sums to zero: the probabilities sum to 1.
    assert_allclose(grad.sum(axis=1), 0.0, rtol=0, atol=8 * _ATOL)
    return grad


def test_oracle_matches_hand_values() -> None:
    # Sanity for the oracle itself, on the input test_losses.py pins exactly.
    logits = np.zeros((2, 2))
    labels = np.array([0, 1], dtype=np.int64)
    assert_array_equal(_oracle_grad(logits, labels), [[-0.25, 0.25], [0.25, -0.25]])


def test_saturated_correct_class_has_exactly_zero_gradient() -> None:
    # p = [1, exp(-2e4), exp(-1e4)] = [1.0, 0.0, 0.0] in float64, so p - onehot
    # is exactly zero: a confidently right prediction pushes nothing.
    grad = _check(np.array([[1e4, -1e4, 0.0]]), np.array([0], dtype=np.int64))
    assert_array_equal(grad, [[0.0, 0.0, 0.0]])


def test_saturated_wrong_class_has_exactly_unit_gradient() -> None:
    # Label 1 while class 0 holds all the mass: the gradient is +1 on the
    # winner and -1 on the label, bounded -- not inf, not nan.
    grad = _check(np.array([[1e4, -1e4, 0.0]]), np.array([1], dtype=np.int64))
    assert_array_equal(grad, [[1.0, -1.0, 0.0]])


def test_tied_maxima_at_large_magnitude_split_the_mass() -> None:
    grad = _check(np.array([[1e4, 1e4, -1e4]]), np.array([0], dtype=np.int64))
    assert_array_equal(grad, [[-0.5, 0.5, 0.0]])


def test_all_equal_row_at_large_magnitude_is_uniform() -> None:
    grad = _check(np.full((1, 4), -1e4), np.array([2], dtype=np.int64))
    assert_allclose(grad, [[0.25, 0.25, -0.75, 0.25]], rtol=_RTOL, atol=_ATOL)


def test_rows_at_different_scales_are_shifted_independently() -> None:
    # Each row must be shifted by its OWN max. Row 1 sits 2e4 below row 0's
    # max: shifted by a batch-wide max, every exp in it underflows to 0 and
    # the row becomes 0/0.
    logits = np.array(
        [
            [1e4, -1e4, 0.0],
            [-1e4, -1e4 + 1.0, -1e4 + 2.0],
            [1e2, -1e2, 50.0],
            [-1e2, 3e3, 3e3 - 0.5],
        ]
    )
    labels = np.array([1, 2, 0, 2], dtype=np.int64)
    _check(logits, labels)


def test_near_ties_at_large_magnitude_keep_their_ratio() -> None:
    # Non-trivial probabilities at 1e4: the shift is exact (Sterbenz) and the
    # softmax of [0, -1, -3, -700] must come through intact, including the
    # 1e-304-scale entry at the bottom of the normal range.
    logits = np.array([[1e4, 1e4 - 1.0, 1e4 - 3.0, 1e4 - 700.0]])
    grad = _check(logits, np.array([1], dtype=np.int64))
    assert 0.0 < grad[0, 3] < 1e-300


@pytest.mark.parametrize("magnitude", [1e2, 1e3, 1e4])
def test_mixed_sign_batch_at_each_magnitude(magnitude: float) -> None:
    rng = np.random.default_rng(int(magnitude))
    logits = rng.choice([-1.0, 1.0], size=(6, 5)) * magnitude * rng.uniform(0.5, 1.0, (6, 5))
    logits[1, 2] = logits[1, 4] = logits[1].max()  # a tie for the row max
    labels = rng.integers(0, 5, size=6)
    _check(logits, labels)


_magnitude = st.floats(min_value=1e2, max_value=1e4)
_signed = st.builds(lambda m, neg: -m if neg else m, _magnitude, st.booleans())
# An offset from a row's base: 0 (exact ties), small (non-trivial probabilities
# at large magnitude), or huge (underflow).
_offset = st.one_of(
    st.just(0.0),
    st.floats(min_value=-50.0, max_value=50.0),
    st.floats(min_value=-2e4, max_value=2e4),
)


def _around(base: float) -> st.SearchStrategy[float]:
    return _offset.map(lambda d: base + d)


@st.composite
def _saturating_batch(draw: st.DrawFn) -> tuple[Array, IntArray]:
    batch = draw(st.integers(1, 5))
    classes = draw(st.integers(2, 6))
    rows = []
    for _ in range(batch):
        # Entries are either independent large values of either sign, or the
        # row's base plus an offset.
        entry = st.one_of(_signed, _around(draw(_signed)))
        rows.append(draw(st.lists(entry, min_size=classes, max_size=classes)))
    labels = draw(st.lists(st.integers(0, classes - 1), min_size=batch, max_size=batch))
    return np.array(rows, dtype=np.float64), np.array(labels, dtype=np.int64)


@given(_saturating_batch())
def test_backward_matches_analytic_oracle_at_saturation(batch: tuple[Array, IntArray]) -> None:
    logits, labels = batch
    _check(logits, labels)
