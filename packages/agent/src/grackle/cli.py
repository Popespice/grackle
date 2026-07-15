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

    from grackle.adapters.base import RuntimeAdapter, TraceEvent

# Maximum inter-event sleep when streaming a completed trace to a server.
# Mirrors file_replay._MAX_GAP_S so --connect pacing matches --trace-source pacing.
_MAX_GAP_S = 0.25


def _resolve_runtime_adapter(script: Path, language: str | None) -> RuntimeAdapter:
    """Return the runtime adapter for SCRIPT's language, gated on the adapter itself.

    Language comes from ``--language`` when given, else is inferred from the file
    extension via the registry's runtime-extension index (no hardcoded per-adapter
    table). Raises a clean ``click`` error when the language is unknown, has no
    registered runtime adapter, or the adapter reports it cannot trace this script
    — a missing/old toolchain, or an unsupported input like JSX — never a traceback.
    """
    from grackle.adapters import registry

    suffix = script.suffix.lower()
    ext_index = registry.runtime_extensions()
    if language is not None:
        lang = language.strip().lower()
        if not lang:
            raise click.UsageError("--language must not be empty")
    else:
        # Extension-less scripts (shebang launchers etc.) were Python-traceable
        # before 8.5's language dispatch — `grackle trace` was Python-only — so
        # preserve that and default them to Python. Genuinely-unknown extensions
        # (e.g. .rb) still error with a pointer to --language.
        lang = ext_index.get(suffix) or ("python" if suffix == "" else "")
        if not lang:
            known = ", ".join(sorted(ext_index))
            raise click.UsageError(
                f"cannot infer runtime language from {script.name!r} "
                f"(known extensions: {known}); pass --language explicitly."
            )

    adapter = registry.get_runtime(lang)
    if adapter is None:
        raise click.UsageError(f"no runtime adapter registered for language {lang!r}")

    # The adapter owns its gate (toolchain availability + unsupported inputs).
    # Also check capabilities().runtime_tracing as an independent signal: a future
    # adapter could correctly set that to False while returning None from
    # runtime_unavailable_reason, and we must not proceed in that case.
    reason = adapter.runtime_unavailable_reason(script)
    if reason is None and not adapter.capabilities().runtime_tracing:
        reason = f"runtime tracing is not available for language {lang!r}"
    if reason is not None:
        raise click.ClickException(reason)
    return adapter


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
    "--language",
    "-l",
    default=None,
    help=(
        "Runtime language for SCRIPT. Inferred from the extension when omitted "
        "(.py/.pyw and extension-less → python; .ts/.mts/.cts → typescript; "
        ".tsx/.jsx are not supported yet)."
    ),
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
        "error if the cap is reached. Default: unlimited. Counts emitted events: "
        "a Node --stream (live coverage) capture emits one event per active "
        "function per poll (aggregating many calls), so an identical workload "
        "reaches the cap at a different point than a per-call Python/sampling trace."
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
@click.option(
    "--capture-values",
    is_flag=True,
    default=False,
    help=(
        "Capture sampled call args and return values onto each event's 'values' "
        "field (ADR-0025). Python-only; default OFF (opt-in consent posture). "
        "Captured values persist to any -o/--stream recording — treat this as "
        "data at rest, not just wire traffic. Redaction is name-based (password, "
        "token, api_key, etc.) and on by default; see --no-redact. The tuning "
        "flags below are no-ops unless --capture-values is set."
    ),
)
@click.option(
    "--max-value-len",
    default=120,
    show_default=True,
    type=click.IntRange(min=1),
    help="Character clamp on one formatted captured value.",
)
@click.option(
    "--max-value-items",
    default=10,
    show_default=True,
    type=click.IntRange(min=1),
    help="Collection items / dataclass fields shown per captured value.",
)
@click.option(
    "--max-value-depth",
    default=3,
    show_default=True,
    type=click.IntRange(min=1),
    help="Nesting levels shown per captured value before elision.",
)
@click.option(
    "--capture-first-n",
    default=100,
    show_default=True,
    type=click.IntRange(min=1),
    help=(
        "Per-node_id budget on how many events capture values. Call/return "
        "events are still always emitted beyond the budget — only value "
        "capture stops, so heat/coverage/flame stay complete."
    ),
)
@click.option(
    "--no-redact",
    is_flag=True,
    default=False,
    help=(
        "Disable name-based redaction of captured values (password, token, "
        "api_key, etc.). Escape hatch; redaction is on by default."
    ),
)
def trace(
    script: Path,
    output: Path | None,
    root: Path,
    language: str | None,
    lines: bool,
    max_events: int | None,
    connect: str | None,
    stream: bool,
    no_pace: bool,
    capture_values: bool,
    max_value_len: int,
    max_value_items: int,
    max_value_depth: int,
    capture_first_n: int,
    no_redact: bool,
) -> None:
    """Trace SCRIPT and emit runtime events as JSONL.

    Each line of output is a JSON object with fields: event, node_id,
    ts_ns, thread_id, frame_depth, metadata. Node IDs match those from
    ``grackle parse ROOT``.

    The runtime adapter is chosen by language: Python (``.py``/``.pyw`` and
    extension-less scripts) is traced under ``sys.monitoring`` (PEP 669);
    TypeScript (``.ts``/``.mts``/``.cts``) is traced by driving Node over the V8
    Inspector (requires a Node toolchain >= 22.6 — a clear error is shown when it
    is missing). JSX (``.tsx``/``.jsx``) is not supported yet (Phase 9). Use
    ``--language`` to override the extension-based inference. Only functions
    inside ROOT are traced; runtime/stdlib/dependency frames are skipped.

    SCRIPT must live inside ROOT — otherwise its frames will not resolve
    to any node in the static graph and every event will fall back to
    ``<unresolved>``. The CLI exits with a clear error in that case.

    Note: the Python adapter executes SCRIPT in this process via
    ``runpy.run_path`` with ``sys.argv`` and the current working directory
    unchanged; the Node adapter executes SCRIPT in a separate ``node``
    subprocess. If the script relies on a specific ``sys.argv``/cwd, set them
    before invoking ``grackle trace``.

    ``--capture-values`` (ADR-0025) is Python-only; passing it for any other
    language raises a clean error.
    """
    import json as _json

    from grackle.adapters.base import TraceCapExceeded, TraceOptions
    from grackle.go_runtime.errors import GoRuntimeError
    from grackle.node_runtime.errors import NodeRuntimeError
    from grackle.python_runtime.writer import write_jsonl
    from grackle.rust_runtime.errors import RustRuntimeError

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

    # Dispatch to the runtime adapter for SCRIPT's language; gate on its
    # capability (e.g. the Node toolchain) before doing any work.
    adapter = _resolve_runtime_adapter(script, language)

    if capture_values and adapter.language != "python":
        raise click.UsageError(
            f"--capture-values is Python-only (ADR-0025); {adapter.language!r} "
            "is not supported yet."
        )

    options = TraceOptions(
        include_line_events=lines,
        max_events=max_events,
        capture_values=capture_values,
        max_value_len=max_value_len,
        max_value_items=max_value_items,
        max_value_depth=max_value_depth,
        capture_first_n=capture_first_n,
        redact_values=not no_redact,
    )

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
        except (NodeRuntimeError, GoRuntimeError, RustRuntimeError) as exc:
            raise click.ClickException(str(exc)) from exc
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
    except (NodeRuntimeError, GoRuntimeError, RustRuntimeError) as exc:
        raise click.ClickException(str(exc)) from exc
    except Exception as exc:
        # Belt-and-suspenders: no adapter failure should reach the user as a
        # traceback (mirrors the --stream path above).
        raise click.ClickException(f"trace error: {exc}") from exc

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
@click.option(
    "--watch",
    is_flag=True,
    default=False,
    help=(
        "Watch --root for source-file changes and re-broadcast a fresh "
        "static_graph to every connected client on a real content change "
        "(ADR-0027). Uses the optional watchfiles package if installed "
        "(pip install grackle[watch]), else a stdlib mtime-poller."
    ),
)
@click.option(
    "--watch-interval",
    default=0.3,
    show_default=True,
    type=click.FloatRange(min=0.05),
    help=(
        "Poll cadence in seconds for the stdlib watcher, and the debounce "
        "window for the optional watchfiles backend. Ignored without --watch."
    ),
)
@click.option(
    "--watch-poll",
    is_flag=True,
    default=False,
    help="Force the stdlib mtime-poller even if watchfiles is installed. Ignored without --watch.",
)
def serve(
    host: str,
    port: int,
    root: Path,
    trace_source: Path | None,
    no_pace: bool,
    store: Path | None,
    watch: bool,
    watch_interval: float,
    watch_poll: bool,
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
        watch=watch,
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
            watch=watch,
            watch_interval=watch_interval,
            watch_poll=watch_poll,
        )
    )


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
@click.option("--port", default=7878, show_default=True, help="WebSocket port.")
@click.option(
    "--fixture-root",
    "fixture_roots_raw",
    multiple=True,
    metavar="NAME=PATH",
    help=(
        "Named project root (any registered language) or a pre-built *.json "
        "graph. Repeatable. Defaults: see below."
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

    Parses each fixture root via parse_all on first connect (agent-side
    hub-score + cycle enrichment included), then caches the result. Accepts
    ``load_fixture`` envelopes to switch mid-session, and seeds a real
    session library (Phase 8.3) from every fixture with a golden trace.

    A ``NAME=PATH`` value may point at a project directory (parsed via
    ``parse_all``) or a pre-built ``*.json`` graph (loaded directly).

    Default fixtures:

    \b
        python = fixtures/tiny-python-app    (Python trace overlay)
        values = fixtures/value-capture      (Python trace w/ captured args + returns, redaction)
        node   = fixtures/tiny-node-app      (TypeScript/Node trace overlay)
        go     = fixtures/tiny-go-app        (Go trace overlay)
        rust   = fixtures/tiny-rust-app      (Rust trace overlay)
        poly   = fixtures/tiny-polyglot      (static only — no trace)
        watch  = fixtures/tiny-app           (static; runs the watch-mode diff-animation preview)
        tiny   = fixtures/demo-graph/tiny.json     (7 nodes)
        small  = fixtures/demo-graph/small.json    (33 nodes)
        medium = fixtures/demo-graph/medium.json   (220 nodes)
        large  = fixtures/demo-graph/large.json    (1,120 nodes)
        huge   = fixtures/demo-graph/huge.json     (4,950 nodes)
        nn     = packages/nn/src              (watch it learn — Phase 11 MLP trace overlay)
    """
    from grackle import demo as demo_module  # lazy: throwaway module

    configure_logging()
    log = structlog.get_logger()

    roots_raw = fixture_roots_raw or (
        "python=fixtures/tiny-python-app",
        "values=fixtures/value-capture",
        "node=fixtures/tiny-node-app",
        "go=fixtures/tiny-go-app",
        "rust=fixtures/tiny-rust-app",
        "poly=fixtures/tiny-polyglot",
        "watch=fixtures/tiny-app",
        "tiny=fixtures/demo-graph/tiny.json",
        "small=fixtures/demo-graph/small.json",
        "medium=fixtures/demo-graph/medium.json",
        "large=fixtures/demo-graph/large.json",
        "huge=fixtures/demo-graph/huge.json",
        "nn=packages/nn/src",
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

    # The nn fixture's source root (packages/nn/src) and its committed golden
    # trace (fixtures/nn-training/) live in different places — see
    # demo._trace_for.
    trace_overrides = {"nn": Path("fixtures/nn-training/trace.golden.jsonl")}

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
            trace_overrides=trace_overrides,
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

    Counts are derived from event volume, so feed traces where one event == one
    call: Python traces, or Node *sampling* captures (``grackle trace app.ts -o
    f.jsonl``). A Node ``--stream`` (live coverage) capture emits one event per
    function per poll, so diffing it compares poll-activity, not call counts.

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
