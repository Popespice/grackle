"""NodeRuntimeAdapter — Node/V8 runtime tracer driven over the V8 Inspector (CDP).

ADR-0022. Implements the ``RuntimeAdapter`` Protocol (ADR-0003) for language
``"typescript"``, emitting the same ``TraceEvent`` schema as the Python tracer so
the entire Phase 6–8 pipeline (server / seek / aggregation / Timeline / heat /
flame / diff / session store) works on Node events unchanged — the adapter only has
to *produce* ``TraceEvent``s.

Two channels (the hybrid — see ADR-0022):

- :meth:`trace` → CPU **sampling** profiler → a faithful ``call``/``return`` flame
  (delivered via ``--connect`` replay or ``-o`` file).
- :meth:`trace_streaming` → **precise-coverage** polling → coarse live heat to a
  sink (delivered via ``--stream`` for mid-execution Timeline + heat).

Registered **unconditionally** (discoverable via ``grackle languages`` / the
registry); :meth:`capabilities` reports ``runtime_tracing`` only when a Node
toolchain ≥ 22.6 is present. The CLI checks the capability before tracing and
raises a clean error when the gate is closed — this adapter never crashes on a
missing/old toolchain.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from grackle.adapters.base import Capabilities
from grackle.node_runtime import capability
from grackle.node_runtime.errors import NodeRuntimeError
from grackle.node_runtime.node_resolution import NodeResolver

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from grackle.adapters.base import TraceEvent, TraceOptions


class NodeRuntimeAdapter:
    """Runtime adapter that traces Node/V8 execution of TypeScript via CDP."""

    language: str = "typescript"

    def capabilities(self) -> Capabilities:
        # A RuntimeAdapter only advertises runtime capabilities; the static
        # TypeScript graph belongs to registry.get_static("typescript").
        return Capabilities(runtime_tracing=capability.node_runtime_available())

    def trace(self, script: Path, root: Path, options: TraceOptions) -> Iterator[TraceEvent]:
        """Trace *script* with the sampling profiler and yield the reconstructed stream.

        Runs the whole spawn → attach → profile → reconstruct cycle synchronously
        (via ``asyncio.run``) on first iteration, then yields the events. Mirrors
        :meth:`PythonRuntimeAdapter.trace`.

        Raises:
            TraceCapExceeded: if ``options.max_events`` is exceeded.
            NodeRuntimeError: on spawn/inspector/CDP failure (caught by the CLI).
        """
        from grackle.node_runtime import launcher

        resolver = self._build_resolver(root)
        events = asyncio.run(launcher.run_sampling(script, root, resolver, options))
        yield from events

    def trace_streaming(
        self,
        script: Path,
        root: Path,
        options: TraceOptions,
        sink: Callable[[TraceEvent], None],
    ) -> None:
        """Trace *script* with precise-coverage polling, routing live events to *sink*.

        The coverage poll loop runs on this (main) thread's event loop; *sink* is
        the same non-blocking enqueue used by the Python streaming path
        (``TraceStreamSender.sink``), so the WebSocket transport is reused as-is.

        Raises:
            TraceCapExceeded: if ``options.max_events`` is exceeded.
            NodeRuntimeError: on spawn/inspector/CDP failure (caught by the CLI).
        """
        from grackle.node_runtime import launcher

        resolver = self._build_resolver(root)
        asyncio.run(launcher.run_coverage(script, root, resolver, options, sink))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_resolver(self, root: Path) -> NodeResolver:
        """Build a :class:`NodeResolver` from the TypeScript static graph for *root*."""
        from grackle.adapters import registry
        from grackle.adapters.base import ParseOptions

        static_adapter = registry.get_static("typescript")
        if static_adapter is None:
            raise NodeRuntimeError(
                "TypeScript static adapter not registered; cannot resolve node IDs"
            )
        graph = static_adapter.parse(root, ParseOptions())
        return NodeResolver(root, graph)
