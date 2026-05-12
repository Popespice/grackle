import asyncio
import platform
import sys

import click
import structlog

from grackle import server as _server
from grackle.logging import configure_logging


@click.group()
def main() -> None:
    """grackle — local-first live code visualizer."""


@main.command()
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind address.")
@click.option("--port", default=7878, show_default=True, help="WebSocket port.")
def serve(host: str, port: int) -> None:
    """Start the grackle agent WebSocket server."""
    configure_logging()
    log = structlog.get_logger()
    log.info(
        "grackle starting",
        platform=platform.platform(),
        python=sys.version.split()[0],
    )
    asyncio.run(_server.serve(host, port))
