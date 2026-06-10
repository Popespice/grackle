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
    help="Write JSONL to FILE instead of stdout.",
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
        "Note: this streams a completed trace, not a live in-progress one."
    ),
)
@click.option(
    "--no-pace",
    is_flag=True,
    default=False,
    help="Disable inter-event pacing when streaming via --connect (push all events immediately).",
)
def trace(
    script: Path,
    output: Path | None,
    root: Path,
    lines: bool,
    max_events: int | None,
    connect: str | None,
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
def serve(
    host: str,
    port: int,
    root: Path,
    trace_source: Path | None,
    no_pace: bool,
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
    asyncio.run(_server.serve(host, port, root=root, trace_source=trace_source, pace=not no_pace))


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
@click.option("--port", default=7878, show_default=True, help="WebSocket port.")
@click.option(
    "--fixture-root",
    "fixture_roots_raw",
    multiple=True,
    metavar="NAME=PATH",
    help=(
        "Named project root (Python/Go/Rust/polyglot). Repeatable. "
        "Defaults: python/rust/poly/tiny/go — see below."
    ),
)
@click.option(
    "--default",
    "default_fixture",
    default="python",
    show_default=True,
    help="Name of the fixture pushed on connect.",
)
@click.option(
    "--loop/--no-loop",
    default=False,
    help="Repeat the golden-trace replay after it ends (default --no-loop).",
)
@click.option(
    "--no-pace",
    is_flag=True,
    default=False,
    help="Disable inter-event pacing during trace replay (push all events immediately).",
)
def demo(
    host: str,
    port: int,
    fixture_roots_raw: tuple[str, ...],
    default_fixture: str,
    loop: bool,
    no_pace: bool,
) -> None:
    """End-product preview: parse real projects + golden-trace overlay.

    Parses each fixture root via parse_all on first connect, then caches the
    result.  Accepts ``load_fixture`` envelopes to switch mid-session.

    The Python fixture (``tiny-python-app``) ships a golden trace and drives
    the real Phase 6.3 Timeline panel + node heat-map.  Rust, Go, and polyglot
    fixtures render as static-only (no trace).  The ``tiny``…``huge`` presets are
    synthetic graph fixtures (``fixtures/demo-graph/*.json``) spanning 7 → 4,950
    nodes so the visualization can be exercised at different scales.

    A ``NAME=PATH`` value may point at a project directory (parsed via
    ``parse_all``) or a pre-built ``*.json`` graph (loaded directly).

    Default fixtures:

    \b
        python = fixtures/tiny-python-app    (has golden trace → overlay)
        rust   = fixtures/tiny-rust-app
        poly   = fixtures/tiny-polyglot
        go     = fixtures/tiny-go-app
        tiny   = fixtures/demo-graph/tiny.json     (7 nodes)
        small  = fixtures/demo-graph/small.json    (33 nodes)
        medium = fixtures/demo-graph/medium.json   (220 nodes)
        large  = fixtures/demo-graph/large.json    (1,120 nodes)
        huge   = fixtures/demo-graph/huge.json     (4,950 nodes)
    """
    from grackle import demo as demo_module  # lazy: throwaway module

    configure_logging()
    log = structlog.get_logger()

    roots_raw = fixture_roots_raw or (
        "python=fixtures/tiny-python-app",
        "rust=fixtures/tiny-rust-app",
        "poly=fixtures/tiny-polyglot",
        "go=fixtures/tiny-go-app",
        "tiny=fixtures/demo-graph/tiny.json",
        "small=fixtures/demo-graph/small.json",
        "medium=fixtures/demo-graph/medium.json",
        "large=fixtures/demo-graph/large.json",
        "huge=fixtures/demo-graph/huge.json",
    )
    fixture_roots: dict[str, Path] = {}
    for raw in roots_raw:
        if "=" not in raw:
            raise click.UsageError(f"--fixture-root must be NAME=PATH, got: {raw!r}")
        name, _, path_str = raw.partition("=")
        name = name.strip()
        p = Path(path_str.strip())
        if not p.exists():
            raise click.UsageError(f"fixture root not found: {p}")
        if not p.is_dir() and p.suffix != ".json":
            raise click.UsageError(f"fixture must be a directory or a .json graph: {p}")
        fixture_roots[name] = p

    log.info(
        "grackle demo starting",
        platform=platform.platform(),
        python=sys.version.split()[0],
        fixture_roots={k: str(v) for k, v in fixture_roots.items()},
        default=default_fixture,
        loop=loop,
        pace=not no_pace,
    )
    asyncio.run(
        demo_module.serve_demo(
            host,
            port,
            fixture_roots,
            default_fixture,
            loop_trace=loop,
            pace=not no_pace,
        )
    )


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
