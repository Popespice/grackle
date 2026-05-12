import threading
from pathlib import Path

from grackle.adapters.base import RuntimeAdapter, StaticParserAdapter


class AdapterRegistry:
    def __init__(self) -> None:
        self._static: dict[str, StaticParserAdapter] = {}
        self._runtime: dict[str, RuntimeAdapter] = {}
        self._lock = threading.Lock()

    def register_static(self, adapter: StaticParserAdapter) -> None:
        key = adapter.language.lower()
        with self._lock:
            if key in self._static:
                raise ValueError(f"static adapter for {key!r} already registered")
            self._static[key] = adapter

    def register_runtime(self, adapter: RuntimeAdapter) -> None:
        key = adapter.language.lower()
        with self._lock:
            if key in self._runtime:
                raise ValueError(f"runtime adapter for {key!r} already registered")
            self._runtime[key] = adapter

    def get_static(self, language: str) -> StaticParserAdapter | None:
        with self._lock:
            return self._static.get(language.lower())

    def get_runtime(self, language: str) -> RuntimeAdapter | None:
        with self._lock:
            return self._runtime.get(language.lower())

    def detect(self, project_root: Path) -> list[str]:
        with self._lock:
            adapters = list(self._static.values())
        return sorted(
            adapter.language.lower() for adapter in adapters if adapter.detect(project_root)
        )

    def supported_languages(self) -> list[str]:
        with self._lock:
            return sorted(self._static.keys() | self._runtime.keys())


registry = AdapterRegistry()
