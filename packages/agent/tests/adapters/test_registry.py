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


def test_detect_uses_registered_key_after_language_mutation() -> None:
    """detect() must yield the registered key, not adapter.language at call time."""
    reg = AdapterRegistry()
    adapter = _StaticStub("python", detects=True)
    reg.register_static(adapter)
    adapter.language = "DIFFERENT"
    assert reg.detect(Path("/tmp")) == ["python"]
    assert reg.supported_languages() == ["python"]


def test_register_rejects_empty_language() -> None:
    reg = AdapterRegistry()
    with pytest.raises(ValueError, match="non-empty"):
        reg.register_static(_StaticStub(""))


def test_register_rejects_whitespace_only_language() -> None:
    reg = AdapterRegistry()
    with pytest.raises(ValueError, match="non-empty"):
        reg.register_static(_StaticStub("   "))


def test_register_rejects_language_with_embedded_control_char() -> None:
    """Embedded newlines/tabs must raise; trailing whitespace is stripped silently."""
    reg = AdapterRegistry()
    with pytest.raises(ValueError, match="control characters"):
        reg.register_static(_StaticStub("py\nthon"))
    # Trailing whitespace (including newlines) is stripped, not an error:
    fresh = AdapterRegistry()
    fresh.register_static(_StaticStub("python\n"))
    assert fresh.supported_languages() == ["python"]


def test_register_strips_and_lowercases_language() -> None:
    reg = AdapterRegistry()
    adapter = _StaticStub("  Python  ")
    reg.register_static(adapter)
    assert reg.supported_languages() == ["python"]
    assert reg.get_static("python") is adapter


def test_get_static_strips_whitespace_on_lookup() -> None:
    reg = AdapterRegistry()
    adapter = _StaticStub("python")
    reg.register_static(adapter)
    assert reg.get_static("  Python  ") is adapter
