"""Node process lifecycle + V8 Inspector driving for the Node/V8 runtime adapter.

This is the impure, Node-touching half of ADR-0022 (the pure halves are
``profile_reconstruct``, ``coverage_poll``, and ``node_resolution``). It owns:

1. **Spawn** ``node --inspect-brk=127.0.0.1:0 [--experimental-strip-types]
   bootstrap.mjs <abs script>`` via ``asyncio.create_subprocess_exec`` (spawn
   semantics, cross-OS). ``--inspect-brk`` guarantees we attach before any user
   code runs; port ``0`` lets the OS pick a free port, parsed from the
   ``Debugger listening on ws://127.0.0.1:<port>/<uuid>`` stderr line.
2. **Attach** a :class:`~grackle.node_runtime.cdp_client.CDPClient`, enable the
   needed domains, **start the profiler/coverage**, then release the inspect-brk
   pause with ``Runtime.runIfWaitingForDebugger`` so collection starts at the
   first user frame.
3. **Lifecycle** — race the ``\\x00GRACKLE_DONE`` stderr sentinel (emitted by
   ``bootstrap.mjs`` after the target's top-level evaluation) against process
   death and a wall-clock timeout. ``bootstrap.mjs`` holds the loop open after
   DONE so we have a window to ``Profiler.stop`` and collect before terminating
   the process.

Two entry points map to the two delivery channels:

- :func:`run_sampling` — CPU sampling profiler → :func:`profile_reconstruct.reconstruct`
  → a faithful ``call``/``return`` stream (``trace()`` → ``--connect`` replay / ``-o``).
- :func:`run_coverage` — precise-coverage polling → :mod:`coverage_poll` → coarse
  live ``call`` events to a sink (``trace_streaming()`` → ``--stream``).
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from grackle.adapters.base import TraceCapExceeded
from grackle.node_runtime import capability, profile_reconstruct
from grackle.node_runtime.cdp_client import CDPError
from grackle.node_runtime.cdp_client import connect as cdp_connect
from grackle.node_runtime.coverage_poll import (
    OffsetLineMap,
    coverage_event,
    diff_coverage,
    normalize_precise_coverage,
)
from grackle.node_runtime.errors import NodeRuntimeError
from grackle.node_runtime.node_resolution import UNRESOLVED

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Mapping

    from grackle.adapters.base import TraceEvent, TraceOptions
    from grackle.node_runtime.coverage_poll import CoverageDelta, CoverageEntry, CoverageKey
    from grackle.node_runtime.node_resolution import NodeResolver

# Seconds to wait for "Debugger listening" before declaring the spawn failed.
_ATTACH_TIMEOUT_S = 15.0
# Overall wall-clock cap on a single traced run.
_RUN_TIMEOUT_S = 120.0
# Coverage poll cadence (ADR-0022).
_POLL_INTERVAL_S = 0.25
# Grace period for the process to exit after SIGTERM before SIGKILL.
_TERMINATE_TIMEOUT_S = 5.0
# Per-command bound on CDP calls issued AFTER user code is running (Profiler.stop,
# takePreciseCoverage): a synchronously-wedged V8 isolate never answers, so without
# this the await would hang forever even though the socket stays open.
_CDP_CMD_TIMEOUT_S = 30.0
# Chunk size for draining the subprocess stdout/stderr pipes. We read fixed-size
# chunks rather than lines so a single >64 KiB unbroken line cannot blow the
# asyncio StreamReader line limit (which would kill the drainer and deadlock Node).
_READ_CHUNK = 65536
# V8 sampling interval (microseconds). Finer than the 1 ms default for richer
# flames on short scripts; still cheap.
_SAMPLING_INTERVAL_US = 200

_LISTENING_RE = re.compile(r"Debugger listening on (ws://\S+)")
_DONE_MARKER = "\x00GRACKLE_DONE"
_ERROR_MARKER = "\x00GRACKLE_ERROR"


def _bootstrap_path() -> Path:
    return Path(__file__).resolve().parent / "bootstrap.mjs"


def _build_argv(script: Path) -> list[str]:
    node = capability.node_executable()
    version = capability.node_version()
    if node is None or version is None:
        # Should be unreachable — the CLI gate runs first — but never crash.
        raise NodeRuntimeError(capability.remediation_message())
    argv = [node, "--inspect-brk=127.0.0.1:0"]
    if capability.needs_strip_types_flag(version):
        argv.append("--experimental-strip-types")
    argv.append(str(_bootstrap_path()))
    argv.append(str(script.resolve()))
    return argv


def _make_resolve(resolver: NodeResolver) -> Callable[[Mapping[str, Any]], str | None]:
    """Adapt a V8 callFrame dict to the resolver's ``resolve_frame`` signature."""

    def resolve(call_frame: Mapping[str, Any]) -> str | None:
        url = str(call_frame.get("url", ""))
        line_number = call_frame.get("lineNumber", -1)
        line = line_number + 1 if isinstance(line_number, int) and line_number >= 0 else None
        function_name = call_frame.get("functionName") or None
        return resolver.resolve_frame(url, line, function_name)

    return resolve


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


async def run_sampling(
    script: Path,
    root: Path,
    resolver: NodeResolver,
    options: TraceOptions,
) -> list[TraceEvent]:
    """Trace *script* with the CPU sampling profiler; return a call/return stream."""
    profile: Mapping[str, Any] = {}
    async with _spawn_and_attach(script) as session:
        cdp = session.cdp
        await cdp.send("Profiler.enable")
        await cdp.send("Profiler.setSamplingInterval", {"interval": _SAMPLING_INTERVAL_US})
        await cdp.send("Profiler.start")
        await cdp.send("Runtime.runIfWaitingForDebugger")
        outcome = await session.wait_for_done(_RUN_TIMEOUT_S)
        with contextlib.suppress(CDPError):
            result = await cdp.send("Profiler.stop", timeout=_CDP_CMD_TIMEOUT_S)
            profile = result.get("profile") or {}

    events = profile_reconstruct.reconstruct(profile, _make_resolve(resolver))
    if session.error_text:
        events.append(_exception_event(resolver, script, root, session.error_text, events))
    if not events and outcome != "done":
        # The isolate is gone (process.exit()/crash) or never finished within the
        # limit — the sampling profile lives in the isolate and can only be read
        # via Profiler.stop while attached, so there is nothing to recover. Make
        # this explicit rather than silently returning an empty trace.
        raise NodeRuntimeError(
            "the target exited before a sampling profile could be collected "
            "(it likely called process.exit(), crashed, or ran past the "
            f"{_RUN_TIMEOUT_S:.0f}s limit). Let the script return normally, or use "
            "--stream for live coverage."
        )
    _enforce_cap(events, options)
    return events


async def run_coverage(
    script: Path,
    root: Path,
    resolver: NodeResolver,
    options: TraceOptions,
    sink: Callable[[TraceEvent], None],
) -> None:
    """Trace *script* with precise-coverage polling; route coarse live events to *sink*."""
    cap = options.max_events
    emitted = 0
    prev: dict[CoverageKey, CoverageEntry] = {}
    # url -> offset→line map, read lazily from disk (no Debugger.enable needed:
    # enabling the Debugger domain closes the inspector when the script finishes).
    line_maps: dict[str, OffsetLineMap | None] = {}

    async with _spawn_and_attach(script) as session:
        cdp = session.cdp
        await cdp.send("Profiler.enable")
        await cdp.send("Profiler.startPreciseCoverage", {"callCount": True, "detailed": True})
        await cdp.send("Runtime.runIfWaitingForDebugger")

        async def poll() -> None:
            nonlocal prev, emitted
            try:
                result = await cdp.send("Profiler.takePreciseCoverage", timeout=_CDP_CMD_TIMEOUT_S)
            except CDPError:
                return  # socket closed (process exited) or wedged — stop reading
            curr = normalize_precise_coverage(result.get("result") or [])
            ts_ns = time.monotonic_ns()
            for delta in diff_coverage(prev, curr):
                node_id = _resolve_coverage_delta(resolver, line_maps, delta)
                if node_id is None:
                    continue
                if cap is not None and emitted >= cap:
                    raise TraceCapExceeded(
                        f"trace event cap of {cap} reached; "
                        "set --max-events higher or omit it to disable"
                    )
                emitted += 1
                sink(coverage_event(node_id, delta["delta"], ts_ns))
            prev = curr

        deadline = time.monotonic() + _RUN_TIMEOUT_S
        while True:
            outcome = await session.wait_for_done(_POLL_INTERVAL_S)
            await poll()
            if outcome in ("done", "exited") or time.monotonic() >= deadline:
                break
        with contextlib.suppress(CDPError):
            await cdp.send("Profiler.stopPreciseCoverage", timeout=_CDP_CMD_TIMEOUT_S)

    # Count the trailing exception event against the cap too (parity with the
    # sampling path, where the exception event is included in _enforce_cap).
    if session.error_text and (cap is None or emitted < cap):
        sink(_exception_event(resolver, script, root, session.error_text, []))


# ---------------------------------------------------------------------------
# Spawn + attach + lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _spawn_and_attach(script: Path) -> AsyncIterator[_NodeSession]:
    """Spawn Node under ``--inspect-brk`` and attach a CDP client.

    Yields a :class:`_NodeSession` with ``.cdp`` set; on exit closes the CDP
    socket and terminates the Node process (it is held alive by ``bootstrap.mjs``
    after the script finishes).
    """
    argv = _build_argv(script)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise NodeRuntimeError(f"failed to spawn node: {exc}") from exc

    session = _NodeSession(proc)
    session.start_readers()
    try:
        cdp_url = await session.wait_for_listening(_ATTACH_TIMEOUT_S)
    except NodeRuntimeError:
        await session.terminate()
        raise

    try:
        async with cdp_connect(cdp_url) as cdp:
            session.cdp = cdp
            await cdp.send("Runtime.enable")
            yield session
    except (NodeRuntimeError, TraceCapExceeded):
        # NodeRuntimeError is already typed; TraceCapExceeded must pass through so
        # the CLI's cap handling (and the --stream tee file write) still works.
        raise
    except Exception as exc:
        # Any CDP / socket / websockets / transport failure becomes a clean typed
        # error — ADR-0022: degrade with a clear message, never a traceback.
        raise NodeRuntimeError(f"Node trace failed over CDP: {exc}") from exc
    finally:
        await session.terminate()


class _NodeSession:
    """Owns the spawned Node process: stderr/stdout readers and end-of-run signals."""

    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self._proc = proc
        # Set inside _spawn_and_attach once the CDP socket is open.
        self._cdp: Any = None
        loop = asyncio.get_running_loop()
        self._listening: asyncio.Future[Any] = loop.create_future()
        self._done = asyncio.Event()
        self._exited = asyncio.Event()
        self.error_text: str | None = None
        self._stderr_tail: list[str] = []
        self._tasks: list[asyncio.Task[Any]] = []

    @property
    def cdp(self) -> Any:
        if self._cdp is None:  # pragma: no cover — programming error
            raise NodeRuntimeError("CDP client accessed before attach")
        return self._cdp

    @cdp.setter
    def cdp(self, value: Any) -> None:
        self._cdp = value

    # -- readers -------------------------------------------------------

    def start_readers(self) -> None:
        self._tasks.append(asyncio.ensure_future(self._read_stderr()))
        self._tasks.append(asyncio.ensure_future(self._drain_stdout()))
        self._tasks.append(asyncio.ensure_future(self._watch_exit()))

    async def _read_stderr(self) -> None:
        # Read fixed-size chunks and split on "\n" ourselves rather than using
        # `async for line in stream`, whose 64 KiB line limit would raise and kill
        # this task on a single over-long line — losing the DONE/ERROR sentinels
        # that follow it (bootstrap.mjs bounds its own lines, but user stderr may
        # not).
        stream = self._proc.stderr
        if stream is None:  # pragma: no cover — we always pipe stderr
            return
        buffer = ""
        while True:
            chunk = await stream.read(_READ_CHUNK)
            if not chunk:
                break
            buffer += chunk.decode("utf-8", "replace")
            *lines, buffer = buffer.split("\n")
            for line in lines:
                self._handle_stderr_line(line.rstrip("\r"))
        if buffer:
            self._handle_stderr_line(buffer.rstrip("\r"))

    def _handle_stderr_line(self, line: str) -> None:
        if not self._listening.done():
            match = _LISTENING_RE.search(line)
            if match is not None:
                self._listening.set_result(match.group(1))
                return
        # Markers are line prefixes written by bootstrap.mjs; match by prefix
        # (not substring) and test ERROR before DONE so an error message can
        # never be misclassified as DONE.
        if line.startswith(_ERROR_MARKER):
            self.error_text = line[len(_ERROR_MARKER) :].strip()
        elif line.startswith(_DONE_MARKER):
            self._done.set()
        else:
            # Keep a small tail for spawn-failure diagnostics.
            self._stderr_tail.append(line)
            if len(self._stderr_tail) > 50:
                self._stderr_tail.pop(0)

    async def _drain_stdout(self) -> None:
        # Chunked read (not line-oriented) so a long unbroken stdout line cannot
        # blow the StreamReader line limit and stop us draining — which would fill
        # the OS pipe and deadlock Node on its next write().
        stream = self._proc.stdout
        if stream is None:  # pragma: no cover — we always pipe stdout
            return
        while await stream.read(_READ_CHUNK):
            pass  # discard — keeps the pipe from filling and blocking Node

    async def _watch_exit(self) -> None:
        with contextlib.suppress(Exception):
            await self._proc.wait()
        self._exited.set()

    # -- lifecycle waits ----------------------------------------------

    async def wait_for_listening(self, timeout: float) -> str:
        """Return the inspector ws:// URL, or raise if Node dies / times out first."""
        racers: list[asyncio.Future[Any]] = [self._listening, self._exited_task()]
        finished = await _race(racers, timeout)
        if self._listening in finished and not self._listening.cancelled():
            return str(self._listening.result())
        detail = " ".join(self._stderr_tail[-10:]).strip()
        raise NodeRuntimeError(
            "node inspector did not become ready" + (f": {detail}" if detail else " (timed out)")
        )

    async def wait_for_done(self, timeout: float) -> str:
        """Race the DONE sentinel vs. process exit vs. *timeout*.

        Returns ``"done"`` (sentinel seen), ``"exited"`` (process died first), or
        ``"timeout"``.
        """
        done_task = asyncio.ensure_future(self._done.wait())
        exit_task = asyncio.ensure_future(self._exited.wait())
        racers: list[asyncio.Future[Any]] = [done_task, exit_task]
        finished = await _race(racers, timeout)
        if done_task in finished:
            return "done"
        if exit_task in finished:
            return "exited"
        return "timeout"

    def _exited_task(self) -> asyncio.Future[Any]:
        return asyncio.ensure_future(self._exited.wait())

    async def terminate(self) -> None:
        """Terminate the Node process and cancel reader tasks (idempotent)."""
        if self._proc.returncode is None:
            with contextlib.suppress(ProcessLookupError, OSError):
                self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), _TERMINATE_TIMEOUT_S)
            except TimeoutError:
                with contextlib.suppress(ProcessLookupError, OSError):
                    self._proc.kill()
                with contextlib.suppress(Exception):
                    await self._proc.wait()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks.clear()


async def _race(futures: list[asyncio.Future[Any]], timeout: float) -> set[asyncio.Future[Any]]:
    """Await until one of *futures* completes or *timeout* elapses; cancel the rest.

    Never cancels a non-Task future the caller still owns (e.g. ``_listening``);
    only the transient tasks created for the race are cancelled.
    """
    done, pending = await asyncio.wait(
        futures, timeout=timeout, return_when=asyncio.FIRST_COMPLETED
    )
    for fut in pending:
        if isinstance(fut, asyncio.Task):
            fut.cancel()
    return done


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_coverage_delta(
    resolver: NodeResolver,
    line_maps: dict[str, OffsetLineMap | None],
    delta: CoverageDelta,
) -> str | None:
    line_map = _line_map_for_url(resolver, line_maps, delta["url"])
    line = line_map.line_of(delta["start_offset"]) if line_map is not None else None
    return resolver.resolve_frame(delta["url"], line, delta["function_name"] or None)


def _line_map_for_url(
    resolver: NodeResolver,
    line_maps: dict[str, OffsetLineMap | None],
    url: str,
) -> OffsetLineMap | None:
    """Build + cache a script's offset→line map by reading its source from disk.

    Type-stripping preserves line boundaries, so the on-disk ``.ts`` lines match
    what V8 executed. Returns ``None`` for non-project URLs or read failures; the
    resolver then falls back to name/file matching (``functionName`` from coverage
    is usually enough on its own).
    """
    if url in line_maps:
        return line_maps[url]
    line_map: OffsetLineMap | None = None
    path = resolver.source_path(url)
    if path is not None:
        try:
            # Read raw bytes (NOT read_text): text mode collapses CRLF → LF, which
            # shifts every offset relative to V8's (V8 keeps \r\n), mis-mapping
            # offsets→lines on CRLF source. Strip a leading BOM (V8 strips it too)
            # so offsets stay aligned.
            raw = path.read_bytes()
            if raw.startswith(b"\xef\xbb\xbf"):
                raw = raw[3:]
            line_map = OffsetLineMap(raw.decode("utf-8", "replace"))
        except OSError:
            line_map = None
    line_maps[url] = line_map
    return line_map


def _exception_event(
    resolver: NodeResolver,
    script: Path,
    root: Path,
    message: str,
    events: list[TraceEvent],
) -> TraceEvent:
    """Build a trailing ``exception`` event from a target-script error.

    Attributed to the script's file node; timestamped at/after the last event so
    it sorts last in the stream.
    """
    node_id = resolver.resolve_frame(script.resolve().as_uri(), None, None) or UNRESOLVED
    ts_ns = events[-1]["ts_ns"] if events else 0
    return {
        "event": "exception",
        "node_id": node_id,
        "ts_ns": ts_ns,
        "thread_id": 0,
        "frame_depth": 0,
        "metadata": {"exc_type": _js_error_type(message), "message": message[:1000]},
    }


def _js_error_type(message: str) -> str:
    """Extract the JS error class from a ``bootstrap.mjs`` error string.

    ``err.stack`` starts with ``"TypeError: ..."`` / ``"RangeError: ..."`` etc.;
    take that leading class name (mirroring the Python tracer's ``exc_type``).
    Falls back to ``"Error"`` for thrown non-Errors or unrecognisable text.
    """
    head = message.split(":", 1)[0].strip()
    if head.endswith("Error") and " " not in head and head.isidentifier():
        return head
    return "Error"


def _enforce_cap(events: list[TraceEvent], options: TraceOptions) -> None:
    cap = options.max_events
    if cap is not None and len(events) > cap:
        raise TraceCapExceeded(
            f"trace event cap of {cap} reached ({len(events)} reconstructed); "
            "set --max-events higher or omit it to disable"
        )
