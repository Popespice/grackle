"""Tests for GoStaticParser adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from grackle.adapters.base import StaticParserAdapter
from grackle.go_parser.adapter import GoStaticParser


def test_adapter_implements_protocol() -> None:
    assert isinstance(GoStaticParser(), StaticParserAdapter)


def test_language_is_go() -> None:
    assert GoStaticParser().language == "go"


def test_detect_go_mod(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text("module example.com/test\n\ngo 1.21\n")
    assert GoStaticParser().detect(tmp_path) is True


def test_detect_go_sum(tmp_path: Path) -> None:
    (tmp_path / "go.sum").write_text("")
    assert GoStaticParser().detect(tmp_path) is True


def test_detect_go_file(tmp_path: Path) -> None:
    (tmp_path / "main.go").write_text("package main\n")
    assert GoStaticParser().detect(tmp_path) is True


def test_detect_empty_dir_false(tmp_path: Path) -> None:
    assert GoStaticParser().detect(tmp_path) is False


def test_detect_python_only_false(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("pass\n")
    assert GoStaticParser().detect(tmp_path) is False


def test_capabilities_calls_true() -> None:
    assert GoStaticParser().capabilities().calls is True


def test_adapter_registered() -> None:
    import grackle  # noqa: F401
    from grackle.adapters import registry

    adapter = registry.get_static("go")
    assert adapter is not None
    assert adapter.language == "go"
