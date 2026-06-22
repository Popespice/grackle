"""Go toolchain capability detection for the Go runtime adapter (ADR-0023).

The ``GoRuntimeAdapter`` registers *unconditionally* so it is discoverable via
``grackle languages`` and the registry, but it can only trace when a suitable Go
toolchain is present. This module owns that detection: whether ``go`` is on
``PATH`` and new enough (>= 1.20.0) for ``go build -cover`` of non-test binaries
+ ``GOCOVERDIR`` support.

Detection results are cached for the process lifetime. Tests that want to
simulate a different toolchain monkeypatch :func:`go_executable` /
:func:`go_version` directly, or call :func:`reset_cache` after patching
``shutil.which``.
"""

from __future__ import annotations

import functools
import re
import shutil
import subprocess

# Minimum Go version that supports ``go build -cover`` for non-test binaries
# with ``GOCOVERDIR`` (Go 1.20). Below this the coverage instrumentation is
# test-only and ``GOCOVERDIR`` is not supported.
MIN_GO_VERSION: tuple[int, int, int] = (1, 20, 0)

_VERSION_PROBE_TIMEOUT_S = 5.0

# Matches "go version go1.21.5 linux/amd64", "go1.21", "go1.22rc1", etc.
# Patch group is optional — defaults to 0. Any trailing rc/beta suffix is
# consumed by the non-capturing (?:...)? anchor and ignored.
_VERSION_RE = re.compile(r"go(\d+)\.(\d+)(?:\.(\d+))?")


@functools.cache
def go_executable() -> str | None:
    """Return the path to the ``go`` executable, or ``None`` if not on PATH."""
    return shutil.which("go")


@functools.cache
def go_version() -> tuple[int, int, int] | None:
    """Return ``go``'s ``(major, minor, patch)`` version, or ``None``.

    Returns ``None`` if ``go`` is absent, the probe fails or times out, exits
    non-zero, or its output cannot be parsed. Never raises.
    """
    exe = go_executable()
    if exe is None:
        return None
    try:
        proc = subprocess.run(
            [exe, "version"],
            capture_output=True,
            text=True,
            timeout=_VERSION_PROBE_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    match = _VERSION_RE.search(proc.stdout.strip())
    if match is None:
        return None
    patch = int(match.group(3)) if match.group(3) is not None else 0
    return (int(match.group(1)), int(match.group(2)), patch)


def go_runtime_available() -> bool:
    """``True`` iff a Go toolchain new enough for coverage instrumentation is present."""
    version = go_version()
    return version is not None and version >= MIN_GO_VERSION


def remediation_message() -> str:
    """Human-readable guidance for when the Go gate is closed."""
    exe = go_executable()
    if exe is None:
        return (
            "Go was not found on PATH. Tracing Go programs requires Go "
            f">= {_fmt(MIN_GO_VERSION)} (for `go build -cover` + GOCOVERDIR). "
            "Install Go and ensure `go` is on PATH."
        )
    version = go_version()
    if version is None:
        return (
            f"Found `go` at {exe} but could not determine its version. "
            f"Tracing Go programs requires Go >= {_fmt(MIN_GO_VERSION)}."
        )
    return (
        f"Go {_fmt(version)} at {exe} is too old to instrument non-test binaries. "
        f"`go build -cover` with GOCOVERDIR requires Go >= {_fmt(MIN_GO_VERSION)}; "
        "please upgrade."
    )


def _fmt(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def reset_cache() -> None:
    """Clear cached detection results (for tests that monkeypatch ``shutil.which``)."""
    go_executable.cache_clear()
    go_version.cache_clear()
