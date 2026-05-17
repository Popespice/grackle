"""TypeScript static parser — TypeScriptStaticParser auto-registered at import."""

from grackle.adapters import registry as _registry
from grackle.typescript_parser.adapter import TypeScriptStaticParser

_registry.register_static(TypeScriptStaticParser())

__all__ = ["TypeScriptStaticParser"]
