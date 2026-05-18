"""Go static parser — GoStaticParser auto-registered at import."""

from grackle.adapters import registry as _registry
from grackle.go_parser.adapter import GoStaticParser

_registry.register_static(GoStaticParser())

__all__ = ["GoStaticParser"]
