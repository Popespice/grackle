"""Tests for the ``grackle trace`` CLI subcommand.

Covers:
- happy path (writes JSONL output)
- ``--max-events`` rejects non-positive values (I3)
- SCRIPT outside ``--root`` is rejected with a clear error (I5)
- ``--max-events`` cap is propagated to the tracer
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from click.testing import CliRunner

from grackle.cli import main

if TYPE_CHECKING:
    from pathlib import Path


def _write_simple_script(root: Path) -> Path:
    """Write a minimal traceable script to ``root/script.py`` and return its path."""
    root.mkdir(parents=True, exist_ok=True)
    script = root / "script.py"
    script.write_text(
        "def add(a, b):\n"
        "    return a + b\n"
        "\n"
        "def main() -> None:\n"
        "    add(1, 2)\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n",
        encoding="utf-8",
    )
    return script


def test_trace_writes_output_file(tmp_path: Path) -> None:
    script = _write_simple_script(tmp_path)
    out = tmp_path / "trace.jsonl"
    result = CliRunner().invoke(
        main,
        ["trace", str(script), "--root", str(tmp_path), "--output", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) > 0
    # Every line must be a valid JSON event with the required fields
    for raw in lines:
        e = json.loads(raw)
        assert "event" in e
        assert "node_id" in e


def test_trace_stdout(tmp_path: Path) -> None:
    script = _write_simple_script(tmp_path)
    result = CliRunner().invoke(main, ["trace", str(script), "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    # First non-empty stdout line must be a JSON object
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert lines, "expected at least one event on stdout"
    json.loads(lines[0])


def test_trace_max_events_zero_rejected(tmp_path: Path) -> None:
    """``--max-events 0`` must fail with a usage error (I3 regression)."""
    script = _write_simple_script(tmp_path)
    result = CliRunner().invoke(
        main,
        ["trace", str(script), "--root", str(tmp_path), "--max-events", "0"],
    )
    assert result.exit_code != 0
    assert "0" in result.output


def test_trace_max_events_negative_rejected(tmp_path: Path) -> None:
    """``--max-events -1`` must fail with a usage error (I3 regression)."""
    script = _write_simple_script(tmp_path)
    result = CliRunner().invoke(
        main,
        ["trace", str(script), "--root", str(tmp_path), "--max-events", "-1"],
    )
    assert result.exit_code != 0


def test_trace_max_events_cap_propagates(tmp_path: Path) -> None:
    """Tracer must surface ``TraceCapExceeded`` as a click error."""
    script = _write_simple_script(tmp_path)
    result = CliRunner().invoke(
        main,
        ["trace", str(script), "--root", str(tmp_path), "--max-events", "1"],
    )
    # Cap of 1 is reached almost immediately on any non-trivial script
    assert result.exit_code != 0
    assert "cap" in result.output.lower()


def test_trace_script_outside_root_rejected(tmp_path: Path) -> None:
    """SCRIPT not under ROOT must be rejected with a clear UsageError (I5)."""
    # Two unrelated dirs
    root_dir = tmp_path / "project"
    outside_dir = tmp_path / "elsewhere"
    root_dir.mkdir()
    outside_dir.mkdir()
    # Script lives in outside_dir, not under root_dir
    script = _write_simple_script(outside_dir)

    result = CliRunner().invoke(main, ["trace", str(script), "--root", str(root_dir)])
    assert result.exit_code != 0, result.output
    assert "not inside" in result.output.lower() or "<unresolved>" in result.output


def test_trace_help_mentions_runpy_caveat() -> None:
    """The ``trace --help`` output should warn about sys.argv/cwd (I4)."""
    result = CliRunner().invoke(main, ["trace", "--help"])
    assert result.exit_code == 0
    # The note about runpy + sys.argv is part of the docstring
    assert "sys.argv" in result.output or "cwd" in result.output
