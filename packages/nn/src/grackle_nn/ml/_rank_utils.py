"""Shared tie-averaged ranking primitive for features.py and metrics_rank.py.

Both modules need "average rank within a tie group, via a stable sort" —
this is the one place that logic is implemented, so the two call sites
(``features.py``'s percentile and ``metrics_rank.py``'s Spearman ranks)
can't silently drift apart. Deliberately does not import from either
sibling module — this is a leaf utility, not a coupling point between two
modules the build order keeps independent. Never imports ``grackle`` at
runtime, not even under ``TYPE_CHECKING``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from grackle_nn._types import Array


def tie_averaged_ranks_0indexed(values: Array) -> Array:
    """0-indexed average rank per value; tied values share their group's mean rank.

    Stable argsort makes tie-group membership (and therefore the averaged
    rank) independent of input order. Returns an all-zero array for an
    empty input (nothing to rank).
    """
    n = values.shape[0]
    ranks = np.zeros(n, dtype=np.float64)
    if n == 0:
        return ranks
    order = np.argsort(values, kind="stable")
    sorted_vals = values[order]
    i = 0
    while i < n:
        j = i
        while j + 1 < n and sorted_vals[j + 1] == sorted_vals[i]:
            j += 1
        avg_rank = (i + j) / 2.0
        ranks[order[i : j + 1]] = avg_rank
        i = j + 1
    return ranks
