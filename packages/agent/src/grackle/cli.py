from __future__ import annotations

import asyncio
import json
import platform
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import click
import structlog

from grackle import protocol as _protocol
from grackle import server as _server
from grackle.logging import configure_logging

if TYPE_CHECKING:
    from collections.abc import Callable

    from grackle.adapters.base import TraceEvent

# Maximum inter-event sleep when streaming a completed trace to a server.
# Mirrors server._MAX_GAP_S so --connect pacing matches --trace-source pacing.
_MAX_GAP_S = 0.25


@click.group()
def main() -> None:
    """grackle — local-first live code visualizer."""


@main.command()
def languages() -> None:
    """List supported languages registered with the adapter registry."""
    from grackle.adapters import registry  # lazy import — keeps CLI startup snappy

    click.echo(f"supported languages: {registry.supported_languages()}")


@main.command()
@click.argument(
    "root",
    default=".",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
)
@click.option(
    "--output",
    "-o",
    default=None,
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    help="Write JSON to FILE instead of stdout.",
)
@click.option("--language", "-l", default=None, help="Force parser language (skips auto-detect).")
@click.option(
    "--exclude",
    "-e",
    "patterns",
    multiple=True,
    help="Exclude glob patterns (repeatable).",
)
def parse(
    root: Path,
    output: Path | None,
    language: str | None,
    patterns: tuple[str, ...],
) -> None:
    """Parse ROOT and emit a static graph as JSON."""
    from grackle.adapters import registry
    from grackle.adapters.base import ParseOptions

    if language is not None:
        adapter = registry.get_static(language)
        if adapter is None:
            raise click.UsageError(f"no static parser registered for language: {language!r}")
        graph = adapter.parse(root, ParseOptions(exclude_patterns=patterns))
    else:
        detected = registry.detect(root)
        if not detected:
            raise click.UsageError(f"no static parser detected for project at: {root}")
        if len(detected) > 1:
            click.echo(f"detected languages: {detected}; merging polyglot graph", err=True)
            graph = registry.parse_all(root, ParseOptions(exclude_patterns=patterns))
        else:
            adapter = registry.get_static(detected[0])
            if adapter is None:  # defensive — detect() only returns registered names
                raise click.UsageError(f"no static parser registered for language: {detected[0]!r}")
            graph = adapter.parse(root, ParseOptions(exclude_patterns=patterns))
    json_str = json.dumps(graph, indent=2)

    if output is not None:
        output.write_text(json_str, encoding="utf-8")
        click.echo(
            f"wrote {len(graph['nodes'])} nodes, {len(graph['edges'])} edges → {output}",
            err=True,
        )
    else:
        click.echo(json_str)


@main.command()
@click.argument("script", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--output",
    "-o",
    default=None,
    type=click.Path(dir_okay=False, writable=True, path_type=Path),
    help=(
        "Write JSONL to FILE. Without --stream, writes after tracing completes "
        "(instead of stdout). With --stream, captures a lossless copy alongside "
        "the live WebSocket stream (tee mode)."
    ),
)
@click.option(
    "--root",
    "-r",
    default=".",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Project root for static-graph ID resolution (default: current directory).",
)
@click.option(
    "--lines",
    is_flag=True,
    default=False,
    help="Include LINE events (one per executed line). Significantly increases event volume.",
)
@click.option(
    "--max-events",
    default=None,
    type=click.IntRange(min=1),
    help=(
        "Hard cap on collected events (must be >= 1). CLI exits with an "
        "error if the cap is reached. Default: unlimited."
    ),
)
@click.option(
    "--connect",
    default=None,
    metavar="URL",
    help=(
        "After tracing completes, stream the collected events to a running "
        "grackle server at URL (e.g. ws://127.0.0.1:7878). "
        "May be combined with --output to write a file AND stream. "
        "Note: without --stream this streams a *completed* trace, not a live one."
    ),
)
@click.option(
    "--stream",
    is_flag=True,
    default=False,
    help=(
        "Stream events to the server in real time as the script runs "
        "(requires --connect). Events appear in the browser during execution. "
        "May be combined with --output to simultaneously capture a lossless file."
    ),
)
@click.option(
    "--no-pace",
    is_flag=True,
    default=False,
    help=(
        "Disable inter-event pacing when streaming a completed trace via --connect. "
        "Ignored when --stream is active (real-time mode has no pacing)."
    ),
)
def trace(
    script: Path,
    output: Path | None,
    root: Path,
    lines: bool,
    max_events: int | None,
    connect: str | None,
    stream: bool,
    no_pace: bool,
) -> None:
    """Trace SCRIPT and emit runtime events as JSONL.

    Each line of output is a JSON object with fields: event, node_id,
    ts_ns, thread_id, frame_depth, metadata. Node IDs match those from
    ``grackle parse ROOT``.

    SCRIPT is executed under sys.monitoring (PEP 669). Only Python
    functions inside ROOT are traced; stdlib and site-packages are skipped.

    SCRIPT must live inside ROOT — otherwise its frames will not resolve
    to any node in the static graph and every event will fall back to
    ``<unresolved>``. The CLI exits with a clear error in that case.

    Note: SCRIPT is executed in this process via ``runpy.run_path`` with
    ``sys.argv`` and the current working directory unchanged. If the
    script reads ``sys.argv`` or relies on a specific cwd, pre-set them
    before invoking ``grackle trace``.
    """
    import json as _json

    from grackle.adapters.base import TraceCapExceeded, TraceOptions
    from grackle.python_runtime.adapter import PythonRuntimeAdapter
    from grackle.python_runtime.writer import write_jsonl

    # ------------------------------------------------------------------
    # Option validation
    # ------------------------------------------------------------------
    if stream and connect is None:
        raise click.UsageError("--stream requires --connect URL")

    # Verify SCRIPT lives inside ROOT — otherwise every frame falls back
    # to "<unresolved>" because the resolver only indexes files under root.
    try:
        script.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise click.UsageError(
            f"SCRIPT ({script}) is not inside --root ({root}); "
            "every traced frame would resolve to <unresolved>. "
            "Pass --root pointing at the project that contains SCRIPT."
        ) from exc

    options = TraceOptions(include_line_events=lines, max_events=max_events)
    adapter = PythonRuntimeAdapter()

    # ------------------------------------------------------------------
    # Real-time streaming path  (--connect URL --stream)
    # ------------------------------------------------------------------
    if stream:
        assert connect is not None  # guarded above

        from grackle.python_runtime.stream_sender import TraceStreamSender

        session_id = str(uuid4())
        sender = TraceStreamSender(connect, session_id)
        try:
            sender.start()
        except ConnectionError as exc:
            raise click.ClickException(f"could not connect to {connect}: {exc}") from exc

        # Tee path: if --output is given, buffer every event alongside the WS
        # stream.  The file is lossless — it captures all events including any
        # the WS sender drops under backpressure — so file count >= sent count.
        _tee_buf: list[TraceEvent] | None = None
        active_sink: Callable[[TraceEvent], None]
        if output is not None:
            _buf: list[TraceEvent] = []
            _tee_buf = _buf
            _ws_sink = sender.sink

            def _tee_sink(event: TraceEvent) -> None:
                _ws_sink(event)
                _buf.append(event)

            active_sink = _tee_sink
        else:
            active_sink = sender.sink

        sent = 0
        _cap_exc: click.ClickException | None = None
        try:
            adapter.trace_streaming(script, root, options, active_sink)
        except TraceCapExceeded as exc:
            # Store cap error — write the file first (captured prefix is valid),
            # then re-raise below so the user gets both the file and the error.
            _cap_exc = click.ClickException(str(exc))
        except Exception as exc:
            raise click.ClickException(f"trace error: {exc}") from exc
        finally:
            sent = sender.finish()

        if _tee_buf is not None:
            assert output is not None
            try:
                tee_count = write_jsonl(_tee_buf, output)
                click.echo(f"wrote {tee_count} events → {output}", err=True)
            except Exception as write_exc:
                raise click.ClickException(f"could not write {output}: {write_exc}") from write_exc

        if _cap_exc is not None:
            raise _cap_exc

        if sender.connection_lost:
            click.echo(
                f"WARNING: connection lost after {sent} events; session_end not sent",
                err=True,
            )
        else:
            click.echo(f"streamed {sent} events → {connect}", err=True)
        return

    # ------------------------------------------------------------------
    # Completed-trace path  (default, or --connect without --stream)
    # ------------------------------------------------------------------
    try:
        events = list(adapter.trace(script, root, options))
    except TraceCapExceeded as exc:
        raise click.ClickException(str(exc)) from exc

    if output is not None:
        count = write_jsonl(events, output)
        click.echo(f"wrote {count} events → {output}", err=True)
    elif connect is None:
        # stdout mode: only when neither --output nor --connect is given
        for event in events:
            click.echo(_json.dumps(event, ensure_ascii=False))

    if connect is not None:
        try:
            asyncio.run(_stream_events_to_server(events, connect, pace=not no_pace))
            click.echo(f"streamed {len(events)} events → {connect}", err=True)
        except Exception as exc:
            raise click.ClickException(f"stream to {connect} failed: {exc}") from exc


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
@click.option("--port", default=7878, show_default=True, help="WebSocket port.")
@click.option(
    "--root",
    default=".",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Project root to parse on client connect (default: current directory).",
)
@click.option(
    "--trace-source",
    default=None,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help=(
        "JSONL trace file to replay to every new browser connection after "
        "the static_graph push.  Each connection replays the file from the "
        "start.  Omit for live-attach mode (producers stream via --connect)."
    ),
)
@click.option(
    "--no-pace",
    is_flag=True,
    default=False,
    help="Disable inter-event pacing during file replay (push all events immediately).",
)
@click.option(
    "--store",
    default=None,
    type=click.Path(dir_okay=False, path_type=Path),
    help=(
        "Path to the SQLite session library (e.g. .grackle/sessions.db). "
        "When set, completed live trace sessions are persisted for later replay "
        "via the SessionLibraryPanel. Created if it does not exist."
    ),
)
def serve(
    host: str,
    port: int,
    root: Path,
    trace_source: Path | None,
    no_pace: bool,
    store: Path | None,
) -> None:
    """Start the grackle agent WebSocket server."""
    configure_logging()
    log = structlog.get_logger()
    log.info(
        "grackle starting",
        platform=platform.platform(),
        python=sys.version.split()[0],
        root=str(root),
        trace_source=str(trace_source) if trace_source else None,
    )
    session_store = None
    if store is not None:
        from grackle.session_store import SessionStore as _SessionStore

        session_store = _SessionStore.open(store)
    asyncio.run(
        _server.serve(
            host,
            port,
            root=root,
            trace_source=trace_source,
            pace=not no_pace,
            store=session_store,
        )
    )


@main.command()
@click.argument(
    "trace_a",
    metavar="A.jsonl",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.argument(
    "trace_b",
    metavar="B.jsonl",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
    help="Output format: human-readable summary (text) or full diff as JSON.",
)
@click.option(
    "--only",
    type=click.Choice(["hotter", "new", "gone", "colder", "same", "all"]),
    default="all",
    show_default=True,
    help="Show only entries with this status (or all).",
)
def diff(
    trace_a: Path,
    trace_b: Path,
    output_format: str,
    only: str,
) -> None:
    """Compare two JSONL trace files and report regressions.

    Classifies each node as: hotter (regression), colder, new, gone, or same.
    Exits with status 1 when any node is hotter (suitable for CI).

    Example:

    \b
        grackle diff baseline.jsonl latest.jsonl
        grackle diff baseline.jsonl latest.jsonl --format json
        grackle diff baseline.jsonl latest.jsonl --only hotter
    """
    from grackle.diff import diff_trace_vs_trace, has_regression
    from grackle.python_runtime.aggregates import TraceAggregates

    agg_a = TraceAggregates.build(trace_a)
    agg_b = TraceAggregates.build(trace_b)
    entries = diff_trace_vs_trace(agg_a, agg_b)

    filtered = entries if only == "all" else [e for e in entries if e["status"] == only]

    if output_format == "json":
        click.echo(json.dumps(filtered, indent=2))
    else:
        # Human-readable summary
        from collections import Counter

        counts: Counter[str] = Counter(e["status"] for e in entries)
        click.echo(f"A: {trace_a}  ({len(agg_a)} events, {len(agg_a.node_ids)} nodes)")
        click.echo(f"B: {trace_b}  ({len(agg_b)} events, {len(agg_b.node_ids)} nodes)")
        click.echo("")
        for status in ("hotter", "new", "gone", "colder", "same"):
            n = counts.get(status, 0)
            if n:
                marker = " ← regression" if status == "hotter" else ""
                click.echo(f"  {status:8s} {n:4d}{marker}")
        click.echo("")

        if filtered:
            col = 48
            click.echo(f"{'node_id':{col}}  status    count_a  count_b   delta")
            click.echo("-" * (col + 38))
            for e in filtered:
                nid = e["node_id"]
                if len(nid) > col:
                    nid = "…" + nid[-(col - 1) :]
                click.echo(
                    f"{nid:{col}}  {e['status']:8s}  {e['count_a']:6d}  "
                    f"{e['count_b']:6d}  {e['delta']:+6d}"
                )
        elif only != "all":
            click.echo(f"(no nodes with status {only!r})")

        # When --only hides the hotter rows, the table can look clean while the
        # exit code is still 1. Call that out so the non-zero exit isn't a
        # mystery in CI logs.
        hotter_n = counts.get("hotter", 0)
        if hotter_n and only not in ("all", "hotter"):
            click.echo("")
            click.echo(
                f"note: {hotter_n} hotter node(s) not shown (hidden by --only "
                f"{only}); exiting 1 due to regression."
            )

    if has_regression(entries):
        raise SystemExit(1)


async def _stream_events_to_server(
    events: list[TraceEvent],
    url: str,
    pace: bool = True,
) -> None:
    """Open a WebSocket to *url* and stream *events* as a completed trace session.

    Sends ``trace_session_start`` → ``trace_event*`` → ``trace_session_end``.
    When *pace* is True, inter-event gaps are reproduced with a cap of
    ``_MAX_GAP_S`` per event.  When False, events are pushed immediately.
    """
    from websockets.asyncio.client import connect as _ws_connect

    session_id = str(uuid4())
    started_ns = time.monotonic_ns()

    async with _ws_connect(url) as ws:
        await ws.send(_protocol.make_trace_session_start(session_id, started_ns, "live"))

        prev_ts_ns: int | None = None
        for event in events:
            if pace and prev_ts_ns is not None:
                gap_s = (event["ts_ns"] - prev_ts_ns) / 1_000_000_000
                sleep_s = min(gap_s, _MAX_GAP_S)
                if sleep_s > 0:
                    await asyncio.sleep(sleep_s)
            prev_ts_ns = event["ts_ns"]
            await ws.send(_protocol.make_trace_event(event))

        await ws.send(
            _protocol.make_trace_session_end(session_id, time.monotonic_ns(), len(events))
        )
