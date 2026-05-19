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
    from collections.abc import Iterator
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
        # Build the static graph so the resolver can map filenames→node IDs.
        from grackle.adapters import registry
        from grackle.adapters.base import ParseOptions

        static_adapter = registry.get_static("python")
        if static_adapter is None:
            raise RuntimeError("Python static adapter not registered; cannot resolve node IDs")

        graph = static_adapter.parse(root, ParseOptions())
        resolver = NodeResolver(root, graph)
        tracer = Tracer(resolver, options)
        events = tracer.run(script)
        yield from events
