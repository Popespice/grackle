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


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
@click.option("--port", default=7878, show_default=True, help="WebSocket port.")
@click.option(
    "--fixture-root",
    "fixture_roots_raw",
    multiple=True,
    metavar="NAME=PATH",
    help=(
        "Named project root in NAME=PATH format. Repeatable. "
        "Defaults to tiny (Python), go (Go), and poly (polyglot)."
    ),
)
@click.option(
    "--default",
    "default_fixture",
    default="tiny",
    show_default=True,
    help="Name of the fixture pushed on connect.",
)
@click.option("--live/--no-live", default=True, help="Loop random pulses every 1.5s.")
def demo(
    host: str,
    port: int,
    fixture_roots_raw: tuple[str, ...],
    default_fixture: str,
    live: bool,
) -> None:
    """End-product preview: parse real projects + optional live pulses.

    Parses each fixture root via parse_all on first connect, then caches the
    result. Accepts ``load_fixture`` envelopes to switch mid-session.
    Ships three default fixtures: tiny (Python), go (Go), poly (polyglot).
    """
    from grackle import demo as demo_module  # lazy: throwaway module

    configure_logging()
    log = structlog.get_logger()

    roots_raw = fixture_roots_raw or (
        "tiny=fixtures/tiny-app",
        "go=fixtures/tiny-go-app",
        "poly=fixtures/tiny-polyglot",
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
        if not p.is_dir():
            raise click.UsageError(f"fixture root must be a directory: {p}")
        fixture_roots[name] = p

    log.info(
        "grackle demo starting",
        platform=platform.platform(),
        python=sys.version.split()[0],
        fixture_roots={k: str(v) for k, v in fixture_roots.items()},
        default=default_fixture,
    )
    asyncio.run(demo_module.serve_demo(host, port, fixture_roots, default_fixture, live))
