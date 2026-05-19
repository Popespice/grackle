"""Tests for python_runtime.adapter — PythonRuntimeAdapter registration and capabilities."""

from __future__ import annotations

from grackle.adapters import registry
from grackle.adapters.base import TraceOptions
from grackle.python_runtime.adapter import PythonRuntimeAdapter


def test_language_string() -> None:
    adapter = PythonRuntimeAdapter()
    assert adapter.language == "python"


def test_capabilities_runtime_tracing() -> None:
    adapter = PythonRuntimeAdapter()
    caps = adapter.capabilities()
    assert caps.runtime_tracing is True


def test_capabilities_static_flags() -> None:
    adapter = PythonRuntimeAdapter()
    caps = adapter.capabilities()
    assert caps.files is True
    assert caps.functions is True
    assert caps.calls is True


def test_registered_in_registry() -> None:
    """PythonRuntimeAdapter must be registered after grackle.__init__ import."""
    import grackle  # noqa: F401 — side-effect import triggers registration

    runtime = registry.get_runtime("python")
    assert runtime is not None
    assert isinstance(runtime, PythonRuntimeAdapter)


def test_supported_languages_includes_python() -> None:
    import grackle  # noqa: F401

    assert "python" in registry.supported_languages()


def test_trace_returns_iterator() -> None:
    """trace() must be iterable — Protocol requires Iterator[TraceEvent]."""
    from pathlib import Path

    adapter = PythonRuntimeAdapter()
    root = Path(__file__).parents[4] / "fixtures" / "tiny-python-app"
    script = root / "main.py"
    options = TraceOptions()
    result = adapter.trace(script, root, options)
    # Iterator protocol: must be iterable
    events = list(result)
    assert isinstance(events, list)
    assert len(events) > 0
