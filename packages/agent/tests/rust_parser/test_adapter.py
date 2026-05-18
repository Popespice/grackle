"""Tests for RustStaticParser adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from grackle.adapters.base import StaticParserAdapter
from grackle.rust_parser.adapter import RustStaticParser


def test_adapter_implements_protocol() -> None:
    assert isinstance(RustStaticParser(), StaticParserAdapter)


def test_language_is_rust() -> None:
    assert RustStaticParser().language == "rust"


def test_detect_cargo_toml(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "test"\n')
    assert RustStaticParser().detect(tmp_path) is True


def test_detect_rs_file(tmp_path: Path) -> None:
    (tmp_path / "main.rs").write_text("fn main() {}\n")
    assert RustStaticParser().detect(tmp_path) is True


def test_detect_empty_dir_false(tmp_path: Path) -> None:
    assert RustStaticParser().detect(tmp_path) is False


def test_detect_python_only_false(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("pass\n")
    assert RustStaticParser().detect(tmp_path) is False


def test_capabilities_calls_true() -> None:
    assert RustStaticParser().capabilities().calls is True


def test_adapter_registered() -> None:
    import grackle  # noqa: F401
    from grackle.adapters import registry

    adapter = registry.get_static("rust")
    assert adapter is not None
    assert adapter.language == "rust"
