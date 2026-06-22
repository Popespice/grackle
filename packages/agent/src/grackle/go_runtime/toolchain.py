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

    # Derive the build target: the package directory relative to root.
    try:
        rel = to_posix(script.parent, root)
    except (ValueError, OSError):
        rel = ""
    target = f"./{rel}" if rel else "."

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        bin_name = "grackle_go_trace" + (".exe" if os.name == "nt" else "")
        binpath = tmp / bin_name
        covdir = tmp / "covdata"
        outpath = tmp / "coverage.txt"

        # Step 1: build with coverage instrumentation.
        build_result = subprocess.run(
            [
                go,
                "build",
                "-cover",
                "-covermode=count",
                "-coverpkg=./...",
                "-o",
                str(binpath),
                target,
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=_BUILD_TIMEOUT_S,
            check=False,
        )
        if build_result.returncode != 0:
            stderr = build_result.stderr.strip()
            tail = stderr[-500:] if len(stderr) > 500 else stderr
            raise GoRuntimeError(f"go build failed:\n{tail}")

        # Step 2: run with GOCOVERDIR.
        covdir.mkdir()
        env = {**os.environ, "GOCOVERDIR": str(covdir)}
        subprocess.run(
            [str(binpath)],
            cwd=root,
            env=env,
            capture_output=True,
            timeout=_RUN_TIMEOUT_S,
            check=False,
        )
        # Non-zero exit is not fatal — check for coverage files instead.
        has_data = any(f.name.startswith(("covmeta", "covcounters")) for f in covdir.iterdir())
        if not has_data:
            raise GoRuntimeError(
                "no coverage data produced; the program must run to completion "
                "(coverage is lost on panic or SIGKILL)"
            )

        # Step 3: export to textfmt.
        export_result = subprocess.run(
            [go, "tool", "covdata", "textfmt", "-i", str(covdir), "-o", str(outpath)],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=_COVDATA_TIMEOUT_S,
            check=False,
        )
        if export_result.returncode != 0:
            stderr = export_result.stderr.strip()
            tail = stderr[-300:] if len(stderr) > 300 else stderr
            raise GoRuntimeError(f"go tool covdata textfmt failed:\n{tail}")

        return outpath.read_text("utf-8")


def _require_go() -> str:
    """Return the ``go`` executable path, raising GoRuntimeError if absent."""
    from grackle.go_runtime import capability

    exe = capability.go_executable()
    if exe is None:
        raise GoRuntimeError(
            "Go was not found on PATH. Install Go >= 1.20 and ensure `go` is on PATH."
        )
    return exe
