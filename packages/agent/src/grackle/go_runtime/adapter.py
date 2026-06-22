"""GoRuntimeAdapter — Go runtime tracer via coverage instrumentation (ADR-0023).

Implements the ``RuntimeAdapter`` Protocol (ADR-0003) for language ``"go"``,
emitting the same ``TraceEvent`` schema as the Python and Node tracers so the
entire Phase 6–8 pipeline works on Go events unchanged.

Channel shape (ADR-0022 "exact counts, coarse events" contract):

- :meth:`trace` → ``go build -cover`` → run → ``go tool covdata textfmt`` →
  one ``call`` event per executed function, ``metadata.count`` = entry-block
  call count, ``frame_depth = 0``.
- :meth:`trace_streaming` → unsupported; raises :class:`GoRuntimeError` with
  a clear remediation message.

Registered unconditionally (discoverable via ``grackle languages``);
:meth:`capabilities` reports ``runtime_tracing`` only when a Go toolchain
>= 1.20 is present.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from grackle.adapters.base import Capabilities, new_trace_event
from grackle.go_runtime import capability
from grackle.go_runtime.errors import GoRuntimeError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from grackle.adapters.base import TraceEvent, TraceOptions


class GoRuntimeAdapter:
    """Runtime adapter that traces Go programs via coverage instrumentation."""

    language: str = "go"
    extensions: tuple[str, ...] = (".go",)

    def runtime_unavailable_reason(self, script: Path) -> str | None:
        """Reject ``_test.go`` inputs and a missing/old Go toolchain; else None."""
        if script.name.endswith("_test.go"):
            return (
                f"{script.name}: tracing Go test files is not supported — "
                "use `go test -cover` directly. Pass a non-test .go file "
                "from a `package main` directory instead."
            )
        if not capability.go_runtime_available():
            return capability.remediation_message()
        return None

    def capabilities(self) -> Capabilities:
        return Capabilities(runtime_tracing=capability.go_runtime_available())

    def trace(self, script: Path, root: Path, options: TraceOptions) -> Iterator[TraceEvent]:
        """Trace *script*'s package with coverage instrumentation.

        Builds the package, runs the binary under ``GOCOVERDIR``, parses the
        textfmt output, and emits one ``call`` event per executed function with
        ``metadata.count`` = entry-block call count.

        Raises:
            TraceCapExceeded: if ``options.max_events`` is exceeded.
            GoRuntimeError: on build/run/covdata failure (caught by the CLI).
        """
        from grackle.adapters import registry
        from grackle.go_runtime import covdata_parse, toolchain
        from grackle.go_runtime.resolution import GoResolver

        # Build the resolver from the static graph.
        try:
            graph = registry.build_static_graph(
                "go",
                root,
                missing_message=("Go static adapter not registered; cannot resolve node IDs"),
            )
        except LookupError as exc:
            raise GoRuntimeError(str(exc)) from exc
        resolver = GoResolver(root, graph)

        # Run the full build → run → covdata pipeline.
        text = toolchain.run(script, root)
        blocks = covdata_parse.parse_textfmt(text)

        # Fold blocks to per-function entry-block call counts (decision #1 in
        # ADR-0023): the entry block (lowest start_line) executes exactly once
        # per call, so its count IS the function call count.
        per_node: dict[str, tuple[int, int]] = {}  # node_id -> (min_line, count)
        for b in blocks:
            nid = resolver.resolve_block(b["import_path"], b["start_line"])
            if nid is None:
                continue
            cur = per_node.get(nid)
            if cur is None or b["start_line"] < cur[0]:
                per_node[nid] = (b["start_line"], b["count"])

        # Emit one event per executed function (count > 0).
        ts_ns = time.monotonic_ns()
        thread_id = threading.get_ident()
        events: list[TraceEvent] = []
        for nid, (_, count) in per_node.items():
            if count == 0:
                continue
            events.append(new_trace_event("call", nid, ts_ns, thread_id, 0, {"count": count}))

        # Post-hoc cap check (finite list; mirrors launcher._enforce_cap form).
        cap = options.max_events
        if cap is not None and len(events) > cap:
            from grackle.adapters.base import TraceCapExceeded

            raise TraceCapExceeded(
                f"trace event cap of {cap} reached; Go trace produced {len(events)} events."
            )

        yield from events

    def trace_streaming(
        self,
        script: Path,
        root: Path,
        options: TraceOptions,
        sink: Callable[[TraceEvent], None],
    ) -> None:
        """Not supported — Go coverage requires program completion.

        Raises:
            GoRuntimeError: always; use ``grackle trace <pkg> -o file.jsonl``
                instead, then load the file.
        """
        raise GoRuntimeError(
            "Go runtime supports completed-trace mode only; --stream is not "
            "available for Go. Use 'grackle trace <pkg> -o file.jsonl' to "
            "capture a trace, then load the file in the frontend."
        )
