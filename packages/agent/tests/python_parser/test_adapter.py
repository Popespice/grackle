from __future__ import annotations

from typing import TYPE_CHECKING

from grackle.adapters.base import ParseOptions
from grackle.python_parser.adapter import PythonStaticParser

if TYPE_CHECKING:
    from pathlib import Path


def _write(root: Path, rel: str, source: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


# ---------------------------------------------------------------------------
# detect()
# ---------------------------------------------------------------------------


def test_detect_pyproject_toml(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text("[build-system]\n")
    assert PythonStaticParser().detect(tmp_path) is True


def test_detect_setup_py(tmp_path: Path) -> None:
    (tmp_path / "setup.py").write_text("from setuptools import setup\n")
    assert PythonStaticParser().detect(tmp_path) is True


def test_detect_python_version_file(tmp_path: Path) -> None:
    (tmp_path / ".python-version").write_text("3.12\n")
    assert PythonStaticParser().detect(tmp_path) is True


def test_detect_any_py_file(tmp_path: Path) -> None:
    _write(tmp_path, "src/main.py", "x = 1\n")
    assert PythonStaticParser().detect(tmp_path) is True


def test_detect_empty_dir_false(tmp_path: Path) -> None:
    assert PythonStaticParser().detect(tmp_path) is False


# ---------------------------------------------------------------------------
# capabilities()
# ---------------------------------------------------------------------------


def test_capabilities() -> None:
    caps = PythonStaticParser().capabilities()
    assert caps.files is True
    assert caps.classes is True
    assert caps.functions is True
    assert caps.imports is True
    assert caps.calls is True
    assert caps.runtime_tracing is False
    assert caps.annotations is False


# ---------------------------------------------------------------------------
# parse()
# ---------------------------------------------------------------------------


def test_parse_returns_graph(tmp_path: Path) -> None:
    _write(tmp_path, "mod.py", "class Foo:\n    pass\n")
    graph = PythonStaticParser().parse(tmp_path, ParseOptions())
    assert graph["version"] == 1
    assert graph["language"] == "python"
    assert any(n["id"] == "mod.py:Foo" for n in graph["nodes"])


def test_parse_second_call_cache_hit(tmp_path: Path) -> None:
    _write(tmp_path, "mod.py", "class Bar:\n    pass\n")
    adapter = PythonStaticParser()
    g1 = adapter.parse(tmp_path, ParseOptions())
    g2 = adapter.parse(tmp_path, ParseOptions())
    ids1 = {n["id"] for n in g1["nodes"]}
    ids2 = {n["id"] for n in g2["nodes"]}
    assert ids1 == ids2


def test_parse_exclude_patterns(tmp_path: Path) -> None:
    _write(tmp_path, "main.py", "class Main:\n    pass\n")
    _write(tmp_path, "test_main.py", "class TestMain:\n    pass\n")
    graph = PythonStaticParser().parse(tmp_path, ParseOptions(exclude_patterns=("test_*.py",)))
    ids = {n["id"] for n in graph["nodes"]}
    assert "main.py:Main" in ids
    assert "test_main.py:TestMain" not in ids


def test_parse_satisfies_protocol() -> None:
    from grackle.adapters.base import StaticParserAdapter

    assert isinstance(PythonStaticParser(), StaticParserAdapter)
