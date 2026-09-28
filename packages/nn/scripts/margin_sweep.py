"""Sweep the ADR-0029 synthetic-acceptance margin over many seeds (campaign T9-1).

The acceptance test (``tests/ml/test_synthetic_acceptance.py``) trains once, at
split seed 0 and train seed 0, and asserts ``mean_model >= mean_baseline +
0.05`` and ``mean_model > 0.5`` over the two held-out graphs. That is a single
draw. Its margin (``mean_model - mean_baseline``) is about +0.056, so the bar
has about 0.006 of headroom at that one point, and nothing says how typical
that point is. This script answers that question. It runs the test's own
pipeline (``tests/ml/acceptance_eval.py``, the same corpus, split, training and
scoring code) over N ``(split_seed, train_seed)`` pairs and reports the
distribution of the margin.

It is additive. ADR-0029 §6 forbids re-seeding the acceptance test, and this
script does not touch it: it only measures how the fixed test sits within the
distribution it was drawn from. If the lower tail crosses the bar, the bar is
closer to a coin flip than a guarantee: any legitimate change that re-draws
the training trajectory (a different order of RNG draws, a changed default,
a fix elsewhere in the pipeline) can turn CI red without a real regression.

Usage, from ``packages/nn``::

    uv run python scripts/margin_sweep.py --seeds 200 --json margin-sweep.json

Options:

``--seeds N``
    Number of seed pairs to run (default 50). Each run trains for 300 epochs
    and takes about 0.3 s on an M-series Mac.
``--start S``
    First seed (default 0). Pair ``k`` uses seed ``S + k``.
``--mode {joint,split,train}``
    Which seeds vary. ``joint`` (default) uses ``(S+k, S+k)``, so the split
    and the init/shuffle stream both vary. ``split`` uses ``(S+k, 0)``, so
    only the held-out pair of graphs changes. ``train`` uses ``(0, S+k)``,
    so only the weight init and minibatch order change. With the default
    ``--start 0`` every mode includes ``(0, 0)``, the test's own draw, and its
    margin is printed alongside the distribution.
``--json PATH``
    Also write the configuration, the summary and every run to PATH.
``--max-fraction-below F``
    Exit 1 if more than a fraction F of runs fall below the margin bar.
    Without it the script only reports, and always exits 0.

The summary gives min / p5 / p25 / median / mean / max of the margin (p5 and
p25 are ``numpy.percentile``'s linear interpolation), the fraction of runs below
the +0.05 margin bar, the fraction below the absolute 0.5 bar, the fraction
that would fail the test (either bar), the mean top-10 overlap, and the five
worst pairs. The bars are imported from the test module, so the sweep always
measures against the test's actual thresholds.

The pipeline modules live in ``tests/ml``, which this script puts on
``sys.path`` at run time; type-check it together with them, as
``mypy --strict src tests scripts``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Sequence

    from acceptance_eval import AcceptanceRun

_TESTS_ML = Path(__file__).resolve().parent.parent / "tests" / "ml"

type Mode = Literal["joint", "split", "train"]


@dataclass(frozen=True, slots=True)
class Bars:
    margin: float
    absolute: float


def _import_pipeline() -> Bars:
    """Put ``tests/ml`` on ``sys.path`` so the test's own helpers import.

    ``synth.py`` and ``acceptance_eval.py`` are test-only modules, never
    shipped under ``src/``. The sweep deliberately reuses them rather than
    copying them, so that it cannot drift from what the test measures.
    """
    if str(_TESTS_ML) not in sys.path:
        sys.path.insert(0, str(_TESTS_ML))
    from test_synthetic_acceptance import _ABSOLUTE_BAR, _MARGIN_BAR

    return Bars(margin=_MARGIN_BAR, absolute=_ABSOLUTE_BAR)


def seed_pairs(mode: Mode, start: int, count: int) -> list[tuple[int, int]]:
    """The ``(split_seed, train_seed)`` pairs for *mode*."""
    seeds = range(start, start + count)
    if mode == "joint":
        return [(s, s) for s in seeds]
    if mode == "split":
        return [(s, 0) for s in seeds]
    return [(0, s) for s in seeds]


def summarize(runs: Sequence[AcceptanceRun], bars: Bars) -> dict[str, Any]:
    """Distribution statistics over *runs*, with each bar applied as the test applies it."""
    margins = np.array([r.margin for r in runs], dtype=np.float64)
    below_margin = [r.mean_model < r.mean_baseline + bars.margin for r in runs]
    below_absolute = [not r.mean_model > bars.absolute for r in runs]
    failing = [m or a for m, a in zip(below_margin, below_absolute, strict=True)]
    worst = sorted(runs, key=lambda r: r.margin)[:5]
    return {
        "n": len(runs),
        "margin_min": float(margins.min()),
        "margin_p5": float(np.percentile(margins, 5)),
        "margin_p25": float(np.percentile(margins, 25)),
        "margin_median": float(np.median(margins)),
        "margin_mean": float(margins.mean()),
        "margin_std": float(margins.std(ddof=1)) if len(runs) > 1 else 0.0,
        "margin_max": float(margins.max()),
        "fraction_below_margin_bar": sum(below_margin) / len(runs),
        "fraction_below_absolute_bar": sum(below_absolute) / len(runs),
        "fraction_failing_test": sum(failing) / len(runs),
        "mean_model_min": min(r.mean_model for r in runs),
        "mean_top10": float(np.mean([r.mean_top10 for r in runs])),
        "min_top10": min(r.mean_top10 for r in runs),
        "worst_pairs": [
            {"split_seed": r.split_seed, "train_seed": r.train_seed, "margin": r.margin}
            for r in worst
        ],
    }


def _run_record(run: AcceptanceRun) -> dict[str, Any]:
    return {
        "split_seed": run.split_seed,
        "train_seed": run.train_seed,
        "val_names": list(run.val_names),
        "model_scores": list(run.model_scores),
        "baseline_scores": list(run.baseline_scores),
        "top10": list(run.top10),
        "mean_model": run.mean_model,
        "mean_baseline": run.mean_baseline,
        "margin": run.margin,
    }


def _print_summary(summary: dict[str, Any], bars: Bars, runs: Sequence[AcceptanceRun]) -> None:
    print(f"margin sweep: n={summary['n']}  (margin = mean_model - mean_baseline)")
    print(
        f"  min {summary['margin_min']:+.4f}   p5 {summary['margin_p5']:+.4f}   "
        f"p25 {summary['margin_p25']:+.4f}   median {summary['margin_median']:+.4f}   "
        f"mean {summary['margin_mean']:+.4f}   max {summary['margin_max']:+.4f}   "
        f"std {summary['margin_std']:.4f}"
    )
    print(
        f"  below margin bar ({bars.margin:+.2f}): {summary['fraction_below_margin_bar']:.1%}   "
        f"below absolute bar (>{bars.absolute}): {summary['fraction_below_absolute_bar']:.1%}   "
        f"would fail the test: {summary['fraction_failing_test']:.1%}"
    )
    print(f"  top-10 overlap: mean {summary['mean_top10']:.3f}   min {summary['min_top10']:.3f}")
    worst = ", ".join(
        f"({w['split_seed']},{w['train_seed']}) {w['margin']:+.4f}" for w in summary["worst_pairs"]
    )
    print(f"  worst pairs (split,train): {worst}")
    test_draw = next((r for r in runs if (r.split_seed, r.train_seed) == (0, 0)), None)
    if test_draw is not None:
        rank = sum(1 for r in runs if r.margin < test_draw.margin)
        print(
            f"  the test's own draw (0,0): margin {test_draw.margin:+.4f}, "
            f"{rank} of {summary['n']} runs below it"
        )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sweep the ADR-0029 synthetic-acceptance margin over seed pairs."
    )
    parser.add_argument("--seeds", type=int, default=50, help="number of seed pairs (default 50)")
    parser.add_argument("--start", type=int, default=0, help="first seed (default 0)")
    parser.add_argument("--mode", choices=("joint", "split", "train"), default="joint")
    parser.add_argument("--json", type=Path, default=None, help="write full results here")
    parser.add_argument(
        "--max-fraction-below",
        type=float,
        default=None,
        help="exit 1 if more than this fraction of runs fall below the margin bar",
    )
    args = parser.parse_args(argv)
    if args.seeds < 1:
        parser.error("--seeds must be at least 1")

    bars = _import_pipeline()
    from acceptance_eval import EPOCHS, build_corpus, evaluate

    mode: Mode = args.mode
    pairs = seed_pairs(mode, args.start, args.seeds)
    examples = build_corpus()
    runs: list[AcceptanceRun] = []
    started = time.perf_counter()
    for i, (split_seed, train_seed) in enumerate(pairs, start=1):
        run = evaluate(examples, split_seed=split_seed, train_seed=train_seed)
        runs.append(run)
        print(
            f"[{i}/{len(pairs)}] split={split_seed} train={train_seed} "
            f"val={','.join(run.val_names)} margin={run.margin:+.4f} "
            f"model={run.mean_model:.4f} top10={run.mean_top10:.2f}",
            file=sys.stderr,
        )
    elapsed = time.perf_counter() - started

    summary = summarize(runs, bars)
    _print_summary(summary, bars, runs)
    print(f"  {len(runs)} runs in {elapsed:.1f}s")

    if args.json is not None:
        payload = {
            "config": {
                "mode": mode,
                "start": args.start,
                "seeds": args.seeds,
                "epochs": EPOCHS,
                "margin_bar": bars.margin,
                "absolute_bar": bars.absolute,
                "numpy": np.__version__,
                "platform": sys.platform,
            },
            "summary": summary,
            "runs": [_run_record(r) for r in runs],
        }
        args.json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    limit = args.max_fraction_below
    if limit is not None and summary["fraction_below_margin_bar"] > limit:
        print(
            f"FAIL: {summary['fraction_below_margin_bar']:.1%} of runs below the margin bar "
            f"(limit {limit:.1%})",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
