"""Errors for the Rust runtime adapter (ADR-0024)."""

from __future__ import annotations


class RustRuntimeError(RuntimeError):
    """A Rust coverage trace could not be produced (build, run, or llvm-cov failure).

    The CLI catches this and surfaces it as a clean ``click.ClickException``
    rather than a traceback — the ADR-0024 robustness requirement that the Rust
    runtime "degrade with a clear message and never crash".
    """
