"""Go toolchain orchestration: build → run → covdata textfmt (ADR-0023).

Synchronous subprocess pipeline. All three steps run inside a single
``tempfile.TemporaryDirectory`` — nothing is ever written into the user's tree.

Steps:
1. ``go build -cover -covermode=count -coverpkg=./... -o <bin> <pkg>``
2. Run the binary with ``GOCOVERDIR=<covdir>`` (dir is created first; Go
   requires it to exist).
3. ``go tool covdata textfmt -i <covdir> -o <out>`` → return the textfmt string.

A non-zero program exit in step 2 is NOT fatal: coverage counters are flushed
on normal return and ``os.Exit``; they are lost only on panic/SIGKILL (documented
limitation). The step only fails if no coverage files are written at all.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

from grackle.go_runtime.errors import GoRuntimeError
from grackle.paths import to_posix

_BUILD_TIMEOUT_S = 120
_RUN_TIMEOUT_S = 120
_COVDATA_TIMEOUT_S = 30


def run(script: Path, root: Path) -> str:
    """Build *script*'s package, run it under coverage, return textfmt output.

    Args:
        script: Path to any ``.go`` file inside the target package (must be
            ``package main`` with a ``func main``).
        root:   Module root (contains ``go.mod``).

    Returns:
        The raw ``go tool covdata textfmt`` output as a UTF-8 string.

    Raises:
        GoRuntimeError: on build failure, missing coverage data, or covdata
            export failure.
    """
    go = _require_go()

    # Derive the build target: the package directory relative to root. When the
    # script is at the module root, to_posix returns ".", for which the clean
    # target is "." (not "./.").
    try:
        rel = to_posix(script.parent, root)
    except (ValueError, OSError):
        rel = ""
    target = f"./{rel}" if rel and rel != "." else "."

    # Single-module assumption (ADR-0023): GoResolver maps covdata import paths
    # via the module path in <root>/go.mod. An inherited GOWORK (go.work
    # workspace) would re-scope `-coverpkg=./...` and shift the import-path
    # prefix, silently dropping every block at resolution. Neutralise it.
    base_env = {**os.environ, "GOWORK": "off"}

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        bin_name = "grackle_go_trace" + (".exe" if os.name == "nt" else "")
        binpath = tmp / bin_name
        covdir = tmp / "covdata"
        outpath = tmp / "coverage.txt"

        # Step 1: build with coverage instrumentation.
        build_cmd = [
            go,
            "build",
            "-cover",
            "-covermode=count",
            "-coverpkg=./...",
            "-o",
            str(binpath),
            target,
        ]
        build_result = _run_step(
            build_cmd,
            cwd=root,
            env=base_env,
            timeout=_BUILD_TIMEOUT_S,
            label="go build",
        )
        if build_result.returncode != 0:
            stderr = (build_result.stderr or "").strip()
            tail = stderr[-500:] if len(stderr) > 500 else stderr
            raise GoRuntimeError(f"go build failed:\n{tail}")

        # Step 2: run with GOCOVERDIR. stdin is closed so a program that reads
        # stdin fails fast instead of blocking until the timeout.
        covdir.mkdir()
        _run_step(
            [str(binpath)],
            cwd=root,
            env={**base_env, "GOCOVERDIR": str(covdir)},
            timeout=_RUN_TIMEOUT_S,
            label="traced program",
        )
        # Non-zero exit is not fatal — check for coverage files instead.
        has_data = any(f.name.startswith(("covmeta", "covcounters")) for f in covdir.iterdir())
        if not has_data:
            raise GoRuntimeError(
                "no coverage data produced; the program must run to completion "
                "(coverage is lost on panic or SIGKILL)"
            )

        # Step 3: export to textfmt.
        export_result = _run_step(
            [go, "tool", "covdata", "textfmt", "-i", str(covdir), "-o", str(outpath)],
            cwd=root,
            env=base_env,
            timeout=_COVDATA_TIMEOUT_S,
            label="go tool covdata textfmt",
        )
        if export_result.returncode != 0:
            stderr = (export_result.stderr or "").strip()
            tail = stderr[-300:] if len(stderr) > 300 else stderr
            raise GoRuntimeError(f"go tool covdata textfmt failed:\n{tail}")

        return outpath.read_text("utf-8")


def _run_step(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int,
    label: str,
) -> subprocess.CompletedProcess[str]:
    """Run one toolchain subprocess, mapping spawn/timeout failures to GoRuntimeError.

    Captures stdout/stderr (so a chatty traced program never pollutes grackle's
    own stdout), closes stdin (a stdin-reading program fails fast rather than
    hanging to the timeout), and wraps ``TimeoutExpired``/``OSError`` in a typed
    ``GoRuntimeError`` so the CLI degrades with a clear message (ADR-0023).
    """
    try:
        return subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired as exc:
        raise GoRuntimeError(f"{label} timed out after {timeout}s") from exc
    except OSError as exc:
        raise GoRuntimeError(f"{label} could not run: {exc}") from exc


def _require_go() -> str:
    """Return the ``go`` executable path, raising GoRuntimeError if absent."""
    from grackle.go_runtime import capability

    exe = capability.go_executable()
    if exe is None:
        raise GoRuntimeError(
            "Go was not found on PATH. Install Go >= 1.20 and ensure `go` is on PATH."
        )
    return exe
