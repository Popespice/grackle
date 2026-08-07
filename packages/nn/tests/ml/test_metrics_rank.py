"""Tests for grackle_nn.ml.metrics_rank (Phase 12.1, block M4)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from grackle_nn.ml.metrics_rank import _ranks, spearman, top_k_overlap


def test_ranks_hand_computed_tie_case() -> None:
    # b=[2,2,3]: the two 2s tie at 1-indexed ranks 1 and 2 -> average 1.5;
    # the 3 is unambiguously rank 3.
    r = _ranks(np.array([2.0, 2.0, 3.0]))
    assert r.tolist() == [1.5, 1.5, 3.0]


def test_ranks_no_ties_are_plain_1_indexed_positions() -> None:
    r = _ranks(np.array([10.0, 20.0, 30.0]))
    assert r.tolist() == [1.0, 2.0, 3.0]


def test_spearman_monotone_is_one() -> None:
    a = np.array([1.0, 2.0, 3.0, 4.0])
    b = np.array([10.0, 20.0, 30.0, 40.0])
    assert spearman(a, b) == pytest.approx(1.0)


def test_spearman_reversed_is_negative_one() -> None:
    a = np.array([1.0, 2.0, 3.0, 4.0])
    b = np.array([4.0, 3.0, 2.0, 1.0])
    assert spearman(a, b) == pytest.approx(-1.0)


def test_spearman_hand_tie_case_sqrt3_over_2() -> None:
    a = np.array([1.0, 2.0, 3.0])
    b = np.array([2.0, 2.0, 3.0])
    assert spearman(a, b) == pytest.approx(0.8660254037844387, rel=1e-12)


def test_spearman_constant_input_is_zero() -> None:
    a = np.array([5.0, 5.0, 5.0])
    b = np.array([1.0, 2.0, 3.0])
    assert spearman(a, b) == 0.0
    assert spearman(b, a) == 0.0
    assert spearman(a, a) == 0.0


def test_spearman_return_type_is_builtin_float() -> None:
    r = spearman(np.array([1.0, 2.0]), np.array([2.0, 1.0]))
    assert type(r) is float


def test_top_k_overlap_full_agreement() -> None:
    a = np.array([5.0, 4.0, 3.0, 2.0, 1.0])
    b = np.array([5.0, 4.0, 3.0, 2.0, 1.0])
    assert top_k_overlap(a, b, 2) == 1.0


def test_top_k_overlap_no_agreement() -> None:
    a = np.array([1.0, 2.0, 3.0, 4.0])
    b = np.array([4.0, 3.0, 2.0, 1.0])
    assert top_k_overlap(a, b, 1) == 0.0


def test_top_k_overlap_partial() -> None:
    a = np.array([4.0, 3.0, 2.0, 1.0])  # top-2 by a: indices 0, 1
    b = np.array([1.0, 4.0, 3.0, 2.0])  # top-2 by b: indices 1, 2
    assert top_k_overlap(a, b, 2) == 0.5


def test_top_k_overlap_k_clamps_to_n() -> None:
    a = np.array([1.0, 2.0])
    b = np.array([2.0, 1.0])
    assert top_k_overlap(a, b, 100) == 1.0


def test_top_k_overlap_k_le_zero_raises() -> None:
    a = np.array([1.0, 2.0])
    with pytest.raises(ValueError, match="k must be positive"):
        top_k_overlap(a, a, 0)
    with pytest.raises(ValueError, match="k must be positive"):
        top_k_overlap(a, a, -1)


def test_top_k_overlap_tie_determinism() -> None:
    # All tied scores -> stable argsort always picks the same top-k
    # (lowest original index first) regardless of how many times it's called.
    a = np.array([1.0, 1.0, 1.0, 1.0])
    results = {tuple(sorted(np.argsort(-a, kind="stable")[:2].tolist())) for _ in range(5)}
    assert len(results) == 1
    assert top_k_overlap(a, a, 2) == 1.0


def test_spearman_empty_input_is_zero_not_nan() -> None:
    r = spearman(np.array([]), np.array([]))
    assert r == 0.0
    assert not math.isnan(r)
    assert type(r) is float


def test_top_k_overlap_empty_input_is_zero() -> None:
    assert top_k_overlap(np.array([]), np.array([]), 5) == 0.0
