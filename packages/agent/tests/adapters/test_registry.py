from pathlib import Path

import pytest

from grackle.adapters.base import Capabilities, ParseOptions, StaticGraph
from grackle.adapters.registry import AdapterRegistry


class _StaticStub:
    def __init__(self, language: str, *, detects: bool = False) -> None:
        self.language = language
        self._detects = detects

    def detect(self, project_root: Path) -> bool:
        return self._detects

    def capabilities(self) -> Capabilities:
        return Capabilities()

    def parse(self, project_root: Path, options: ParseOptions) -> StaticGraph:
        return {"version": 1, "language": self.language, "nodes": [], "edges": []}


class _RuntimeStub:
    def __init__(self, language: str) -> None:
        self.language = language

    def capabilities(self) -> Capabilities:
        return Capabilities()


def test_register_and_retrieve_static() -> None:
    reg = AdapterRegistry()
    adapter = _StaticStub("python")
    reg.register_static(adapter)
    assert reg.get_static("python") is adapter


def test_register_and_retrieve_runtime() -> None:
    reg = AdapterRegistry()
    adapter = _RuntimeStub("python")
    reg.register_runtime(adapter)
    assert reg.get_runtime("python") is adapter


def test_case_insensitive_lookup() -> None:
    reg = AdapterRegistry()
    adapter = _StaticStub("python")
    reg.register_static(adapter)
    assert reg.get_static("Python") is adapter
    assert reg.get_static("PYTHON") is adapter
    assert reg.get_static("python") is adapter


def test_duplicate_static_raises() -> None:
    reg = AdapterRegistry()
    reg.register_static(_StaticStub("python"))
    with pytest.raises(ValueError, match="already registered"):
        reg.register_static(_StaticStub("python"))


def test_duplicate_runtime_raises() -> None:
    reg = AdapterRegistry()
    reg.register_runtime(_RuntimeStub("python"))
    with pytest.raises(ValueError, match="already registered"):
        reg.register_runtime(_RuntimeStub("python"))


def test_two_languages_coexist() -> None:
    reg = AdapterRegistry()
    py = _StaticStub("python")
    ts = _StaticStub("typescript")
    reg.register_static(py)
    reg.register_static(ts)
    assert reg.get_static("python") is py
    assert reg.get_static("typescript") is ts


def test_detect_returns_only_matches(tmp_path: Path) -> None:
    reg = AdapterRegistry()
    reg.register_static(_StaticStub("python", detects=True))
    reg.register_static(_StaticStub("typescript", detects=False))
    assert reg.detect(tmp_path) == ["python"]


def test_supported_languages_union_sorted() -> None:
    reg = AdapterRegistry()
    reg.register_static(_StaticStub("typescript"))
    reg.register_runtime(_RuntimeStub("python"))
    assert reg.supported_languages() == ["python", "typescript"]


def test_get_static_unknown_returns_none() -> None:
    reg = AdapterRegistry()
    assert reg.get_static("unknown") is None


def test_get_runtime_unknown_returns_none() -> None:
    reg = AdapterRegistry()
    assert reg.get_runtime("unknown") is None
