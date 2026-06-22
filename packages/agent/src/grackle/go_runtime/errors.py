"""Errors for the Go runtime adapter (ADR-0023)."""

from __future__ import annotations


class GoRuntimeError(RuntimeError):
    """A Go coverage trace could not be produced (build, run, or covdata failure).

    The CLI catches this and surfaces it as a clean ``click.ClickException``
    rather than a traceback — the ADR-0023 robustness requirement that the Go
    runtime "degrade with a clear message and never crash".
    """
