"""Rust toolchain capability detection for the Rust runtime adapter (ADR-0024).

The ``RustRuntimeAdapter`` registers *unconditionally* so it is discoverable via
``grackle languages`` and the registry, but it can only trace when a suitable Rust
toolchain is present. This module owns that detection: whether ``cargo`` and ``rustc``
are on PATH, ``rustc`` is new enough (>= 1.60.0 for stable ``-Cinstrument-coverage``),
and the ``llvm-profdata`` / ``llvm-cov`` binaries from ``llvm-tools-preview`` are
present in the sysroot.

Detection results are cached for the process lifetime. Tests that want to simulate a
different toolchain monkeypatch functions directly, or call :func:`reset_cache` after
patching ``shutil.which``.
"""

from __future__ import annotations

import functools
import re
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

# Minimum Rust version that supports ``-Cinstrument-coverage`` for stable builds
# (stabilised in Rust 1.60, March 2022).
MIN_RUST_VERSION: tuple[int, int, int] = (1, 60, 0)

_PROBE_TIMEOUT_S = 5.0

# Matches "rustc 1.75.0 (82e1608df 2023-12-21)" and "rustc 1.75.0-nightly (...)".
_RUSTC_VERSION_RE = re.compile(r"rustc (\d+)\.(\d+)\.(\d+)")

# Matches the "host: <triple>" line in ``rustc -vV`` output.
_HOST_RE = re.compile(r"^host:\s+(.+)$", re.MULTILINE)


@functools.cache
def cargo_executable() -> str | None:
    """Return the path to the ``cargo`` executable, or ``None`` if not on PATH."""
    return shutil.which("cargo")


@functools.cache
def rustc_executable() -> str | None:
    """Return the path to the ``rustc`` executable, or ``None`` if not on PATH."""
    return shutil.which("rustc")


@functools.cache
def rustc_version() -> tuple[int, int, int] | None:
    """Return ``rustc``'s ``(major, minor, patch)`` version, or ``None``.

    Returns ``None`` if ``rustc`` is absent, the probe fails or times out, exits
    non-zero, or its output cannot be parsed. Never raises.
    """
    exe = rustc_executable()
    if exe is None:
        return None
    try:
        proc = subprocess.run(
            [exe, "--version"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    m = _RUSTC_VERSION_RE.search(proc.stdout.strip())
    if m is None:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


@functools.cache
def rustc_sysroot() -> str | None:
    """Return the rustc sysroot path, or ``None`` if unavailable. Never raises."""
    exe = rustc_executable()
    if exe is None:
        return None
    try:
        proc = subprocess.run(
            [exe, "--print", "sysroot"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    sysroot = proc.stdout.strip()
    return sysroot if sysroot else None


@functools.cache
def host_triple() -> str | None:
    """Return the host target triple (e.g. ``x86_64-pc-windows-msvc``), or ``None``."""
    exe = rustc_executable()
    if exe is None:
        return None
    try:
        proc = subprocess.run(
            [exe, "-vV"],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    m = _HOST_RE.search(proc.stdout)
    if m is None:
        return None
    return m.group(1).strip()


@functools.cache
def llvm_tool_path(name: str) -> str | None:
    """Return the absolute path to a sysroot LLVM tool, or ``None`` if not found.

    Looks under ``<sysroot>/lib/rustlib/<host>/bin/<name>`` with an optional
    ``.exe`` suffix for Windows.
    """
    sysroot = rustc_sysroot()
    triple = host_triple()
    if sysroot is None or triple is None:
        return None
    bin_dir = Path(sysroot) / "lib" / "rustlib" / triple / "bin"
    for suffix in ("", ".exe"):
        candidate = bin_dir / (name + suffix)
        if candidate.exists():
            return str(candidate)
    return None


def rust_runtime_available() -> bool:
    """``True`` iff a Rust toolchain with llvm-tools-preview is present and usable.

    Gates on: cargo present, rustc present, version >= 1.60, and both
    ``llvm-profdata`` and ``llvm-cov`` discoverable in the sysroot.
    """
    if cargo_executable() is None or rustc_executable() is None:
        return False
    version = rustc_version()
    if version is None or version < MIN_RUST_VERSION:
        return False
    if llvm_tool_path("llvm-profdata") is None:
        return False
    return llvm_tool_path("llvm-cov") is not None


def remediation_message() -> str:
    """Human-readable guidance for when the Rust gate is closed."""
    if cargo_executable() is None:
        return (
            "Rust was not found on PATH. Tracing Rust programs requires a Rust "
            f">= {_fmt(MIN_RUST_VERSION)} toolchain. "
            "Install Rust via rustup (https://rustup.rs/) and ensure `cargo` is on PATH."
        )
    if rustc_executable() is None:
        return (
            "`cargo` was found but `rustc` was not on PATH. "
            "Install Rust via rustup (https://rustup.rs/)."
        )
    version = rustc_version()
    if version is None:
        exe = rustc_executable() or "rustc"
        return (
            f"Found `rustc` at {exe} but could not determine its version. "
            f"Tracing Rust programs requires Rust >= {_fmt(MIN_RUST_VERSION)}."
        )
    if version < MIN_RUST_VERSION:
        exe = rustc_executable() or "rustc"
        return (
            f"Rust {_fmt(version)} at {exe} is too old for coverage instrumentation. "
            f"`-Cinstrument-coverage` requires Rust >= {_fmt(MIN_RUST_VERSION)}; "
            "please upgrade via rustup."
        )
    # Toolchain is present and new enough — tools must be missing.
    missing = []
    if llvm_tool_path("llvm-profdata") is None:
        missing.append("llvm-profdata")
    if llvm_tool_path("llvm-cov") is None:
        missing.append("llvm-cov")
    if missing:
        tools = " and ".join(missing)
        return (
            f"{tools} not found in the rustc sysroot. "
            "Install the LLVM tools component: `rustup component add llvm-tools-preview`"
        )
    return "Rust runtime tracing is unavailable (unknown reason)."


def _fmt(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def reset_cache() -> None:
    """Clear cached detection results (for tests that monkeypatch toolchain functions)."""
    cargo_executable.cache_clear()
    rustc_executable.cache_clear()
    rustc_version.cache_clear()
    rustc_sysroot.cache_clear()
    host_triple.cache_clear()
    llvm_tool_path.cache_clear()


# ``RUSTFLAGS`` env variable key (used by toolchain.py to append instrumentation flag).
INSTRUMENT_COVERAGE_FLAG = "-Cinstrument-coverage"


def build_rustflags(existing_env: Mapping[str, str]) -> str:
    """Return the RUSTFLAGS value with ``-Cinstrument-coverage`` appended.

    Preserves any existing ``RUSTFLAGS`` value so user flags are not dropped.
    Accepts any ``Mapping`` (including ``os.environ``) to avoid needless copies.
    """
    existing = existing_env.get("RUSTFLAGS", "").strip()
    flag = INSTRUMENT_COVERAGE_FLAG
    if existing:
        return f"{existing} {flag}"
    return flag


def llvm_profdata() -> str:
    """Return the llvm-profdata path; raises ``RustRuntimeError`` if absent."""
    path = llvm_tool_path("llvm-profdata")
    if path is None:
        from grackle.rust_runtime.errors import RustRuntimeError

        raise RustRuntimeError(
            "llvm-profdata not found in the rustc sysroot. "
            "Run: rustup component add llvm-tools-preview"
        )
    return path


def llvm_cov() -> str:
    """Return the llvm-cov path; raises ``RustRuntimeError`` if absent."""
    path = llvm_tool_path("llvm-cov")
    if path is None:
        from grackle.rust_runtime.errors import RustRuntimeError

        raise RustRuntimeError(
            "llvm-cov not found in the rustc sysroot. Run: rustup component add llvm-tools-preview"
        )
    return path


def _require_cargo() -> str:
    """Return the cargo path; raises ``RustRuntimeError`` if absent."""
    exe = cargo_executable()
    if exe is None:
        from grackle.rust_runtime.errors import RustRuntimeError

        raise RustRuntimeError(
            "cargo not found on PATH. Install Rust via rustup (https://rustup.rs/)."
        )
    return exe


# Expose LLVM_PROFILE_FILE env variable name as a constant.
LLVM_PROFILE_FILE_ENV = "LLVM_PROFILE_FILE"
LLVM_PROFILE_PATTERN = "grackle-%p-%m.profraw"

# Disable incremental compilation in the temp target-dir (no benefit for single runs
# and it writes extra files that waste disk space and slow the build slightly).
CARGO_INCREMENTAL_ENV = "CARGO_INCREMENTAL"
