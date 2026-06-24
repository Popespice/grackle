"""RustRuntimeAdapter — Rust runtime tracer via LLVM coverage instrumentation (ADR-0024).

Implements the ``RuntimeAdapter`` Protocol (ADR-0003) for language ``"rust"``,
emitting the same ``TraceEvent`` schema as the Python, Node, and Go tracers so
the entire Phase 6–8 pipeline works on Rust events unchanged.

Channel shape (ADR-0022 "exact counts, coarse events" contract):

- :meth:`trace` → ``RUSTFLAGS=-Cinstrument-coverage`` build → run →
  ``llvm-profdata merge`` → ``llvm-cov export --format=json`` →
  one ``call`` event per executed function, ``metadata.count`` = summed
  entry count across all monomorphisations, ``frame_depth = 0``.
- :meth:`trace_streaming` → unsupported; raises :class:`RustRuntimeError` with
  a clear remediation message.

Registered unconditionally (discoverable via ``grackle languages``);
:meth:`capabilities` reports ``runtime_tracing`` only when a Rust toolchain
with ``llvm-tools-preview`` is present.

**Monomorphisation folding:** ``llvm-cov export`` emits one ``functions[]``
entry per instantiation of a generic function (each with a distinct mangled
name). All entries sharing the same resolved node ID contribute to the same
source function, so their counts are *summed* (unlike Go, which takes the
entry-block min-line count per block). The fold key is the resolved ``node_id``,
not the raw ``(path, line)`` pair.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from grackle.adapters.base import Capabilities, new_trace_event
from grackle.rust_runtime import capability
from grackle.rust_runtime.errors import RustRuntimeError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from grackle.adapters.base import TraceEvent, TraceOptions


class RustRuntimeAdapter:
    """Runtime adapter that traces Rust programs via LLVM coverage instrumentation."""

    language: str = "rust"
    extensions: tuple[str, ...] = (".rs",)

    # Directory names that, as the *immediate* parent of a `.rs` file, indicate a
    # Cargo integration-test or benchmark target (non-binary). Deeper toolchain
    # detection (src_path bin match) handles any other non-bin input.
    _REJECT_COMPONENTS = frozenset({"tests", "benches"})

    def runtime_unavailable_reason(self, script: Path) -> str | None:
        """Reject test/bench files and a missing/incomplete Rust toolchain; else None."""
        # Cargo integration tests / benchmarks are the .rs files directly under a
        # crate's `tests/` or `benches/` directory; they compile to non-bin
        # targets. Match only the *immediate* parent directory, so an unrelated
        # ancestor that merely happens to be named `tests`/`benches` (e.g. a
        # checkout at `~/src/tests/myproj/src/main.rs`) does not wrongly reject a
        # valid `src/main.rs` entry point.
        parent = script.parent.name
        if parent in self._REJECT_COMPONENTS:
            return (
                f"{script.name}: tracing files under '{parent}/' is not supported. "
                "Pass a binary entry point (src/main.rs or src/bin/<name>.rs) instead."
            )
        if not capability.rust_runtime_available():
            return capability.remediation_message()
        return None

    def capabilities(self) -> Capabilities:
        return Capabilities(runtime_tracing=capability.rust_runtime_available())

    def trace(self, script: Path, root: Path, options: TraceOptions) -> Iterator[TraceEvent]:
        """Trace *script*'s Cargo package with LLVM coverage instrumentation.

        Builds the package (bins only into a temp target-dir), runs the binary
        under ``LLVM_PROFILE_FILE``, merges raw profiles, exports JSON via
        ``llvm-cov``, and emits one ``call`` event per executed function with
        ``metadata.count`` = summed entry count across all monomorphisations.

        Raises:
            TraceCapExceeded: if ``options.max_events`` is exceeded.
            RustRuntimeError: on build/run/merge/export failure (caught by CLI).
        """
        from grackle.adapters import registry
        from grackle.rust_runtime import llvm_cov_parse, toolchain
        from grackle.rust_runtime.resolution import RustResolver

        # Build the resolver from the Rust static graph.
        try:
            graph = registry.build_static_graph(
                "rust",
                root,
                missing_message=("Rust static adapter not registered; cannot resolve node IDs"),
            )
        except LookupError as exc:
            raise RustRuntimeError(str(exc)) from exc
        resolver = RustResolver(root, graph)

        # Run the full build → run → merge → export pipeline.
        json_text = toolchain.run(script, root)
        functions = llvm_cov_parse.parse_export(json_text)

        # Fold: sum counts per node_id across all monomorphisations and entries.
        # The fold key is the resolved node_id, not (path, line), so two
        # instantiations of the same generic function sum rather than collide.
        per_node: dict[str, int] = {}
        for fn in functions:
            nid = resolver.resolve_function(fn["path"], fn["start_line"])
            if nid is None:
                continue
            per_node[nid] = per_node.get(nid, 0) + fn["count"]

        # Emit one event per executed function (summed count > 0).
        ts_ns = time.monotonic_ns()
        thread_id = threading.get_ident()
        events: list[TraceEvent] = []
        for nid, total in per_node.items():
            if total == 0:
                continue
            events.append(new_trace_event("call", nid, ts_ns, thread_id, 0, {"count": total}))

        # Post-hoc cap check (finite list produced before yielding, like Go).
        cap = options.max_events
        if cap is not None and len(events) > cap:
            from grackle.adapters.base import TraceCapExceeded

            raise TraceCapExceeded(
                f"trace event cap of {cap} reached; Rust trace produced {len(events)} events."
            )

        yield from events

    def trace_streaming(
        self,
        script: Path,
        root: Path,
        options: TraceOptions,
        sink: Callable[[TraceEvent], None],
    ) -> None:
        """Not supported — Rust coverage requires program completion.

        Raises:
            RustRuntimeError: always; use ``grackle trace <script> -o file.jsonl``
                instead, then load the file.
        """
        raise RustRuntimeError(
            "Rust runtime supports completed-trace mode only; --stream is not "
            "available for Rust. Use 'grackle trace <script> -o file.jsonl' to "
            "capture a trace, then load the file in the frontend."
        )
