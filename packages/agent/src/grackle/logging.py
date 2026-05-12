import logging
import os
import sys
from typing import Any

import structlog


def configure_logging(format: str | None = None) -> None:
    """Configure structlog for pretty (dev) or JSON (production) output.

    Priority: explicit argument > GRACKLE_LOG_FORMAT env > isatty() auto-detect.
    On Windows, ConsoleRenderer auto-loads colorama for ANSI support.
    """
    fmt = (
        format
        or os.environ.get("GRACKLE_LOG_FORMAT")
        or ("pretty" if sys.stdout.isatty() else "json")
    )

    shared: list[Any] = [
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    renderer: Any
    if fmt == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
