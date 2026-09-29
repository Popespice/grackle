"""The ADR-0029 synthetic-acceptance pipeline, shared by the test and the margin sweep.

Test-only, like ``synth.py``. ``test_synthetic_acceptance.py`` runs this
pipeline once, at the ADR's fixed seeds (split 0, train 0), against the bars.
``scripts/margin_sweep.py`` (campaign T9-1) runs the *same* pipeline over many
``(split_seed, train_seed)`` pairs to characterize the margin's distribution.
Keeping the pipeline in one place is what makes the sweep a statement about
the test: both measure one computation, and they cannot drift apart.

Two evaluations share it. ``evaluate`` is the original ADR-0029 protocol: one
split, two held-out graphs. ``evaluate_kfold`` (finding F-12, owner decision)
holds every graph of the corpus out exactly once, so the margin is a mean over
all eight graphs instead of two.

The seeds of the acceptance test itself are never changed (ADR-0029 §6's
escape hatch). This module takes them as arguments; it does not choose them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
from synth import make_synthetic_pair

from grackle_nn.ml.dataset import build_example, split_by_graph
from grackle_nn.ml.features import FEATURE_NAMES
from grackle_nn.ml.heat_model import train_heat_model
from grackle_nn.ml.metrics_rank import spearman, top_k_overlap

if TYPE_CHECKING:
    from collections.abc import Sequence

    from grackle_nn.ml.dataset import Example
    from grackle_nn.ml.heat_model import HeatModel

CORPUS_SIZE = 8
VAL_COUNT = 2
EPOCHS = 300
TOP_K = 10
FOLDS = 4  # 8 graphs -> 4 folds of 2: each training set is 6 graphs, as in `evaluate`


def build_corpus() -> list[Example]:
    """The 8-graph seeded synthetic corpus (graph seeds 0..7, fixed)."""
    examples = []
    for seed in range(CORPUS_SIZE):
        graph, heat = make_synthetic_pair(seed)
        examples.append(build_example(f"synth{seed}", graph, heat))
    return examples


def baseline_scores(val: Sequence[Example]) -> list[float]:
    """Spearman of raw in-degree (feature column 0, un-logged) against heat, per graph."""
    scores = []
    for ex in val:
        in_degree = np.expm1(ex.x[:, FEATURE_NAMES.index("log1p_in_degree")])
        counts = ex.counts.astype(np.float64)
        scores.append(spearman(in_degree, counts))
    return scores


@dataclass(frozen=True, slots=True)
class AcceptanceRun:
    """One train/evaluate pass: per-held-out-graph scores and the derived margin."""

    split_seed: int
    train_seed: int
    val_names: tuple[str, ...]
    model_scores: tuple[float, ...]
    baseline_scores: tuple[float, ...]
    top10: tuple[float, ...]

    @property
    def mean_model(self) -> float:
        return float(np.mean(self.model_scores))

    @property
    def mean_baseline(self) -> float:
        return float(np.mean(self.baseline_scores))

    @property
    def margin(self) -> float:
        """``mean_model - mean_baseline`` — the quantity the +0.05 bar is set on."""
        return self.mean_model - self.mean_baseline

    @property
    def mean_top10(self) -> float:
        return float(np.mean(self.top10))


def _score_held_out(model: HeatModel, val: Sequence[Example]) -> tuple[list[float], list[float]]:
    """Per-graph Spearman and top-k overlap of *model* on each held-out graph."""
    model_scores = []
    top10 = []
    for ex in val:
        pred = model.predict(ex.x)
        counts = ex.counts.astype(np.float64)
        model_scores.append(spearman(pred, counts))
        top10.append(top_k_overlap(pred, counts, TOP_K))
    return model_scores, top10


def evaluate(
    examples: Sequence[Example], *, split_seed: int, train_seed: int, epochs: int = EPOCHS
) -> AcceptanceRun:
    """Split whole graphs, train, and score the held-out graphs against the baseline."""
    train, val = split_by_graph(
        examples, val_count=VAL_COUNT, rng=np.random.default_rng(split_seed)
    )
    model, _ = train_heat_model(train, epochs=epochs, seed=train_seed)
    model_scores, top10 = _score_held_out(model, val)

    return AcceptanceRun(
        split_seed=split_seed,
        train_seed=train_seed,
        val_names=tuple(ex.name for ex in val),
        model_scores=tuple(model_scores),
        baseline_scores=tuple(baseline_scores(val)),
        top10=tuple(top10),
    )


def evaluate_kfold(
    examples: Sequence[Example],
    *,
    split_seed: int,
    train_seed: int,
    folds: int = FOLDS,
    epochs: int = EPOCHS,
) -> AcceptanceRun:
    """K-fold over whole graphs: every graph is held out exactly once.

    ``split_seed`` fixes how the graphs are dealt into folds (a seeded
    permutation, split into *folds* near-equal groups) and ``train_seed`` seeds
    every fold's training. A graph never appears in its own training set, and a
    graph's rows never straddle folds (the whole-graph invariant of
    ``split_by_graph``). The result reuses :class:`AcceptanceRun`, so ``margin``
    is the mean over *all* graphs of ``model - baseline``, and ``val_names``
    lists the graphs in the order they were held out.
    """
    if not 2 <= folds <= len(examples):
        raise ValueError(f"folds ({folds}) must be between 2 and len(examples) ({len(examples)})")
    order = np.random.default_rng(split_seed).permutation(len(examples))
    val_names: list[str] = []
    model_scores: list[float] = []
    baselines: list[float] = []
    top10: list[float] = []
    for fold in np.array_split(order, folds):
        held_out = {int(i) for i in fold}
        train = [examples[int(i)] for i in order if int(i) not in held_out]
        val = [examples[int(i)] for i in fold]
        model, _ = train_heat_model(train, epochs=epochs, seed=train_seed)
        fold_scores, fold_top10 = _score_held_out(model, val)
        val_names.extend(ex.name for ex in val)
        model_scores.extend(fold_scores)
        baselines.extend(baseline_scores(val))
        top10.extend(fold_top10)

    return AcceptanceRun(
        split_seed=split_seed,
        train_seed=train_seed,
        val_names=tuple(val_names),
        model_scores=tuple(model_scores),
        baseline_scores=tuple(baselines),
        top10=tuple(top10),
    )
