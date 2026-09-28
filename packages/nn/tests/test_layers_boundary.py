"""ReLU and Tanh at their boundaries: the kink, saturation, and non-finite inputs.

Campaign T9-4 (docs/test-campaigns/phase-12.md). The gradchecks in
``test_gradcheck.py`` deliberately keep inputs away from ReLU's kink (a central
difference straddling 0 measures neither one-sided derivative), and nothing
else covered it, nor Tanh's saturated tail, nor ±inf / nan. This file pins what
the layers actually do there.

Pinned conventions:

- **ReLU's subgradient at exactly 0 is 0** (``x > 0.0``, strict). A dead-at-zero
  unit passes no gradient; ``-0.0`` behaves like ``0.0``.
- ReLU passes +inf through, and propagates nan forward. Backward sends 0 to
  every unit whose input was not strictly positive, including a nan input.
- **Tanh saturates to exactly ±1.0**, with a local derivative of exactly 0.0,
  for ``|x| >= 22`` (every libm special-cases that range; macOS/arm64 numpy
  reaches exactly 1.0 from ``x ~ 18.99``). ``tanh(±inf) = ±1`` exactly (C99
  Annex F) and ``tanh(nan) = nan``.

One confirmed defect is ledgered (strict xfail, T9-4): ReLU is written as a
multiplicative mask, ``x * (x > 0)``, so an infinity in a masked-off position
becomes ``inf * 0 = nan`` instead of 0 — ``ReLU(-inf)`` is nan, and an infinite
upstream gradient at an inactive unit comes back nan. Latent: a float64
activation in this MLP only reaches ±inf after training has already diverged.
Incidental and not pinned: the mask idiom also returns ``-0.0`` (not ``+0.0``)
for negative inputs, which compares equal to 0 and shows as ``-0.`` in the
value inspector's array reprs.
"""

from __future__ import annotations

import math
import warnings
from typing import TYPE_CHECKING

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from numpy.testing import assert_array_equal

from grackle_nn.layers import ReLU, Tanh

if TYPE_CHECKING:
    from grackle_nn._types import Array

_T94 = "T9-4: ReLU's multiplicative mask turns inf into nan (docs/test-campaigns/phase-12.md)"


def _relu(x: list[float]) -> tuple[ReLU, Array]:
    layer = ReLU()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # inf * 0 in the ledgered case
        out = layer.forward(np.array([x], dtype=np.float64))
    return layer, out


# --- ReLU: the kink -----------------------------------------------------------


def test_relu_subgradient_at_zero_is_zero() -> None:
    layer, out = _relu([0.0, -0.0, 1.0, -1.0])
    assert_array_equal(out, [[0.0, 0.0, 1.0, 0.0]])
    dx = layer.backward(np.array([[7.0, 7.0, 7.0, 7.0]]))
    assert_array_equal(dx, [[0.0, 0.0, 7.0, 0.0]])


def test_relu_kink_is_strict_at_the_smallest_representable_steps() -> None:
    # The boundary is exactly 0: the smallest subnormal above it is active, and
    # its negation is not. Guards against a threshold or epsilon creeping in.
    tiny = math.ulp(0.0)  # 5e-324
    layer, out = _relu([tiny, -tiny, 0.0])
    assert_array_equal(out, [[tiny, 0.0, 0.0]])
    assert_array_equal(layer.backward(np.ones((1, 3))), [[1.0, 0.0, 0.0]])


# --- ReLU: non-finite inputs ----------------------------------------------------


def test_relu_passes_positive_infinity_through() -> None:
    layer, out = _relu([math.inf])
    assert_array_equal(out, [[math.inf]])
    assert_array_equal(layer.backward(np.array([[3.0]])), [[3.0]])


def test_relu_propagates_nan_forward_and_blocks_its_gradient() -> None:
    # nan > 0 is False, so the unit is treated as inactive in backward, while
    # the forward value stays nan: a nan activation is never silently hidden.
    layer, out = _relu([math.nan, 1.0])
    assert math.isnan(out[0, 0])
    assert out[0, 1] == 1.0
    assert_array_equal(layer.backward(np.array([[5.0, 5.0]])), [[0.0, 5.0]])


def test_relu_gradient_at_negative_infinity_is_zero() -> None:
    layer, _ = _relu([-math.inf])
    assert_array_equal(layer.backward(np.array([[4.0]])), [[0.0]])


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_T94)
def test_relu_of_negative_infinity_is_zero() -> None:
    _, out = _relu([-math.inf])
    assert_array_equal(out, [[0.0]])  # observed: nan


@pytest.mark.xfail(strict=True, raises=AssertionError, reason=_T94)
def test_relu_blocks_an_infinite_gradient_at_an_inactive_unit() -> None:
    layer, _ = _relu([-1.0, 0.0])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        dx = layer.backward(np.array([[math.inf, -math.inf]]))
    assert_array_equal(dx, [[0.0, 0.0]])  # observed: [[nan, nan]]


@given(st.floats(allow_nan=False, allow_infinity=False))
def test_relu_matches_max_zero_on_every_finite_input(v: float) -> None:
    layer, out = _relu([v])
    assert out[0, 0] == max(v, 0.0)
    assert layer.backward(np.array([[2.0]]))[0, 0] == (2.0 if v > 0.0 else 0.0)


# --- Tanh: saturation and non-finite inputs ------------------------------------


def _tanh(x: list[float]) -> tuple[Tanh, Array]:
    layer = Tanh()
    return layer, layer.forward(np.array([x], dtype=np.float64))


def test_tanh_at_zero_is_identity_slope() -> None:
    layer, out = _tanh([0.0, -0.0])
    assert_array_equal(out, [[0.0, 0.0]])
    assert math.copysign(1.0, out[0, 1]) == -1.0  # tanh is odd: tanh(-0) = -0
    assert_array_equal(layer.backward(np.array([[3.0, 3.0]])), [[3.0, 3.0]])


@pytest.mark.parametrize("magnitude", [22.0, 25.0, 50.0, 710.0, 1e300, math.inf])
def test_tanh_saturates_to_exactly_one_with_zero_slope(magnitude: float) -> None:
    layer, out = _tanh([magnitude, -magnitude])
    assert_array_equal(out, [[1.0, -1.0]])
    assert_array_equal(layer.backward(np.array([[5.0, -5.0]])), [[0.0, 0.0]])


def test_tanh_nan_propagates_both_ways() -> None:
    layer, out = _tanh([math.nan])
    assert math.isnan(out[0, 0])
    assert math.isnan(layer.backward(np.array([[1.0]]))[0, 0])


@given(st.floats(allow_nan=False))
def test_tanh_is_bounded_sign_preserving_and_its_slope_is_in_unit_interval(v: float) -> None:
    layer, out = _tanh([v])
    t = out[0, 0]
    assert -1.0 <= t <= 1.0
    assert math.copysign(1.0, t) == math.copysign(1.0, v)
    slope = layer.backward(np.array([[1.0]]))[0, 0]
    assert 0.0 <= slope <= 1.0
    if abs(v) >= 22.0:
        assert abs(t) == 1.0
        assert slope == 0.0
