"""sys.monitoring-based Python runtime tracer (Python 3.12+, PEP 669).

The ``Tracer`` class registers itself with ``sys.monitoring``, runs the target
script via ``runpy.run_path``, collects ``TraceEvent`` dicts, and returns them.
Callbacks are kept as short as possible so tracer overhead stays low.

Design notes (ADR-0013):
- Uses ``sys.monitoring`` (PEP 669) rather than ``sys.settrace``/``sys.setprofile``
  for ~20× lower overhead on Python 3.12+.
- Subscribes to ``PY_START`` (call), ``PY_RETURN`` (normal return), ``PY_UNWIND``
  (frame exit via exception — depth bookkeeping only, no event emitted), ``RAISE``
  (exception observation), and optionally ``LINE``.
- Non-project code objects return ``sys.monitoring.DISABLE`` on first call so they
  are never probed again — this eliminates stdlib/site-packages overhead.
- ``co_firstlineno`` is the start line of the enclosing function definition
  (or first decorator if any). The ``NodeResolver`` and the static parser
  agree on this rule (see ``python_parser.visitors``).
- ``PY_YIELD``/``PY_RESUME`` (generator suspension/resume) are intentionally
  NOT subscribed — frame_depth accounting for generators is non-trivial and
  the static graph does not distinguish suspended frames. The current
  ``frame_depth`` value for code inside a generator may therefore drift by
  one until the generator returns; documented as a known limitation in
  ADR-0013.
"""

from __future__ import annotations

import contextlib
import sys
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from types import CodeType

    from grackle.python_runtime.node_resolution import NodeResolver

from grackle.adapters.base import TraceCapExceeded, TraceEvent, TraceOptions

# Tool ID used with sys.monitoring. IDs 0-2 are reserved (debugger, coverage,
# profiler). 3 is the first freely usable ID.
_GRACKLE_TOOL_ID = 3
_TOOL_NAME = "grackle"


class Tracer:
    """Collect trace events from a script using sys.monitoring.

    Args:
        resolver: Pre-built node resolver for the project.
        options:  Trace configuration (line events, event cap).
        sink:     Optional callable invoked with each ``TraceEvent`` instead
                  of appending to an internal list.  When provided, the list
                  returned by :meth:`run` will be empty — the caller is
                  responsible for consuming events via the sink.  The sink
                  must be non-blocking (it is called on the hot path inside
                  ``sys.monitoring`` callbacks).
    """

    def __init__(
        self,
        resolver: NodeResolver,
        options: TraceOptions,
        *,
        sink: Callable[[TraceEvent], None] | None = None,
    ) -> None:
        self._resolver = resolver
        self._options = options
        self._events: list[TraceEvent] = []
        # Per-thread call-stack depth counters.
        self._depth: dict[int, int] = {}
        # Optional hot-path sink.  When set, events are routed to the sink
        # instead of self._events.
        self._sink = sink
        # Event count decoupled from len(self._events) so the cap check works
        # correctly when a custom sink is active (self._events stays empty).
        self._count: int = 0
        # If the sink raises, the exception propagates through sys.monitoring
        # into the script and is caught by run()'s BaseException handler.
        # We store it here so it can be re-raised after _stop() completes.
        self._sink_exc: BaseException | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, script: Path) -> list[TraceEvent]:
        """Execute *script* under the tracer and return collected events.

        Catches ``BaseException`` (not just ``Exception``) so that scripts
        which call ``sys.exit()`` (raises ``SystemExit``) or are interrupted
        with Ctrl-C (raises ``KeyboardInterrupt``) still get a clean
        teardown and a populated event list. ``TraceCapExceeded`` is
        re-raised because callers need to know the cap fired.  If the sink
        raises, the exception propagates out after ``_stop()`` completes.

        Raises:
            TraceCapExceeded: if ``options.max_events`` is set and reached.
            BaseException: if the sink raises (re-raised after teardown).
        """
        import runpy

        self._start()
        try:
            runpy.run_path(str(script), run_name="__main__")
        except TraceCapExceeded:
            raise
        except BaseException:
            # The script raised — SystemExit, KeyboardInterrupt, or any
            # other exception. The RAISE callback already captured it; we
            # just need to fall through to ``finally`` so _stop() runs.
            # (Sink exceptions also arrive here — we re-raise them below.)
            pass
        finally:
            self._stop()
        if self._sink_exc is not None:
            raise self._sink_exc
        return self._events

    # ------------------------------------------------------------------
    # sys.monitoring wiring
    # ------------------------------------------------------------------

    def _start(self) -> None:
        mon = sys.monitoring
        mon.use_tool_id(_GRACKLE_TOOL_ID, _TOOL_NAME)

        event_set = (
            mon.events.PY_START | mon.events.PY_RETURN | mon.events.PY_UNWIND | mon.events.RAISE
        )
        if self._options.include_line_events:
            event_set |= mon.events.LINE
        mon.set_events(_GRACKLE_TOOL_ID, event_set)

        mon.register_callback(_GRACKLE_TOOL_ID, mon.events.PY_START, self._on_call)
        mon.register_callback(_GRACKLE_TOOL_ID, mon.events.PY_RETURN, self._on_return)
        mon.register_callback(_GRACKLE_TOOL_ID, mon.events.PY_UNWIND, self._on_unwind)
        mon.register_callback(_GRACKLE_TOOL_ID, mon.events.RAISE, self._on_raise)
        if self._options.include_line_events:
            mon.register_callback(_GRACKLE_TOOL_ID, mon.events.LINE, self._on_line)

    def _stop(self) -> None:
        # Order matters: clear events first so no more callbacks fire, then
        # unregister callbacks (pass None), then release the tool ID. Without
        # this, ``free_tool_id`` alone leaves callbacks registered and they
        # keep firing for the rest of the process lifetime — polluting
        # subsequent pytest tests and crashing during interpreter shutdown
        # when ``sys.meta_path`` is None.
        #
        # Suppress the narrow set of exceptions sys.monitoring can raise: a
        # ``ValueError`` if the tool ID is not currently in use, or an
        # ``OSError`` propagated from a callback. Broader exceptions should
        # surface so we don't mask real bugs in the teardown path.
        with contextlib.suppress(ValueError, OSError):
            mon = sys.monitoring
            mon.set_events(_GRACKLE_TOOL_ID, 0)
            mon.register_callback(_GRACKLE_TOOL_ID, mon.events.PY_START, None)
            mon.register_callback(_GRACKLE_TOOL_ID, mon.events.PY_RETURN, None)
            mon.register_callback(_GRACKLE_TOOL_ID, mon.events.PY_UNWIND, None)
            mon.register_callback(_GRACKLE_TOOL_ID, mon.events.RAISE, None)
            if self._options.include_line_events:
                mon.register_callback(_GRACKLE_TOOL_ID, mon.events.LINE, None)
            mon.free_tool_id(_GRACKLE_TOOL_ID)

    # ------------------------------------------------------------------
    # Callbacks (hot path — keep allocation/work minimal)
    # ------------------------------------------------------------------

    def _on_call(self, code: CodeType, offset: int) -> object:
        # PY_START is the only event whose callback may return
        # ``sys.monitoring.DISABLE`` — doing so for non-project code objects
        # silences them for the rest of the process, eliminating per-frame
        # overhead for stdlib/site-packages.
        if not self._resolver.is_project_file(code.co_filename):
            return sys.monitoring.DISABLE

        tid = threading.get_ident()
        depth = self._depth.get(tid, 0)
        self._depth[tid] = depth + 1
        node_id = self._resolver.resolve(code.co_filename, code.co_firstlineno, code.co_name)
        self._emit(
            {
                "event": "call",
                "node_id": node_id,
                "ts_ns": time.monotonic_ns(),
                "thread_id": tid,
                "frame_depth": depth,
                "metadata": {},
            }
        )
        return None

    def _on_return(self, code: CodeType, offset: int, retval: object) -> None:
        # PY_RETURN does NOT support returning DISABLE (only PY_START does).
        if not self._resolver.is_project_file(code.co_filename):
            return

        tid = threading.get_ident()
        depth = max(0, self._depth.get(tid, 1) - 1)
        self._depth[tid] = depth
        node_id = self._resolver.resolve(code.co_filename, code.co_firstlineno, code.co_name)
        self._emit(
            {
                "event": "return",
                "node_id": node_id,
                "ts_ns": time.monotonic_ns(),
                "thread_id": tid,
                "frame_depth": depth,
                "metadata": {},
            }
        )

    def _on_unwind(self, code: CodeType, offset: int, exception: BaseException) -> None:
        """Frame is exiting because an exception is propagating through it.

        PY_UNWIND fires once per frame that the exception traverses on its way
        up the stack. Without subscribing to it, ``self._depth`` would only
        be decremented for the frame that originally raised (PY_RETURN never
        fires for a frame that exits via exception) — every later trace event
        on the same thread would then report a wrong (inflated) ``frame_depth``.

        We do NOT emit a separate event here: the RAISE callback already
        recorded the exception. We just need the depth bookkeeping.
        """
        # PY_UNWIND does NOT support returning DISABLE.
        if not self._resolver.is_project_file(code.co_filename):
            return

        tid = threading.get_ident()
        # Mirror _on_return's decrement so a clean return and an exception
        # unwind leave the depth counter in the same state.
        depth = max(0, self._depth.get(tid, 1) - 1)
        self._depth[tid] = depth

    def _on_raise(self, code: CodeType, offset: int, exception: BaseException) -> None:
        # RAISE does NOT support returning DISABLE.
        if not self._resolver.is_project_file(code.co_filename):
            return

        tid = threading.get_ident()
        depth = self._depth.get(tid, 0)
        node_id = self._resolver.resolve(code.co_filename, code.co_firstlineno, code.co_name)
        exc_type = type(exception).__name__
        self._emit(
            {
                "event": "exception",
                "node_id": node_id,
                "ts_ns": time.monotonic_ns(),
                "thread_id": tid,
                "frame_depth": depth,
                "metadata": {"exc_type": exc_type},
            }
        )

    def _on_line(self, code: CodeType, line_number: int) -> None:
        # LINE does NOT support returning DISABLE.
        if not self._resolver.is_project_file(code.co_filename):
            return

        tid = threading.get_ident()
        depth = self._depth.get(tid, 0)
        node_id = self._resolver.resolve(code.co_filename, code.co_firstlineno, code.co_name)
        self._emit(
            {
                "event": "line",
                "node_id": node_id,
                "ts_ns": time.monotonic_ns(),
                "thread_id": tid,
                "frame_depth": depth,
                "metadata": {"line": line_number},
            }
        )

    def _emit(self, event: TraceEvent) -> None:
        cap = self._options.max_events
        if cap is not None and self._count >= cap:
            raise TraceCapExceeded(
                f"trace event cap of {cap} reached; set TraceOptions.max_events=None to disable"
            )
        self._count += 1
        if self._sink is not None:
            try:
                self._sink(event)
            except BaseException as exc:
                # Store the first sink exception so run() can re-raise it
                # after _stop() completes.  Re-raise here so sys.monitoring
                # propagates it through the monitored code, stopping execution.
                if self._sink_exc is None:
                    self._sink_exc = exc
                raise
        else:
            self._events.append(event)
