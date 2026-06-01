"""Node.js toolchain capability detection for the Node/V8 runtime adapter (ADR-0022).

The ``NodeRuntimeAdapter`` registers *unconditionally* so it is discoverable via
``grackle languages`` and the registry, but it can only trace when a suitable Node
toolchain is present. This module owns that detection: whether ``node`` is on
``PATH`` and new enough (>= 22.6.0) to run TypeScript directly via type-stripping.

This mirrors the Python 3.12 gate in spirit. Python runtime tracing is always-on
(``requires-python = ">=3.12"`` makes ``sys.monitoring`` guaranteed); Node runtime
tracing is conditional on the external toolchain, checked here and surfaced as a
clean ``click.ClickException`` by the CLI — never a traceback.

Detection results are cached for the process lifetime: ``node`` does not appear or
change version mid-run, and the capability check may be called repeatedly. Tests
that want to simulate a different toolchain monkeypatch :func:`node_executable` /
:func:`node_version` directly (replacing the cached function object), or call
:func:`reset_cache` after patching ``shutil.which``.
"""

from __future__ import annotations

import functools
import re
import shutil
import subprocess

# Minimum Node version that can run TypeScript via type-stripping. Native
# `--experimental-strip-types` landed in Node 22.6.0; below this, `.ts` cannot
# be executed without an external transpiler (out of scope — see ADR-0022).
MIN_NODE_VERSION: tuple[int, int, int] = (22, 6, 0)

# Type stripping is enabled by default from Node 23.6.0. Below that the
# `--experimental-strip-types` flag is required to run `.ts` directly; at/above
# it the flag is redundant (and newer Node warns that it is no longer needed),
# so the launcher omits it.
_STRIP_TYPES_DEFAULT_ON: tuple[int, int, int] = (23, 6, 0)

# Probe timeout for `node --version`. Generous — a cold `node` start can be
# slow on CI, but we never want to hang the CLI indefinitely.
_VERSION_PROBE_TIMEOUT_S = 5.0

_VERSION_RE = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")


@functools.cache
def node_executable() -> str | None:
    """Return the path to the ``node`` executable, or ``None`` if not on PATH."""
    return shutil.which("node")


@functools.cache
def node_version() -> tuple[int, int, int] | None:
    """Return ``node``'s ``(major, minor, patch)`` version, or ``None``.

    Returns ``None`` if ``node`` is absent, the probe fails or times out, exits
    non-zero, or its output cannot be parsed. Never raises — a missing or broken
    toolchain is a normal, gated condition, not an error.
    """
    exe = node_executable()
    if exe is None:
        return None
    try:
        proc = subprocess.run(
            [exe, "--version"],
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
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def node_runtime_available() -> bool:
    """``True`` iff a Node toolchain new enough for type-stripping is present."""
    version = node_version()
    return version is not None and version >= MIN_NODE_VERSION


def needs_strip_types_flag(version: tuple[int, int, int]) -> bool:
    """``True`` if ``--experimental-strip-types`` must be passed for *version*.

    Type stripping is on by default from 23.6.0; below that the flag is required.
    """
    return version < _STRIP_TYPES_DEFAULT_ON


def remediation_message() -> str:
    """Human-readable guidance for when the Node gate is closed.

    Distinguishes "no node", "unparseable version", and "too old" so the CLI
    error tells the user exactly what to fix.
    """
    exe = node_executable()
    if exe is None:
        return (
            "Node.js was not found on PATH. Tracing TypeScript requires Node "
            f">= {_fmt(MIN_NODE_VERSION)} (for `--experimental-strip-types`). "
            "Install Node.js and ensure `node` is on PATH."
        )
    version = node_version()
    if version is None:
        return (
            f"Found `node` at {exe} but could not determine its version. "
            f"Tracing TypeScript requires Node >= {_fmt(MIN_NODE_VERSION)}."
        )
    return (
        f"Node {_fmt(version)} at {exe} is too old to trace TypeScript directly. "
        f"Type-stripping requires Node >= {_fmt(MIN_NODE_VERSION)}; please upgrade."
    )


def _fmt(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def reset_cache() -> None:
    """Clear cached detection results.

    For tests that simulate a different toolchain by monkeypatching
    ``shutil.which`` / the subprocess probe rather than the functions here.
    """
    node_executable.cache_clear()
    node_version.cache_clear()
