"""Self-supervised hotspot-prediction pipeline (Phase 12.1, ADR-0028/ADR-0029).

Iron rules for every module in this package:

- **Dict-level consumption only.** Every graph/trace input is a
  ``Mapping[str, Any]`` (bare ``grackle parse``/trace-JSONL shape) — never a
  ``grackle`` TypedDict, never anything requiring the agent package to be
  installed at runtime.
- **Never imports ``grackle`` at runtime — not even under ``TYPE_CHECKING``.**
  ``grackle`` is an editable **dev**-only dependency (tests may import it for
  cross-checks against the live implementation); this package must remain
  fully usable with only ``grackle-nn``'s own runtime deps (numpy).

See ``PHASE_12_SUMMARY.md`` and ADR-0028 for the full design rationale.
"""

from __future__ import annotations

from grackle_nn.ml.dataset import (
    Example,
    build_example,
    load_corpus_dir,
    load_pair,
    split_by_graph,
)
from grackle_nn.ml.features import FEATURE_VERSION, extract_features
from grackle_nn.ml.heat_model import HeatModel, ModelFormatError, train_heat_model
from grackle_nn.ml.labels import heat_from_jsonl
from grackle_nn.ml.metrics_rank import spearman, top_k_overlap

__all__ = [
    "FEATURE_VERSION",
    "Example",
    "HeatModel",
    "ModelFormatError",
    "build_example",
    "extract_features",
    "heat_from_jsonl",
    "load_corpus_dir",
    "load_pair",
    "spearman",
    "split_by_graph",
    "top_k_overlap",
    "train_heat_model",
]
