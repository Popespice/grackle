from __future__ import annotations

import json
from typing import TYPE_CHECKING

from click.testing import CliRunner

from grackle.cli import main

if TYPE_CHECKING:
    from pathlib import Path


def _write(root: Path, rel: str, src: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(src, encoding="utf-8")


def test_parse_stdout_valid_json(tmp_path: Path) -> None:
    _write(tmp_path, "mod.py", "class Foo:\n    pass\n")
    result = CliRunner().invoke(main, ["parse", str(tmp_path)])
    assert result.exit_code == 0, result.output
    graph = json.loads(result.output)
    assert graph["language"] == "python"
    assert graph["version"] == 1
    assert any(n["id"] == "mod.py:Foo" for n in graph["nodes"])


def test_parse_output_file(tmp_path: Path) -> None:
    _write(tmp_path, "mod.py", "def helper():\n    pass\n")
    out = tmp_path / "graph.json"
    result = CliRunner().invoke(main, ["parse", str(tmp_path), "--output", str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists()
    graph = json.loads(out.read_text())
    assert graph["language"] == "python"
    assert any(n["id"] == "mod.py:helper" for n in graph["nodes"])


def test_parse_explicit_language(tmp_path: Path) -> None:
    _write(tmp_path, "mod.py", "class Bar:\n    pass\n")
    result = CliRunner().invoke(main, ["parse", str(tmp_path), "--language", "python"])
    assert result.exit_code == 0, result.output
    graph = json.loads(result.output)
    assert any(n["id"] == "mod.py:Bar" for n in graph["nodes"])


def test_parse_unknown_language_error(tmp_path: Path) -> None:
    result = CliRunner().invoke(main, ["parse", str(tmp_path), "--language", "cobol"])
    assert result.exit_code != 0


def test_parse_exclude_pattern(tmp_path: Path) -> None:
    _write(tmp_path, "main.py", "class Main:\n    pass\n")
    _write(tmp_path, "test_main.py", "class TestMain:\n    pass\n")
    result = CliRunner().invoke(main, ["parse", str(tmp_path), "--exclude", "test_*.py"])
    assert result.exit_code == 0, result.output
    graph = json.loads(result.output)
    ids = {n["id"] for n in graph["nodes"]}
    assert "main.py:Main" in ids
    assert "test_main.py:TestMain" not in ids


def test_parse_short_alias(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "x = 1\n")
    result = CliRunner().invoke(main, ["parse", str(tmp_path), "-l", "python"])
    assert result.exit_code == 0, result.output


def test_languages_shows_python() -> None:
    result = CliRunner().invoke(main, ["languages"])
    assert result.exit_code == 0
    assert "python" in result.output
