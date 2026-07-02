"""Tests for ``grackle trace`` adapter-by-language dispatch + capability gate (8.5).

These exercise routing and the Node gate without ever launching Node — the gate is
forced closed by monkeypatching the capability probe. Actual Node tracing is
covered (Node-gated) in ``tests/node_runtime/test_e2e.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from click.testing import CliRunner

from grackle.cli import main

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _write(root: Path, name: str, body: str = "") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_text(body, encoding="utf-8")
    return path


def test_help_mentions_language_option() -> None:
    result = CliRunner().invoke(main, ["trace", "--help"])
    assert result.exit_code == 0
    assert "--language" in result.output


def test_python_extension_still_traces(tmp_path: Path) -> None:
    """Regression: a .py script with no --language dispatches to the Python adapter."""
    script = _write(
        tmp_path,
        "script.py",
        "def f() -> int:\n    return 1\n\nf()\n",
    )
    result = CliRunner().invoke(main, ["trace", str(script), "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "node_id" in result.output  # emitted JSONL events on stdout


def test_pyw_extension_traces_as_python(tmp_path: Path) -> None:
    """Regression (#5): .pyw was Python-traceable before 8.5's dispatch."""
    script = _write(tmp_path, "script.pyw", "def f() -> int:\n    return 1\n\nf()\n")
    result = CliRunner().invoke(main, ["trace", str(script), "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "node_id" in result.output


def test_extensionless_script_traces_as_python(tmp_path: Path) -> None:
    """Regression (#5): an extension-less script defaults to Python (trace was Python-only)."""
    script = _write(tmp_path, "runme", "def f() -> int:\n    return 1\n\nf()\n")
    result = CliRunner().invoke(main, ["trace", str(script), "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "node_id" in result.output


def test_tsx_extension_clean_error(tmp_path: Path) -> None:
    """#6: .tsx is not in any adapter's extensions → "cannot infer" usage error.

    .tsx/.jsx are excluded from the Node adapter's extensions tuple because they
    are always rejected at the gate (JSX not supported until Phase 9). Users who
    want the JSX-specific message can pass --language typescript explicitly.
    """
    script = _write(tmp_path, "app.tsx", "export const x = 1;\n")
    result = CliRunner().invoke(main, ["trace", str(script), "--root", str(tmp_path)])
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "cannot infer" in result.output


def test_explicit_language_python(tmp_path: Path) -> None:
    script = _write(tmp_path, "weird.txt", "def f():\n    return 1\n\nf()\n")
    result = CliRunner().invoke(
        main, ["trace", str(script), "--root", str(tmp_path), "--language", "python"]
    )
    assert result.exit_code == 0, result.output


def test_unknown_extension_errors(tmp_path: Path) -> None:
    script = _write(tmp_path, "app.rb", "puts 1\n")
    result = CliRunner().invoke(main, ["trace", str(script), "--root", str(tmp_path)])
    assert result.exit_code != 0
    assert "infer" in result.output.lower()
    assert "--language" in result.output


def test_go_gate_closed_clean_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When Go is unavailable, .go tracing fails with a clean, Go-mentioning error."""
    from grackle.go_runtime import capability

    monkeypatch.setattr(capability, "go_executable", lambda: None)
    monkeypatch.setattr(capability, "go_version", lambda: None)

    script = _write(tmp_path, "main.go", "package main\n")
    result = CliRunner().invoke(
        main, ["trace", str(script), "--root", str(tmp_path), "--language", "go"]
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "Go" in result.output


def test_go_runtime_error_surfaced_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A GoRuntimeError from trace() reaches the user as its own message, not 'trace error:'.

    The gate is forced open and the adapter's trace() is patched to raise, so this
    exercises the dedicated `except GoRuntimeError` branch without a Go toolchain.
    """
    from grackle.go_runtime import capability
    from grackle.go_runtime.adapter import GoRuntimeAdapter
    from grackle.go_runtime.errors import GoRuntimeError

    monkeypatch.setattr(capability, "go_runtime_available", lambda: True)

    def _boom(self: GoRuntimeAdapter, *a: object, **k: object) -> object:
        raise GoRuntimeError("go build failed:\nboom.go:1: undefined: x")

    monkeypatch.setattr(GoRuntimeAdapter, "trace", _boom)

    script = _write(tmp_path, "main.go", "package main\n")
    result = CliRunner().invoke(
        main, ["trace", str(script), "--root", str(tmp_path), "--language", "go"]
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "go build failed" in result.output
    # The dedicated branch preserves the typed message — no generic prefix.
    assert "trace error:" not in result.output


def test_rust_runtime_error_surfaced_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A RustRuntimeError from trace() reaches the user as its own message, not 'trace error:'.

    Rust only ever uses the completed-trace path (trace_streaming always raises), so this
    guards the `except (... RustRuntimeError)` branch on that path — the regression where
    only the --stream clause had been updated.
    """
    from grackle.rust_runtime import capability
    from grackle.rust_runtime.adapter import RustRuntimeAdapter
    from grackle.rust_runtime.errors import RustRuntimeError

    monkeypatch.setattr(capability, "rust_runtime_available", lambda: True)

    def _boom(self: RustRuntimeAdapter, *a: object, **k: object) -> object:
        raise RustRuntimeError("cargo build failed:\nerror[E0425]: cannot find value `x`")

    monkeypatch.setattr(RustRuntimeAdapter, "trace", _boom)

    script = _write(tmp_path / "src", "main.rs", "fn main() {}\n")
    result = CliRunner().invoke(
        main, ["trace", str(script), "--root", str(tmp_path), "--language", "rust"]
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "cargo build failed" in result.output
    # The dedicated branch preserves the typed message — no generic prefix.
    assert "trace error:" not in result.output


def test_typescript_gate_closed_clean_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When Node is unavailable, .ts tracing fails with a clean, Node-mentioning error."""
    from grackle.node_runtime import capability

    monkeypatch.setattr(capability, "node_executable", lambda: None)
    monkeypatch.setattr(capability, "node_version", lambda: None)

    script = _write(tmp_path, "app.ts", "export function f(): number { return 1; }\n")
    result = CliRunner().invoke(main, ["trace", str(script), "--root", str(tmp_path)])
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "Node" in result.output
    assert "22.6" in result.output


def test_capture_values_rejected_for_non_python_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--capture-values is Python-only (ADR-0025); other languages get a clean error."""
    from grackle.go_runtime import capability

    monkeypatch.setattr(capability, "go_runtime_available", lambda: True)

    script = _write(tmp_path, "main.go", "package main\n")
    result = CliRunner().invoke(
        main,
        ["trace", str(script), "--root", str(tmp_path), "--language", "go", "--capture-values"],
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "Python-only" in result.output


def test_typescript_gate_closed_via_explicit_language(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from grackle.node_runtime import capability

    # Patch BOTH so the "too old" remediation branch is reached deterministically,
    # regardless of whether the host actually has node on PATH.
    monkeypatch.setattr(capability, "node_executable", lambda: "/usr/bin/node")
    monkeypatch.setattr(capability, "node_version", lambda: (20, 0, 0))  # too old

    script = _write(tmp_path, "app.mts", "export const x: number = 1;\n")
    result = CliRunner().invoke(
        main, ["trace", str(script), "--root", str(tmp_path), "--language", "typescript"]
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "too old" in result.output
