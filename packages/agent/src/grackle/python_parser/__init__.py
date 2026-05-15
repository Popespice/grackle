"""Python static parser — PythonStaticParser auto-registered at import."""

from grackle.adapters import registry as _registry
from grackle.python_parser.adapter import PythonStaticParser

_registry.register_static(PythonStaticParser())

__all__ = ["PythonStaticParser"]
