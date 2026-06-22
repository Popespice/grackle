"""Go runtime adapter — GoRuntimeAdapter auto-registered at import (ADR-0023)."""

from grackle.adapters import registry as _registry
from grackle.go_runtime.adapter import GoRuntimeAdapter
from grackle.go_runtime.errors import GoRuntimeError

_registry.register_runtime(GoRuntimeAdapter())

__all__ = ["GoRuntimeAdapter", "GoRuntimeError"]
