"""Rust toolchain orchestration: build → run → merge → export (ADR-0024).

Synchronous subprocess pipeline. All steps run inside a single
``tempfile.TemporaryDirectory`` — nothing is ever written into the user's tree.

Steps:
1. Resolve the Cargo package for the input script via workspace.get_crates().
2. ``cargo build -p <pkg> --bins --message-format=json-render-diagnostics
   --target-dir <tmp>/target`` with ``RUSTFLAGS=-Cinstrument-coverage``
   (appended to any existing RUSTFLAGS). Select the ``compiler-artifact``
   whose ``target.src_path`` matches the script → the binary path.
3. Run the binary with ``LLVM_PROFILE_FILE=<tmp>/prof/grackle-%p-%m.profraw``
   (directory created first). Non-zero exit is not fatal — check for .profraw
   files instead.
4. ``llvm-profdata merge -sparse <profraw>... -o <tmp>/merged.profdata``
5. ``llvm-cov export <binary> -instr-profile=<tmp>/merged.profdata
   --format=json`` → return stdout.

Build timeout is 300 s (cold instrumented Rust builds with dep compilation can
exceed the 120 s Go adapter uses). Run and profdata timeouts mirror Go's.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from grackle.paths import to_posix
from grackle.rust_runtime import capability
from grackle.rust_runtime.errors import RustRuntimeError

_BUILD_TIMEOUT_S = 300
_RUN_TIMEOUT_S = 120
_PROFDATA_TIMEOUT_S = 30
_EXPORT_TIMEOUT_S = 30


def run(script: Path, root: Path) -> str:
    """Build *script*'s Cargo package, run it under coverage, return export JSON.

    Args:
        script: Path to the binary entry-point ``.rs`` file (``src/main.rs``
            or ``src/bin/foo.rs``).
        root:   Project root (contains ``Cargo.toml`` or ``Cargo.toml``
            workspace manifest).

    Returns:
        The raw ``llvm-cov export --format=json`` output as a UTF-8 string.

    Raises:
        RustRuntimeError: on any step failure.
    """
    cargo = capability._require_cargo()
    profdata_exe = capability.llvm_profdata()
    llvm_cov_exe = capability.llvm_cov()

    # Resolve the Cargo package name for the given script.
    pkg_name = _resolve_package(script, root)

    # Shared environment: append coverage flag, disable incremental to keep the
    # temp build lean. Do not mutate os.environ directly.
    # CARGO_NET_OFFLINE is intentionally NOT set — users may have crates.io
    # dependencies not yet in their local registry cache.
    base_env = {
        **os.environ,
        "RUSTFLAGS": capability.build_rustflags(os.environ),
        capability.CARGO_INCREMENTAL_ENV: "0",
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        target_dir = tmp / "target"
        prof_dir = tmp / "prof"
        prof_dir.mkdir()
        merged = tmp / "merged.profdata"

        # Step 2: build, bins-only, into a dedicated target-dir.
        binary = _build(cargo, pkg_name, script, root, target_dir, base_env)

        # Step 3: run with LLVM_PROFILE_FILE.
        _run_binary(
            binary,
            root,
            {
                **base_env,
                capability.LLVM_PROFILE_FILE_ENV: str(prof_dir / capability.LLVM_PROFILE_PATTERN),
            },
        )

        # Step 4: merge raw profiles.
        profraw_files = sorted(prof_dir.glob("*.profraw"))
        if not profraw_files:
            raise RustRuntimeError(
                "no coverage data produced; the program must run to completion "
                "(coverage is lost on abort, SIGKILL, or panic=abort)"
            )
        _merge_profiles(profdata_exe, profraw_files, merged, base_env, root)

        # Step 5: export JSON.
        return _export(llvm_cov_exe, binary, merged, base_env, root)


def _resolve_package(script: Path, root: Path) -> str:
    """Return the Cargo package name whose source tree contains *script*.

    Uses posix_root prefix-matching (consistent with rust_parser's resolver).
    Single-crate fallback (posix_root == "") always matches.
    """
    from grackle.rust_parser.workspace import get_crates

    try:
        script_posix = to_posix(script, root)
    except (ValueError, OSError) as exc:
        raise RustRuntimeError(f"{script}: not inside the project root {root}") from exc

    crates = get_crates(root)
    # Prefer the most specific (longest posix_root) match.
    matched = [
        c for c in crates if c.posix_root == "" or script_posix.startswith(c.posix_root + "/")
    ]
    if not matched:
        # Workspace-root-as-package: a Cargo.toml may carry both [workspace] and
        # [package] (e.g. a binary at the repo root with helper crates under
        # crates/). get_crates() returns only the explicit members list — the root
        # package itself is absent. Fall back to reading it directly so that
        # `cargo build -p <name>` targets the right package.
        root_pkg = _try_root_package(root)
        if root_pkg is not None:
            return root_pkg
        raise RustRuntimeError(
            f"{script.name}: could not find a Cargo package for this file "
            f"under {root}. Ensure the file is inside a crate's src/ directory."
        )
    # Longest posix_root wins (most specific crate).
    best = max(matched, key=lambda c: len(c.posix_root))
    return best.name


def _try_root_package(root: Path) -> str | None:
    """Return the package name from the root Cargo.toml if it declares [package], else None."""
    import tomllib

    root_toml = root / "Cargo.toml"
    if not root_toml.exists():
        return None
    try:
        with root_toml.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    name = data.get("package", {}).get("name")
    return name if isinstance(name, str) and name else None


def _build(
    cargo: str,
    pkg_name: str,
    script: Path,
    root: Path,
    target_dir: Path,
    env: dict[str, str],
) -> Path:
    """Build the package (bins only) and return the matching binary path."""
    cmd = [
        cargo,
        "build",
        "-p",
        pkg_name,
        "--bins",
        "--message-format=json-render-diagnostics",
        "--target-dir",
        str(target_dir),
    ]
    result = _run_step(cmd, cwd=root, env=env, timeout=_BUILD_TIMEOUT_S, label="cargo build")
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        tail = stderr[-500:] if len(stderr) > 500 else stderr
        raise RustRuntimeError(f"cargo build failed:\n{tail}")

    # Parse JSON-lines output to find the compiler-artifact whose src_path
    # matches the requested script.
    script_resolved = script.resolve()
    candidates: list[Path] = []
    for raw_line in result.stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(msg, dict) or msg.get("reason") != "compiler-artifact":
            continue
        target = msg.get("target", {})
        kinds = target.get("kind", [])
        if "bin" not in kinds:
            continue
        src_path = target.get("src_path", "")
        if Path(src_path).resolve() != script_resolved:
            continue
        executable = msg.get("executable")
        if executable:
            candidates.append(Path(executable))

    if not candidates:
        raise RustRuntimeError(
            f"{script.name}: not a binary entry point — "
            "need a `fn main` in `src/main.rs` or `src/bin/<name>.rs`. "
            "Pass a binary source file, not a library."
        )
    if len(candidates) > 1:
        names = ", ".join(str(p) for p in candidates)
        raise RustRuntimeError(
            f"{script.name}: multiple binary artifacts matched — cannot determine "
            f"which binary to trace. Found: {names}"
        )
    return candidates[0]


def _run_binary(binary: Path, cwd: Path, env: dict[str, str]) -> None:
    """Run the instrumented binary. Non-zero exit is not fatal (coverage still written)."""
    _run_step(
        [str(binary)],
        cwd=cwd,
        env=env,
        timeout=_RUN_TIMEOUT_S,
        label="traced program",
    )
    # Non-zero exit is acceptable — we check for .profraw files in the caller.


def _merge_profiles(
    profdata_exe: str,
    profraw_files: list[Path],
    output: Path,
    env: dict[str, str],
    cwd: Path,
) -> None:
    """Merge raw profile files into a single .profdata file."""
    cmd = [profdata_exe, "merge", "-sparse", *[str(f) for f in profraw_files], "-o", str(output)]
    result = _run_step(
        cmd, cwd=cwd, env=env, timeout=_PROFDATA_TIMEOUT_S, label="llvm-profdata merge"
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        tail = stderr[-300:] if len(stderr) > 300 else stderr
        raise RustRuntimeError(f"llvm-profdata merge failed:\n{tail}")


def _export(
    llvm_cov_exe: str,
    binary: Path,
    profdata: Path,
    env: dict[str, str],
    cwd: Path,
) -> str:
    """Export coverage data as JSON via llvm-cov."""
    # --format=json is intentionally omitted: JSON is the default for llvm-cov
    # export, and the Rust-specific sysroot build of llvm-cov (llvm-tools-preview)
    # does not expose "json" as an explicit --format value even though it is the
    # implicit default. Omitting the flag keeps compatibility across all sysroot
    # versions while preserving JSON output.
    cmd = [
        llvm_cov_exe,
        "export",
        str(binary),
        f"-instr-profile={profdata}",
    ]
    result = _run_step(cmd, cwd=cwd, env=env, timeout=_EXPORT_TIMEOUT_S, label="llvm-cov export")
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        tail = stderr[-300:] if len(stderr) > 300 else stderr
        raise RustRuntimeError(f"llvm-cov export failed:\n{tail}")
    return result.stdout


def _run_step(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int,
    label: str,
) -> subprocess.CompletedProcess[str]:
    """Run one toolchain subprocess, mapping spawn/timeout failures to RustRuntimeError.

    Captures stdout/stderr (so a chatty traced program never pollutes grackle's
    own stdout), closes stdin (a stdin-reading program fails fast rather than
    hanging to the timeout), and wraps ``TimeoutExpired``/``OSError`` in a typed
    ``RustRuntimeError`` so the CLI degrades with a clear message (ADR-0024).
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
        raise RustRuntimeError(f"{label} timed out after {timeout}s") from exc
    except OSError as exc:
        raise RustRuntimeError(f"{label} could not run: {exc}") from exc
