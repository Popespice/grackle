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
import threading
from typing import TYPE_CHECKING, Any

from grackle.adapters.base import Capabilities
from grackle.node_runtime import capability
from grackle.node_runtime.errors import NodeRuntimeError
from grackle.node_runtime.node_resolution import NodeResolver

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Iterator
    from pathlib import Path

    from grackle.adapters.base import TraceEvent, TraceOptions


def _run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    """Run *coro* to completion from a synchronous caller.

    ``asyncio.run`` raises ``RuntimeError`` if a loop is already running on this
    thread. The CLI calls the adapter from a plain sync context (no loop), but to
    keep the adapter callable from an async context too — matching the loop-free
    Python adapter contract — fall back to running the coroutine on a private
    thread when a loop is already running, propagating its result/exception.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)  # no running loop — the normal CLI path

    box: list[T] = []
    err: list[BaseException] = []

    def _runner() -> None:
        try:
            box.append(asyncio.run(coro))
        except BaseException as exc:
            err.append(exc)

    thread = threading.Thread(target=_runner, name="grackle-node-trace")
    thread.start()
    thread.join()
    if err:
        raise err[0]
    return box[0]


class NodeRuntimeAdapter:
    """Runtime adapter that traces Node/V8 execution of TypeScript via CDP."""

    language: str = "typescript"
    # Extensions routable to this adapter. Only includes extensions that are
    # actually runnable; .tsx/.jsx are rejected at the gate (JSX is not supported
    # until Phase 9) so they are omitted here — advertising them as "known" leads
    # users to expect they work.
    extensions: tuple[str, ...] = (".ts", ".mts", ".cts")

    # JSX extensions: type-stripping strips type annotations but cannot transform
    # JSX. Out of scope until Phase 9 — surfaced as a clean, specific message when
    # the user passes --language typescript explicitly.
    _UNSUPPORTED_EXTENSIONS = (".tsx", ".jsx")

    def runtime_unavailable_reason(self, script: Path) -> str | None:
        """Reject JSX inputs and a missing/old Node toolchain; else None.

        Owns both gate kinds (was ``cli._UNSUPPORTED_TS_EXTENSIONS`` +
        ``cli._runtime_gate_message``) so the per-adapter knowledge lives here.
        """
        suffix = script.suffix.lower()
        if suffix in self._UNSUPPORTED_EXTENSIONS:
            return (
                f"{script.name}: TypeScript JSX ({suffix}) is not supported yet -- "
                "Node's type-stripping strips type annotations but cannot transform "
                "JSX (planned for Phase 9). Trace a plain .ts/.mts/.cts module, or "
                "pre-compile the JSX to .js first."
            )
        if not capability.node_runtime_available():
            return capability.remediation_message()
        return None

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
        events = _run_async(launcher.run_sampling(script, root, resolver, options))
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
        _run_async(launcher.run_coverage(script, root, resolver, options, sink))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_resolver(self, root: Path) -> NodeResolver:
        """Build a :class:`NodeResolver` from the TypeScript static graph for *root*."""
        from grackle.adapters import registry

        try:
            graph = registry.build_static_graph(
                "typescript",
                root,
                missing_message="TypeScript static adapter not registered; cannot resolve node IDs",
            )
        except LookupError as exc:
            raise NodeRuntimeError(str(exc)) from exc
        return NodeResolver(root, graph)
