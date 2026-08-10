"""Bridge to the optional ``grackle-nn`` ML package (Phase 12.2, ADR-0028/ADR-0030).

``grackle-nn`` is not published to PyPI — it lives at ``packages/nn`` in this
repo and is wired into the agent as an editable **dev**-only dependency,
purely so this module can be exercised against a real implementation in
tests. At runtime :func:`learn_available` soft-imports it; when absent (or
broken), ``grackle learn`` closes its gate with a remediation message and the
server-side ``predicted_heat`` injection silently no-ops — the agent's
runtime hard dependencies stay numpy-free either way (mirrors the
``go_runtime``/``rust_runtime``/``node_runtime`` toolchain-capability gates).

This module is the **only** place in the agent package that imports
``grackle_nn``, and it does so exclusively inside function bodies — never at
module scope — so importing ``grackle.cli`` or ``grackle.server`` never pulls
in numpy.

Every public function here contains the ML package's entire failure surface
(a corrupt checkpoint, a version/shape mismatch, an empty corpus, a bad
prediction shape) behind :class:`MLBridgeError`. Callers — the CLI and the
server's static-graph push — need to catch only this one type; the original
cause is preserved via ``__cause__``.
"""

from __future__ import annotations

import functools
import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from grackle.adapters.base import StaticGraph


class MLBridgeError(Exception):
    """Any failure inside the ML bridge — model I/O, training, or prediction."""


@functools.cache
def learn_available() -> bool:
    """``True`` iff ``grackle_nn.ml`` can be imported (the ML extras are installed).

    Cached for the process lifetime, mirroring the toolchain-capability gates
    (``go_runtime.capability.go_runtime_available``, etc.). Tests that want to
    simulate a different environment monkeypatch the import machinery and
    call :func:`reset_cache`.
    """
    try:
        import grackle_nn.ml  # noqa: F401
    except ImportError:
        return False
    return True


def remediation_message() -> str:
    """Human-readable guidance for when the ML gate is closed.

    ``grackle-nn`` is not on PyPI — it only exists as a sibling package in
    this repo — so the remediation names the real, local install command
    rather than a fictional package-index one.
    """
    return (
        "grackle's ML features (`grackle learn`, predicted_heat) require the "
        "grackle-nn package. It is not on PyPI — it ships in this repo at "
        "packages/nn. Install it into the agent's environment with: "
        "`cd packages/agent && uv pip install -e ../nn`."
    )


def reset_cache() -> None:
    """Clear the cached availability check (for tests that monkeypatch the import)."""
    learn_available.cache_clear()


@dataclass(frozen=True, slots=True)
class LearnSummary:
    """Summary of one ``grackle learn`` run.

    Also the shape appended (as one JSON line) to
    ``<model dir>/learn-history.jsonl`` — the "report card" that makes "is the
    model getting smarter over time?" answerable across future runs. Every
    trace fed into one ``learn`` invocation shares a single ``--root``
    (D12.7), so there is exactly one graph and no held-out graph to validate
    against (D12.4 splits by graph, never by node) — ``train_spearman`` and
    ``baseline_spearman`` are therefore train-set metrics, never mislabeled
    as validation. ``val_spearman`` is not computed here; it stays ``null``
    in the report card until a multi-root corpus exists (the session store's
    ``root`` column, ADR-0030 future work).
    """

    examples: int
    nodes: int
    epochs: int
    seed: int
    final_loss: float
    train_spearman: float
    baseline_spearman: float
    model_path: str


def train_and_save(
    graph: StaticGraph,
    heat: Mapping[str, int],
    *,
    epochs: int,
    seed: int,
    out: Path,
) -> LearnSummary:
    """Train a hotspot-prediction model on one ``(graph, heat)`` pair, save it to *out*.

    *heat* must already be merged across every trace fed into this ``learn``
    run (summed ``node_id -> count``) with ``"<unresolved>"`` filtered out —
    the caller's job; this module never reads trace files directly. Save is
    atomic (``HeatModel.save``'s tmp+replace); the report-card line is
    appended only after the model itself is durably on disk, so a JSON line
    never dangles ahead of the artifact it describes.

    Raises :class:`MLBridgeError` for every failure mode: an empty/zero-node
    graph, a training failure, or a write failure.
    """
    try:
        from grackle_nn.ml import build_example, spearman, train_heat_model

        example = build_example("root", graph, heat)
        if example.x.shape[0] == 0:
            raise MLBridgeError("the parsed graph has no nodes — nothing to train on")

        model, history = train_heat_model([example], epochs=epochs, seed=seed)

        out.parent.mkdir(parents=True, exist_ok=True)
        model.save(out)

        predicted = model.predict(example.x)
        baseline = example.x[:, 0]  # feature 0: log1p(in_degree) — the D12.4 baseline
        counts = example.counts.astype("float64")
        train_spearman = spearman(predicted, counts)
        baseline_spearman = spearman(baseline, counts)
        final_loss = float(history[-1][1]) if history else float("nan")

        summary = LearnSummary(
            examples=1,
            nodes=example.x.shape[0],
            epochs=epochs,
            seed=seed,
            final_loss=final_loss,
            train_spearman=train_spearman,
            baseline_spearman=baseline_spearman,
            model_path=str(out),
        )
        _append_learn_history(out, summary)
        return summary
    except MLBridgeError:
        raise
    except Exception as exc:
        raise MLBridgeError(f"training failed: {exc}") from exc


def _append_learn_history(model_path: Path, summary: LearnSummary) -> None:
    """Append one report-card line to ``<model dir>/learn-history.jsonl``.

    Append-only (``open("a")``) — a local single-writer file, same durability
    posture as the model artifact itself (see the persistence matrix in the
    Phase 12 plan). Never raises: a report-card write failure must not turn a
    successful ``learn`` run into a CLI error.
    """
    from grackle_nn.ml import FEATURE_VERSION

    history_path = model_path.parent / "learn-history.jsonl"
    record = {
        "ts": time.time_ns(),
        "model_version": FEATURE_VERSION,
        "feature_version": FEATURE_VERSION,
        "examples": summary.examples,
        "nodes": summary.nodes,
        "epochs": summary.epochs,
        "seed": summary.seed,
        "final_loss": summary.final_loss,
        "train_spearman": summary.train_spearman,
        "baseline_spearman": summary.baseline_spearman,
        "val_spearman": None,
    }
    try:
        with history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError:
        pass


def predict_scores(graph: StaticGraph, model_path: Path) -> dict[str, Any]:
    """Load a saved model and score every node in *graph*.

    Returns the D12.9 wire shape — ``{"model_version": int, "scores": [...]}``
    — for **all** nodes (no top-N truncation: an under-predicted "surprise
    hotspot" must stay visible, not be truncated away by construction).

    Every failure mode is caught and re-raised as :class:`MLBridgeError`: a
    missing/corrupt/truncated checkpoint, a ``feature_version``/shape
    mismatch, or a malformed graph. Callers (the server's static-graph push)
    rely on this containment — a broken model must never crash the graph
    push that calls this.
    """
    try:
        from grackle_nn.ml import FEATURE_VERSION, HeatModel, extract_features

        model = HeatModel.load(model_path)
        node_ids, x = extract_features(graph)
        scores = model.predict(x)
        return {
            "model_version": FEATURE_VERSION,
            "scores": [
                {"node_id": nid, "score": round(float(s), 4)}
                for nid, s in zip(node_ids, scores, strict=True)
            ],
        }
    except Exception as exc:
        raise MLBridgeError(f"prediction failed for model {model_path}: {exc}") from exc
