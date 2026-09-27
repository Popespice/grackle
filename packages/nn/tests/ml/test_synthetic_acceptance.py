"""THE acceptance test (Phase 12.1, block M6): the model beats a raw-in-degree
baseline on held-out synthetic graphs.

Margins were measured locally before picking the documented bar; per the
plan's explicit escape hatch, a cross-OS CI flake here should lower the bar,
never unseed the corpus/split/train calls (that would just be reshuffling
the dice until it happens to pass).

The pipeline itself lives in ``acceptance_eval.py`` so that the campaign T9-1
margin sweep (``scripts/margin_sweep.py``) measures exactly this computation
at other seeds. The seeds here (split 0, train 0) and the bars below are the
ADR-0029 ones, unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from acceptance_eval import VAL_COUNT, baseline_scores, build_corpus, evaluate

from grackle_nn.ml.dataset import split_by_graph
from grackle_nn.ml.features import FEATURE_NAMES
from grackle_nn.ml.heat_model import train_heat_model
from grackle_nn.ml.metrics_rank import spearman

if TYPE_CHECKING:
    from collections.abc import Callable

_MARGIN_BAR = 0.05
_ABSOLUTE_BAR = 0.5


def test_baseline_sanity_not_degenerate() -> None:
    # The in-degree baseline must itself be positively correlated with heat
    # (the generator is not degenerate) -- otherwise "beat the baseline"
    # would be a meaningless bar.
    examples = build_corpus()
    for score in baseline_scores(examples):
        assert score > 0.0


def test_model_beats_degree_baseline_on_held_out_graphs(
    record_property: Callable[[str, object], None],
) -> None:
    run = evaluate(build_corpus(), split_seed=0, train_seed=0)

    # Campaign T9-1: the passing path reports how much headroom it had, and
    # the top-10 overlap that used to be computed and thrown away. The bar
    # sits at +0.05 over a single-point margin of about +0.056, so a quiet pass
    # says nothing about how close the next change comes to failing it.
    # record_property lands in --junitxml; the print shows under -rA / -rP.
    telemetry = (
        f"margin={run.margin:+.4f} (bar {_MARGIN_BAR:+.2f}, "
        f"headroom {run.margin - _MARGIN_BAR:+.4f}) "
        f"mean_model={run.mean_model:.4f} (bar >{_ABSOLUTE_BAR}) "
        f"mean_baseline={run.mean_baseline:.4f} "
        f"top10={list(run.top10)} val={','.join(run.val_names)}"
    )
    record_property("acceptance_margin", run.margin)
    record_property("acceptance_headroom", run.margin - _MARGIN_BAR)
    record_property("acceptance_mean_model", run.mean_model)
    record_property("acceptance_mean_baseline", run.mean_baseline)
    record_property("acceptance_top10", list(run.top10))
    print(f"T9-1 acceptance telemetry: {telemetry}")

    assert run.mean_model >= run.mean_baseline + _MARGIN_BAR, telemetry
    assert run.mean_model > _ABSOLUTE_BAR, telemetry


def test_acceptance_bar_is_discriminating_against_shuffled_labels() -> None:
    """Mutation check: shuffling a held-out graph's heat must break the bar.

    Proves the two assertions above are actually discriminating (the model
    is reading real structure-heat correlation), not vacuously satisfied.
    """
    examples = build_corpus()
    train, val = split_by_graph(examples, val_count=VAL_COUNT, rng=np.random.default_rng(0))
    model, _ = train_heat_model(train, epochs=300, seed=0)

    shuffle_rng = np.random.default_rng(99)
    model_scores = []
    baseline_scores_ = []
    for ex in val:
        shuffled_counts = shuffle_rng.permutation(ex.counts.astype(np.float64))
        pred = model.predict(ex.x)
        model_scores.append(spearman(pred, shuffled_counts))
        in_degree = np.expm1(ex.x[:, FEATURE_NAMES.index("log1p_in_degree")])
        baseline_scores_.append(spearman(in_degree, shuffled_counts))

    mean_model = float(np.mean(model_scores))
    mean_baseline = float(np.mean(baseline_scores_))
    assert not (mean_model >= mean_baseline + _MARGIN_BAR and mean_model > _ABSOLUTE_BAR)
