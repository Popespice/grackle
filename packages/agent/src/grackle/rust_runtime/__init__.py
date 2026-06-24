"""Rust runtime adapter — RustRuntimeAdapter auto-registered at import (ADR-0024)."""

from grackle.adapters import registry as _registry
from grackle.rust_runtime.adapter import RustRuntimeAdapter
from grackle.rust_runtime.errors import RustRuntimeError

_registry.register_runtime(RustRuntimeAdapter())

__all__ = ["RustRuntimeAdapter", "RustRuntimeError"]
