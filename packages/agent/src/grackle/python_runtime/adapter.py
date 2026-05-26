"""PythonRuntimeAdapter — sys.monitoring-based runtime tracer for Python.

Registered against the ``AdapterRegistry`` runtime slot in ``__init__.py``.
Satisfies the ``RuntimeAdapter`` Protocol (ADR-0003).

Requires Python 3.12+ (``sys.monitoring`` introduced in PEP 669). The
``runtime_tracing`` capability flag is always ``True`` because
``packages/agent/pyproject.toml`` pins ``requires-python = ">=3.12"``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from grackle.adapters.base import Capabilities, TraceEvent, TraceOptions
from grackle.python_runtime.node_resolution import NodeResolver
from grackle.python_runtime.tracer import Tracer

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path


class PythonRuntimeAdapter:
    """Runtime adapter that traces Python script execution via sys.monitoring."""

    language: str = "python"

    def capabilities(self) -> Capabilities:
        # A ``RuntimeAdapter`` only advertises runtime capabilities.
        # Static-graph features (files/classes/functions/imports/calls/
        # annotations) belong to the ``StaticParserAdapter`` returned by
        # ``registry.get_static("python")``. Mixing both on the runtime
        # adapter was misleading and made it look as if the runtime tracer
        # itself produced a static graph.
        return Capabilities(runtime_tracing=True)

    def trace(self, script: Path, root: Path, options: TraceOptions) -> Iterator[TraceEvent]:
        """Execute *script* under sys.monitoring and yield collected trace events.

        Args:
            script:  Path to the Python script to run (must be absolute or
                     resolvable relative to the caller's cwd).
            root:    Project root used to build the static graph and normalise
                     ``co_filename`` values.
            options: Tracing configuration (line events, event cap).

        Yields:
            ``TraceEvent`` dicts, one per monitored frame entry/exit/exception.

        Raises:
            TraceCapExceeded: if ``options.max_events`` is set and reached.
        """
        tracer = self._build_tracer(root, options)
        events = tracer.run(script)
        yield from events

    def trace_streaming(
        self,
        script: Path,
        root: Path,
        options: TraceOptions,
        sink: Callable[[TraceEvent], None],
    ) -> None:
        """Execute *script* routing each event to *sink* instead of a list.

        Intended for real-time streaming (``grackle trace --stream``).  The
        *sink* is called directly from ``sys.monitoring`` callbacks on the
        hot path — it must be non-blocking (no I/O, no ``await``).

        Unlike :meth:`trace`, this method returns ``None``; the caller is
        responsible for the session lifecycle (``session_start`` / ``session_end``
        are handled by ``TraceStreamSender``).

        Args:
            script:  Path to the Python script to run.
            root:    Project root for static-graph ID resolution.
            options: Tracing configuration (line events, event cap).
            sink:    Non-blocking callable receiving each ``TraceEvent``.

        Raises:
            TraceCapExceeded: if ``options.max_events`` is set and reached.
        """
        tracer = self._build_tracer(root, options, sink=sink)
        tracer.run(script)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_tracer(
        self,
        root: Path,
        options: TraceOptions,
        *,
        sink: Callable[[TraceEvent], None] | None = None,
    ) -> Tracer:
        """Build and return a configured :class:`Tracer` for *root*."""
        from grackle.adapters import registry
        from grackle.adapters.base import ParseOptions

        static_adapter = registry.get_static("python")
        if static_adapter is None:
            raise RuntimeError("Python static adapter not registered; cannot resolve node IDs")

        graph = static_adapter.parse(root, ParseOptions())
        resolver = NodeResolver(root, graph)
        return Tracer(resolver, options, sink=sink)
