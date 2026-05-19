"""sys.monitoring-based Python runtime tracer (Python 3.12+, PEP 669).

The ``Tracer`` class registers itself with ``sys.monitoring``, runs the target
script via ``runpy.run_path``, collects ``TraceEvent`` dicts, and returns them.
Callbacks are kept as short as possible so tracer overhead stays low.

Design notes (ADR-0013):
- Uses ``sys.monitoring`` (PEP 669) rather than ``sys.settrace``/``sys.setprofile``
  for ~20× lower overhead on Python 3.12+.
- Only ``PY_START``, ``PY_RETURN``, ``RAISE``, and optionally ``LINE`` events are
  subscribed; consumers of the events file may add more in future phases.
- Non-project code objects return ``sys.monitoring.DISABLE`` on first call so they
  are never probed again — this eliminates stdlib/site-packages overhead.
- ``co_firstlineno`` is the start line of the enclosing function definition.
  The ``NodeResolver`` uses this for an exact-match lookup (ADR-0013 §3).
"""

from __future__ import annotations

import contextlib
import sys
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
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
    """

    def __init__(self, resolver: NodeResolver, options: TraceOptions) -> None:
        self._resolver = resolver
        self._options = options
        self._events: list[TraceEvent] = []
        # Per-thread call-stack depth counters.
        self._depth: dict[int, int] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, script: Path) -> list[TraceEvent]:
        """Execute *script* under the tracer and return collected events.

        Raises:
            TraceCapExceeded: if ``options.max_events`` is set and reached.
        """
        import runpy

        self._start()
        try:
            runpy.run_path(str(script), run_name="__main__")
        except TraceCapExceeded:
            raise
        except Exception:
            # The script raised an exception — that's fine; we collected
            # the RAISE event and continue normally.
            pass
        finally:
            self._stop()
        return self._events

    # ------------------------------------------------------------------
    # sys.monitoring wiring
    # ------------------------------------------------------------------

    def _start(self) -> None:
        mon = sys.monitoring
        mon.use_tool_id(_GRACKLE_TOOL_ID, _TOOL_NAME)

        event_set = mon.events.PY_START | mon.events.PY_RETURN | mon.events.RAISE
        if self._options.include_line_events:
            event_set |= mon.events.LINE
        mon.set_events(_GRACKLE_TOOL_ID, event_set)

        mon.register_callback(_GRACKLE_TOOL_ID, mon.events.PY_START, self._on_call)
        mon.register_callback(_GRACKLE_TOOL_ID, mon.events.PY_RETURN, self._on_return)
        mon.register_callback(_GRACKLE_TOOL_ID, mon.events.RAISE, self._on_raise)
        if self._options.include_line_events:
            mon.register_callback(_GRACKLE_TOOL_ID, mon.events.LINE, self._on_line)

    def _stop(self) -> None:
        # Order matters: clear events first so no more callbacks fire, then
        # unregister callbacks (pass None), then release the tool ID. Without
        # this, `free_tool_id` alone leaves callbacks registered and they keep
        # firing for the rest of the process lifetime — polluting subsequent
        # pytest tests and crashing during interpreter shutdown when
        # ``sys.meta_path`` is None.
        with contextlib.suppress(Exception):
            mon = sys.monitoring
            mon.set_events(_GRACKLE_TOOL_ID, 0)
            mon.register_callback(_GRACKLE_TOOL_ID, mon.events.PY_START, None)
            mon.register_callback(_GRACKLE_TOOL_ID, mon.events.PY_RETURN, None)
            mon.register_callback(_GRACKLE_TOOL_ID, mon.events.RAISE, None)
            if self._options.include_line_events:
                mon.register_callback(_GRACKLE_TOOL_ID, mon.events.LINE, None)
            mon.free_tool_id(_GRACKLE_TOOL_ID)

    # ------------------------------------------------------------------
    # Callbacks (hot path — keep allocation/work minimal)
    # ------------------------------------------------------------------

    def _on_call(self, code: CodeType, offset: int) -> object:
        if not self._resolver.is_project_file(code.co_filename):
            return sys.monitoring.DISABLE

        tid = threading.get_ident()
        depth = self._depth.get(tid, 0)
        self._depth[tid] = depth + 1
        node_id = self._resolver.resolve(code.co_filename, code.co_firstlineno)
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
        node_id = self._resolver.resolve(code.co_filename, code.co_firstlineno)
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

    def _on_raise(self, code: CodeType, offset: int, exception: BaseException) -> None:
        # RAISE does NOT support returning DISABLE.
        if not self._resolver.is_project_file(code.co_filename):
            return

        tid = threading.get_ident()
        depth = self._depth.get(tid, 0)
        node_id = self._resolver.resolve(code.co_filename, code.co_firstlineno)
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
        node_id = self._resolver.resolve(code.co_filename, code.co_firstlineno)
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
        if cap is not None and len(self._events) >= cap:
            raise TraceCapExceeded(
                f"trace event cap of {cap} reached; set TraceOptions.max_events=None to disable"
            )
        self._events.append(event)
