"""Tests for TypeScriptStaticParser adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from grackle.adapters.base import StaticParserAdapter
from grackle.typescript_parser.adapter import TypeScriptStaticParser


@pytest.fixture()
def adapter() -> TypeScriptStaticParser:
    return TypeScriptStaticParser()


# ---------------------------------------------------------------------------
# Protocol compliance
# ---------------------------------------------------------------------------


def test_adapter_implements_protocol(adapter: TypeScriptStaticParser) -> None:
    assert isinstance(adapter, StaticParserAdapter)


def test_language_is_typescript(adapter: TypeScriptStaticParser) -> None:
    assert adapter.language == "typescript"


# ---------------------------------------------------------------------------
# detect()
# ---------------------------------------------------------------------------


def test_detect_tsconfig(adapter: TypeScriptStaticParser, tmp_path: Path) -> None:
    (tmp_path / "tsconfig.json").write_text("{}")
    assert adapter.detect(tmp_path) is True


def test_detect_package_json(adapter: TypeScriptStaticParser, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name":"test"}')
    assert adapter.detect(tmp_path) is True


def test_detect_ts_file(adapter: TypeScriptStaticParser, tmp_path: Path) -> None:
    (tmp_path / "index.ts").write_text("export {};")
    assert adapter.detect(tmp_path) is True


def test_detect_tsx_file(adapter: TypeScriptStaticParser, tmp_path: Path) -> None:
    (tmp_path / "App.tsx").write_text("export const App = () => null;")
    assert adapter.detect(tmp_path) is True


def test_detect_empty_dir_is_false(adapter: TypeScriptStaticParser, tmp_path: Path) -> None:
    assert adapter.detect(tmp_path) is False


def test_detect_python_only_is_false(adapter: TypeScriptStaticParser, tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("pass")
    assert adapter.detect(tmp_path) is False


# ---------------------------------------------------------------------------
# capabilities()
# ---------------------------------------------------------------------------


def test_capabilities_calls_true(adapter: TypeScriptStaticParser) -> None:
    cap = adapter.capabilities()
    assert cap.calls is True


def test_capabilities_imports_true(adapter: TypeScriptStaticParser) -> None:
    cap = adapter.capabilities()
    assert cap.imports is True


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_adapter_registered() -> None:
    import grackle  # noqa: F401 — triggers registration
    from grackle.adapters import registry

    adapter = registry.get_static("typescript")
    assert adapter is not None
    assert adapter.language == "typescript"
