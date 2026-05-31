"""Node/V8 runtime adapter — NodeRuntimeAdapter auto-registered at import (ADR-0022)."""

from grackle.adapters import registry as _registry
from grackle.node_runtime.adapter import NodeRuntimeAdapter
from grackle.node_runtime.errors import NodeRuntimeError

_registry.register_runtime(NodeRuntimeAdapter())

__all__ = ["NodeRuntimeAdapter", "NodeRuntimeError"]
