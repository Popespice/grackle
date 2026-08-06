"""THE acceptance test (Phase 12.1, block M6): the model beats a raw-in-degree
baseline on held-out synthetic graphs.

Margins were measured locally before picking the documented bar; per the
plan's explicit escape hatch, a cross-OS CI flake here should lower the bar,
never unseed the corpus/split/train calls (that would just be reshuffling
the dice until it happens to pass).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from synth import make_synthetic_pair

from grackle_nn.ml.dataset import build_example, split_by_graph
from grackle_nn.ml.features import FEATURE_NAMES
from grackle_nn.ml.heat_model import train_heat_model
from grackle_nn.ml.metrics_rank import spearman, top_k_overlap

if TYPE_CHECKING:
    from grackle_nn.ml.dataset import Example

_MARGIN_BAR = 0.05
_ABSOLUTE_BAR = 0.5


def _corpus() -> list[Example]:
    examples = []
    for seed in range(8):
        graph, heat = make_synthetic_pair(seed)
        examples.append(build_example(f"synth{seed}", graph, heat))
    return examples


def _baseline_scores(val: list[Example]) -> list[float]:
    scores = []
    for ex in val:
        in_degree = np.expm1(ex.x[:, FEATURE_NAMES.index("log1p_in_degree")])
        counts = ex.counts.astype(np.float64)
        scores.append(spearman(in_degree, counts))
    return scores


def test_baseline_sanity_not_degenerate() -> None:
    # The in-degree baseline must itself be positively correlated with heat
    # (the generator is not degenerate) -- otherwise "beat the baseline"
    # would be a meaningless bar.
    examples = _corpus()
    for score in _baseline_scores(examples):
        assert score > 0.0


def test_model_beats_degree_baseline_on_held_out_graphs() -> None:
    examples = _corpus()
    train, val = split_by_graph(examples, val_count=2, rng=np.random.default_rng(0))
    model, _ = train_heat_model(train, epochs=300, seed=0)

    model_scores = []
    top10 = []
    for ex in val:
        pred = model.predict(ex.x)
        counts = ex.counts.astype(np.float64)
        model_scores.append(spearman(pred, counts))
        top10.append(top_k_overlap(pred, counts, 10))
    baseline_scores = _baseline_scores(val)

    mean_model = float(np.mean(model_scores))
    mean_baseline = float(np.mean(baseline_scores))
    assert mean_model >= mean_baseline + _MARGIN_BAR
    assert mean_model > _ABSOLUTE_BAR


def test_acceptance_bar_is_discriminating_against_shuffled_labels() -> None:
    """Mutation check: shuffling a held-out graph's heat must break the bar.

    Proves the two assertions above are actually discriminating (the model
    is reading real structure-heat correlation), not vacuously satisfied.
    """
    examples = _corpus()
    train, val = split_by_graph(examples, val_count=2, rng=np.random.default_rng(0))
    model, _ = train_heat_model(train, epochs=300, seed=0)

    shuffle_rng = np.random.default_rng(99)
    model_scores = []
    baseline_scores = []
    for ex in val:
        shuffled_counts = shuffle_rng.permutation(ex.counts.astype(np.float64))
        pred = model.predict(ex.x)
        model_scores.append(spearman(pred, shuffled_counts))
        in_degree = np.expm1(ex.x[:, FEATURE_NAMES.index("log1p_in_degree")])
        baseline_scores.append(spearman(in_degree, shuffled_counts))

    mean_model = float(np.mean(model_scores))
    mean_baseline = float(np.mean(baseline_scores))
    assert not (mean_model >= mean_baseline + _MARGIN_BAR and mean_model > _ABSOLUTE_BAR)
