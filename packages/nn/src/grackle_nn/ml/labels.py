"""Heat labels for the self-supervised hotspot-prediction model.

``heat_from_jsonl`` is an intentional line-for-line mirror of the agent's
``python_runtime/aggregates.py`` ``_event_weight`` + ``cumulative_heat_all``
— it must stay pinned to that behavior (a cross-check test proves the two
agree on real golden traces) so a trained model's labels always match what
the live heat-map/diff overlay would show. It deliberately goes further than
the mirrored original in one respect: a JSON line that parses but isn't an
object, or a ``node_id`` that isn't a string, is skipped rather than raised
— ``aggregates.py`` assumes every line is an object with a string
``node_id`` (true of every writer grackle ships) and would raise
``AttributeError``/``TypeError`` on such a line instead. This module never
imports ``grackle`` at runtime, not even under ``TYPE_CHECKING``.
"""

from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING, Any

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from grackle_nn._types import Array


def _event_weight(event: dict[str, Any]) -> int:
    """Count-weight of one parsed trace event (line-for-line mirror of
    ``aggregates.py::_event_weight``): ``metadata.count``, default 1.

    Non-dict/missing metadata, a missing/non-numeric/non-finite/bool count,
    or a count < 1 all collapse to the default weight of 1. Finite numeric
    counts truncate to ``int`` and floor at 1.
    """
    metadata = event.get("metadata")
    if not isinstance(metadata, dict):
        return 1
    raw = metadata.get("count", 1)
    if isinstance(raw, bool):
        return 1
    if isinstance(raw, int):
        return max(1, raw)
    if isinstance(raw, float) and math.isfinite(raw):
        return max(1, int(raw))
    return 1


def heat_from_jsonl(path: Path) -> dict[str, int]:
    """Cumulative weighted hit count per ``node_id`` over an entire JSONL trace.

    Every successfully-parsed JSON object with a truthy string ``node_id``
    contributes ``_event_weight(event)`` — counting both ``call`` and
    ``return`` events, identical to ``TraceAggregates.cumulative_heat_all``
    at the end of the file. Blank lines, JSON parse failures, non-object
    top-level values, and a non-string ``node_id`` (which could otherwise be
    unhashable, e.g. a JSON array) are all skipped (never raised).
    """
    heat: dict[str, int] = {}
    with path.open("rb") as f:
        for raw_line in f:
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                event = json.loads(stripped.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(event, dict):
                continue
            node_id = event.get("node_id")
            if not isinstance(node_id, str) or not node_id:
                continue
            heat[node_id] = heat.get(node_id, 0) + _event_weight(event)
    return heat


def make_targets(node_ids: Sequence[str], heat: Mapping[str, int]) -> Array:
    """Per-graph max-normalized log heat (D12.2): ``log1p(count) /
    log1p(max_count_in_graph)``, in ``[0, 1]``.

    All-zero (or empty) heat returns an all-zero array — there is nothing to
    normalize against.

    Numerator and denominator both go through ``np.log1p`` — never
    ``math.log1p`` for one of them. They are different implementations
    (numpy's own vs. the platform libm) and may disagree by 1 ULP, which on
    at least one Linux CI runner made the hottest node normalize to
    ``1.0000000000000002`` — breaking the documented ``[0, 1]`` upper bound,
    and only on that platform. Using one implementation for both makes the
    max element cancel to exactly ``1.0`` by IEEE-754 construction
    (``a / a == 1.0`` for any finite non-zero ``a``) on every platform.
    """
    counts = np.array([heat.get(nid, 0) for nid in node_ids], dtype=np.float64)
    max_count = float(counts.max()) if counts.size else 0.0
    if max_count <= 0.0:
        return np.zeros(counts.shape, dtype=np.float64)
    result: Array = np.log1p(counts) / np.log1p(max_count)
    return result
