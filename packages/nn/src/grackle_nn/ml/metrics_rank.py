"""Rank-correlation metrics for the hotspot-prediction model, pure numpy (no scipy).

Used to compare a model's predicted heat ranking against ground-truth trace
heat (D12.4): ``spearman`` for overall rank agreement, ``top_k_overlap`` for
"did it find the hottest nodes" at a fixed budget. This module never imports
``grackle`` at runtime, not even under ``TYPE_CHECKING``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from grackle_nn.ml._rank_utils import tie_averaged_ranks_0indexed

if TYPE_CHECKING:
    from grackle_nn._types import Array


def _ranks(values: Array) -> Array:
    """1-indexed ranks with tied values sharing their group's average rank."""
    result: Array = tie_averaged_ranks_0indexed(values) + 1.0
    return result


def spearman(a: Array, b: Array) -> float:
    """Spearman rank correlation (Pearson correlation of the two rank arrays).

    A constant input (every value tied, so every rank is identical, so the
    array's rank-variance is zero) makes the correlation undefined; this is
    documented as returning 0.0 rather than NaN — as does an empty input
    (nothing to correlate), which numpy would otherwise turn into NaN with a
    handful of RuntimeWarnings.
    """
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    if a.shape[0] == 0:
        return 0.0
    ra = _ranks(a)
    rb = _ranks(b)
    if np.std(ra) == 0.0 or np.std(rb) == 0.0:
        return 0.0
    return float(np.corrcoef(ra, rb)[0, 1])


def top_k_overlap(a: Array, b: Array, k: int) -> float:
    """Fraction of the top-``k`` (by *a*) that also appears in the top-``k`` (by *b*).

    Ties broken by a stable descending argsort (lower original index wins),
    so results are deterministic regardless of tie order in the input.
    ``k`` is clamped to the array length; ``k <= 0`` raises ``ValueError``.
    """
    if k <= 0:
        raise ValueError(f"k must be positive, got {k}")
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    n = a.shape[0]
    k_eff = min(k, n)
    if k_eff == 0:
        return 0.0
    top_a = set(np.argsort(-a, kind="stable")[:k_eff].tolist())
    top_b = set(np.argsort(-b, kind="stable")[:k_eff].tolist())
    return float(len(top_a & top_b) / k_eff)
