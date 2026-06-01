"""Errors for the Node/V8 runtime adapter (ADR-0022)."""

from __future__ import annotations


class NodeRuntimeError(RuntimeError):
    """A Node/V8 trace could not be produced (spawn, inspector, or CDP failure).

    The CLI catches this and surfaces it as a clean ``click.ClickException``
    rather than a traceback — the ADR-0022 robustness requirement that the Node
    runtime "degrade with a clear message and never crash".
    """
