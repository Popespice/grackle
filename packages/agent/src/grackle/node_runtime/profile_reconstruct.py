"""Pure reconstruction of a call/return stream from a V8 CPU sampling profile.

This is the **faithful** channel of the Node/V8 runtime adapter (ADR-0022). The V8
sampling profiler (``Profiler.start`` → ``Profiler.stop``) returns a *merged call
tree* plus a temporal list of samples (one tree-node id per sample tick) and the
inter-sample time deltas — the same shape as a ``.cpuprofile`` file. A sampled call
tree *is* a flame graph, so reconstructing a ``call``/``return`` stream from it
feeds the Phase-8.2 flame infra directly.

The function is pure and Node-free: it takes the profile dict plus a ``resolve``
callback (callFrame → grackle node ID, or ``None`` to filter the frame), so it is
unit-tested from a captured ``.cpuprofile`` fixture with no Node in the loop.

Algorithm — stack-diff over samples, by V8 tree-node identity:

- Each tree node is a unique root→leaf *call path*; the path of tree-node ids from
  root to the sampled node IS the call stack at that sample. Recursion and
  multiple call sites of the same function map to *distinct* tree nodes, so
  comparing paths by tree-node id correctly tells "same frame instance" from
  "different frame".
- Per sample, the *filtered* stack keeps only frames that ``resolve`` to a project
  node (pseudo-frames / ``node:internal`` / out-of-project files drop out, exactly
  as the Python tracer's ``sys.monitoring.DISABLE`` skips non-project code).
- Consecutive filtered stacks are diffed: frames present before but not now are
  closed (``return``, deepest first); frames present now but not before are opened
  (``call``, shallowest first). ``frame_depth`` is the 0-based index in the
  filtered stack, so a ``call`` and its matching ``return`` carry the *same* depth
  — the invariant the Phase-8.2 call-tree builder relies on.
- ``ts_ns`` is the cumulative sample time (``startTime + Σ timeDeltas``,
  microseconds → nanoseconds). All frames collapse to ``thread_id = 0`` (single V8
  isolate; worker threads are out of scope → Phase 9).

Known characteristic (documented in ADR-0022): because this is a CPU profile, time
the main thread spends *off-CPU* (``(idle)`` samples) closes the project stack and a
later sample reopens it. For synchronous CPU work this is a no-op; for async gaps it
fragments a frame into multiple spans — faithful to "what was on-CPU".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from grackle.adapters.base import TraceEvent

# V8 profile times are microseconds; TraceEvent.ts_ns is nanoseconds.
_US_TO_NS = 1000

# Single main V8 isolate. Worker threads (separate inspector targets) → Phase 9.
_THREAD_ID = 0


def reconstruct(
    profile: Mapping[str, Any],
    resolve: Callable[[Mapping[str, Any]], str | None],
) -> list[TraceEvent]:
    """Reconstruct a ``call``/``return`` ``TraceEvent`` stream from a V8 CPU profile.

    Args:
        profile: A V8 profile dict (``Profiler.stop().profile`` / ``.cpuprofile``):
            ``{nodes, samples, timeDeltas, startTime, endTime}``.
        resolve: Maps a V8 callFrame dict to a grackle node ID, or returns ``None``
            to filter the frame out of the project stack.

    Returns:
        Time-ordered ``TraceEvent``s (``call``/``return`` only) with ``frame_depth``
        the 0-based project-stack index and ``ts_ns`` the cumulative sample time.
    """
    samples = profile.get("samples") or []
    if not samples:
        return []

    nodes = profile.get("nodes") or []
    time_deltas = profile.get("timeDeltas") or []
    start_us = int(profile.get("startTime", 0))
    end_us = int(profile.get("endTime", start_us))

    nodes_by_id: dict[int, Mapping[str, Any]] = {}
    for node in nodes:
        try:
            nodes_by_id[int(node["id"])] = node
        except (KeyError, TypeError, ValueError):
            continue  # malformed node — tolerate, matching the module's defensive style
    parent_of = _build_parents(nodes_by_id)
    # tree-node id -> filtered project stack: list of (tree_id, grackle_node_id).
    path_cache: dict[int, list[tuple[int, str]]] = {}

    def filtered_path(node_id: int) -> list[tuple[int, str]]:
        cached = path_cache.get(node_id)
        if cached is not None:
            return cached
        chain: list[tuple[int, str]] = []
        for tid in _path_to_root(node_id, parent_of):
            node = nodes_by_id.get(tid)
            if node is None:
                continue
            call_frame = node.get("callFrame")
            if call_frame is None:
                continue  # malformed node without a callFrame — drop the frame
            grackle_id = resolve(call_frame)
            if grackle_id is not None:
                chain.append((tid, grackle_id))
        path_cache[node_id] = chain
        return chain

    events: list[TraceEvent] = []
    prev: list[tuple[int, str]] = []
    cum_us = start_us

    for i, raw_sid in enumerate(samples):
        # V8's timeDeltas[i] is the time BEFORE sample i, so sample i represents
        # the interval (prev_cum, cum]. Stamp the whole transition at prev_cum —
        # the START of that interval — so a frame that is the deepest leaf for a
        # single sample is credited that interval's duration instead of collapsing
        # to zero (which would mis-fold its self-time into its parent).
        prev_cum = cum_us
        cum_us += int(time_deltas[i]) if i < len(time_deltas) else 0
        sid = int(raw_sid)
        if sid not in nodes_by_id:
            # Malformed sample referencing an unknown node — skip rather than
            # corrupt the open-frame stack. (Cumulative time already advanced.)
            continue
        cur = filtered_path(sid)
        _emit_transition(events, prev, cur, prev_cum * _US_TO_NS)
        prev = cur

    # Close any frames still open at profile end (never before the last sample).
    _emit_transition(events, prev, [], max(end_us, cum_us) * _US_TO_NS)
    return events


def _emit_transition(
    events: list[TraceEvent],
    prev: list[tuple[int, str]],
    cur: list[tuple[int, str]],
    ts_ns: int,
) -> None:
    """Append the ``return``/``call`` events moving the stack from *prev* to *cur*."""
    common = 0
    limit = min(len(prev), len(cur))
    while common < limit and prev[common][0] == cur[common][0]:
        common += 1
    # Close popped frames, deepest first.
    for depth in range(len(prev) - 1, common - 1, -1):
        events.append(_event("return", prev[depth][1], ts_ns, depth))
    # Open pushed frames, shallowest first.
    for depth in range(common, len(cur)):
        events.append(_event("call", cur[depth][1], ts_ns, depth))


def _event(kind: str, node_id: str, ts_ns: int, depth: int) -> TraceEvent:
    return {
        "event": kind,
        "node_id": node_id,
        "ts_ns": ts_ns,
        "thread_id": _THREAD_ID,
        "frame_depth": depth,
        "metadata": {},
    }


def _build_parents(nodes_by_id: Mapping[int, Mapping[str, Any]]) -> dict[int, int | None]:
    parent_of: dict[int, int | None] = dict.fromkeys(nodes_by_id, None)
    for nid, node in nodes_by_id.items():
        for child in node.get("children") or []:
            cid = int(child)
            if cid in parent_of:
                parent_of[cid] = nid
    return parent_of


def _path_to_root(node_id: int, parent_of: Mapping[int, int | None]) -> list[int]:
    """Return tree-node ids from root → *node_id* (inclusive).

    Guards against cycles in a malformed profile via a visited set.
    """
    rev: list[int] = []
    seen: set[int] = set()
    cur: int | None = node_id
    while cur is not None and cur not in seen:
        seen.add(cur)
        rev.append(cur)
        cur = parent_of.get(cur)
    rev.reverse()
    return rev
