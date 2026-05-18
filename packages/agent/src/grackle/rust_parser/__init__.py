"""Rust static parser — RustStaticParser auto-registered at import."""

from grackle.adapters import registry as _registry
from grackle.rust_parser.adapter import RustStaticParser

_registry.register_static(RustStaticParser())

__all__ = ["RustStaticParser"]
