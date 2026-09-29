"""The ADR-0029 acceptance test (Phase 12.1, block M6), evaluated k-fold.

ADR-0029 §6 asked for the learned model to beat a raw-in-degree baseline by
+0.05 Spearman on held-out synthetic graphs, measured on one split of two
graphs. Campaign finding F-12 showed that measurement was seed luck: over 200
seed pairs 91% of draws fell below the bar, and the test passed only because
split 0 / train 0 is one of the favorable ones. The owner decided to evaluate
k-fold instead (ADR-0029, "Amendment: k-fold acceptance"): every graph of the
8-graph corpus is held out exactly once (4 folds of 2), so the margin is a mean
over all 8 graphs, not 2.

What k-fold measures is that the model **matches** the baseline and does not
beat it. Over 100 (split, train) seed pairs the mean margin is +0.001, the
median 0.000, the range -0.052 to +0.039, and no draw reaches +0.05. So the
claim and the guard are tested separately:

- ``test_kfold_model_is_not_worse_than_degree_baseline`` (passing) is the
  regression guard: mean Spearman above 0.5, and a margin no lower than
  ``_MARGIN_FLOOR``. The floor sits just below the worst of those 100 draws, so
  it holds on every observed seed pair and is not a coin flip. It catches a
  model that has gotten worse than the baseline, not one that fails to beat it.
- ``test_kfold_model_beats_degree_baseline`` (strict xfail) is the ADR's actual
  claim, ledgered as F-12: it fails today, and goes green when the model or
  features improve. The fixing change removes the marker.

The seeds here (split 0, train 0) are the ADR-0029 ones and are never changed;
per the ADR's escape hatch a flake lowers a bar, it does not reshuffle the dice.
The pipeline lives in ``acceptance_eval.py`` so ``scripts/margin_sweep.py``
measures exactly this computation at other seeds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import acceptance_eval
import numpy as np
import pytest
from acceptance_eval import (
    EPOCHS,
    VAL_COUNT,
    baseline_scores,
    build_corpus,
    evaluate,
    evaluate_kfold,
)

from grackle_nn.ml.dataset import split_by_graph
from grackle_nn.ml.features import FEATURE_NAMES
from grackle_nn.ml.heat_model import train_heat_model
from grackle_nn.ml.metrics_rank import spearman

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from typing import Any

    from grackle_nn.ml.dataset import Example

# ADR-0029's claim: the model beats raw in-degree by this much.
_MARGIN_BAR = 0.05
# The regression guard: the model is no worse than in-degree by more than this.
# The worst k-fold margin over 100 seed pairs was -0.0517.
_MARGIN_FLOOR = -0.06
_ABSOLUTE_BAR = 0.5


def _guard_holds(mean_model: float, mean_baseline: float) -> bool:
    return mean_model >= mean_baseline + _MARGIN_FLOOR and mean_model > _ABSOLUTE_BAR


def test_baseline_sanity_not_degenerate() -> None:
    # The in-degree baseline must itself be positively correlated with heat
    # (the generator is not degenerate) -- otherwise "not worse than the
    # baseline" would be a meaningless bar.
    examples = build_corpus()
    for score in baseline_scores(examples):
        assert score > 0.0


def test_kfold_model_is_not_worse_than_degree_baseline(
    record_property: Callable[[str, object], None],
) -> None:
    run = evaluate_kfold(build_corpus(), split_seed=0, train_seed=0)

    # Campaign T9-1: report how much headroom the guard had, and the top-10
    # overlap, so a quiet pass says how close the next change comes to failing.
    # record_property lands in --junitxml; the print shows under -rA / -rP.
    telemetry = (
        f"margin={run.margin:+.4f} (floor {_MARGIN_FLOOR:+.2f}, "
        f"headroom {run.margin - _MARGIN_FLOOR:+.4f}; claim {_MARGIN_BAR:+.2f}) "
        f"mean_model={run.mean_model:.4f} (bar >{_ABSOLUTE_BAR}) "
        f"mean_baseline={run.mean_baseline:.4f} "
        f"top10={[round(t, 2) for t in run.top10]} held_out={','.join(run.val_names)}"
    )
    record_property("acceptance_margin", run.margin)
    record_property("acceptance_headroom", run.margin - _MARGIN_FLOOR)
    record_property("acceptance_mean_model", run.mean_model)
    record_property("acceptance_mean_baseline", run.mean_baseline)
    record_property("acceptance_top10", list(run.top10))
    print(f"T9-1 acceptance telemetry: {telemetry}")

    assert len(run.val_names) == len(set(run.val_names)) == len(build_corpus())
    assert run.mean_model >= run.mean_baseline + _MARGIN_FLOOR, telemetry
    assert run.mean_model > _ABSOLUTE_BAR, telemetry


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "F-12: evaluated k-fold over all 8 graphs, the model matches the raw-in-degree baseline "
        "(mean margin +0.001 over 100 seed pairs, none reaching +0.05) instead of beating it by "
        "the +0.05 ADR-0029 claims (docs/test-campaigns/phase-12.md)"
    ),
)
def test_kfold_model_beats_degree_baseline() -> None:
    run = evaluate_kfold(build_corpus(), split_seed=0, train_seed=0)
    assert run.mean_model >= run.mean_baseline + _MARGIN_BAR, (
        f"margin {run.margin:+.4f} < {_MARGIN_BAR:+.2f}"
    )


def test_kfold_holds_every_graph_out_exactly_once_and_never_trains_on_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The evaluation's own invariant: the corpus is partitioned, so no graph is
    scored by a model that saw it, and the mean is over every graph."""
    examples = build_corpus()
    corpus = sorted(ex.name for ex in examples)
    trained_on: list[list[str]] = []
    real_train = train_heat_model

    def recording_train(train: Sequence[Example], **kwargs: Any) -> Any:
        trained_on.append([ex.name for ex in train])
        return real_train(train, **kwargs)

    monkeypatch.setattr(acceptance_eval, "train_heat_model", recording_train)
    run = evaluate_kfold(examples, split_seed=3, train_seed=0, epochs=5)

    assert sorted(run.val_names) == corpus
    assert len(run.model_scores) == len(run.baseline_scores) == len(run.top10) == len(examples)
    assert len(trained_on) == 4
    for i, train_names in enumerate(trained_on):
        held_out = list(run.val_names[2 * i : 2 * i + 2])  # folds are held out in order
        assert set(train_names).isdisjoint(held_out), (i, held_out)
        assert sorted([*train_names, *held_out]) == corpus, i
    with pytest.raises(ValueError, match="folds"):
        evaluate_kfold(examples, split_seed=0, train_seed=0, folds=1)


def test_acceptance_bars_are_discriminating_against_shuffled_labels() -> None:
    """Mutation check: shuffling a held-out graph's heat must break the guard.

    Proves the guard is actually discriminating (the model is reading real
    structure-heat correlation), not vacuously satisfied.
    """
    examples = build_corpus()
    train, val = split_by_graph(examples, val_count=VAL_COUNT, rng=np.random.default_rng(0))
    model, _ = train_heat_model(train, epochs=EPOCHS, seed=0)

    shuffle_rng = np.random.default_rng(99)
    model_scores = []
    baseline_scores_ = []
    for ex in val:
        shuffled_counts = shuffle_rng.permutation(ex.counts.astype(np.float64))
        pred = model.predict(ex.x)
        model_scores.append(spearman(pred, shuffled_counts))
        in_degree = np.expm1(ex.x[:, FEATURE_NAMES.index("log1p_in_degree")])
        baseline_scores_.append(spearman(in_degree, shuffled_counts))

    assert not _guard_holds(float(np.mean(model_scores)), float(np.mean(baseline_scores_)))


def test_margin_floor_is_discriminating_against_an_inverted_model() -> None:
    """A model that ranks nodes backwards must trip the floor, not just the
    absolute bar: the floor is what says "worse than the baseline"."""
    run = evaluate(build_corpus(), split_seed=0, train_seed=0)
    inverted = -np.array(run.model_scores)
    assert not _guard_holds(float(np.mean(inverted)), run.mean_baseline)
    # The floor condition on its own, apart from the absolute bar.
    assert float(np.mean(inverted)) < run.mean_baseline + _MARGIN_FLOOR
