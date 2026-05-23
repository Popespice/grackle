"""Python runtime adapter — PythonRuntimeAdapter auto-registered at import."""

from grackle.adapters import registry as _registry
from grackle.python_runtime.adapter import PythonRuntimeAdapter

_registry.register_runtime(PythonRuntimeAdapter())

__all__ = ["PythonRuntimeAdapter"]
