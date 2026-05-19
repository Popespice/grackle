import asyncio
import json
import platform
import sys
from pathlib import Path

import click
import structlog

from grackle import server as _server
from grackle.logging import configure_logging


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
def trace(
    script: Path,
    output: Path | None,
    root: Path,
    lines: bool,
    max_events: int | None,
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
    else:
        for event in events:
            click.echo(_json.dumps(event, ensure_ascii=False))


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
@click.option("--port", default=7878, show_default=True, help="WebSocket port.")
@click.option(
    "--root",
    default=".",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Project root to parse on client connect (default: current directory).",
)
def serve(host: str, port: int, root: Path) -> None:
    """Start the grackle agent WebSocket server."""
    configure_logging()
    log = structlog.get_logger()
    log.info(
        "grackle starting",
        platform=platform.platform(),
        python=sys.version.split()[0],
        root=str(root),
    )
    asyncio.run(_server.serve(host, port, root=root))
