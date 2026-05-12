import threading
from pathlib import Path

from grackle.adapters.base import RuntimeAdapter, StaticParserAdapter

_INVALID_LANGUAGE_CHARS = frozenset("\n\r\t\0\v\f")


def _canonical_language(language: str) -> str:
    """Strip + lowercase a language string for use as a registry key.

    Raises ValueError if the result is empty or contains control characters.
    Lookup helpers strip+lower silently and return None for non-matches.
    """
    key = language.strip().lower()
    if not key:
        raise ValueError("language must be non-empty after stripping whitespace")
    if any(c in _INVALID_LANGUAGE_CHARS for c in key):
        raise ValueError(
            f"language {language!r} contains control characters; use printable characters only"
        )
    return key


class AdapterRegistry:
    def __init__(self) -> None:
        self._static: dict[str, StaticParserAdapter] = {}
        self._runtime: dict[str, RuntimeAdapter] = {}
        self._lock = threading.Lock()

    def register_static(self, adapter: StaticParserAdapter) -> None:
        key = _canonical_language(adapter.language)
        with self._lock:
            if key in self._static:
                raise ValueError(f"static adapter for {key!r} already registered")
            self._static[key] = adapter

    def register_runtime(self, adapter: RuntimeAdapter) -> None:
        key = _canonical_language(adapter.language)
        with self._lock:
            if key in self._runtime:
                raise ValueError(f"runtime adapter for {key!r} already registered")
            self._runtime[key] = adapter

    def get_static(self, language: str) -> StaticParserAdapter | None:
        with self._lock:
            return self._static.get(language.strip().lower())

    def get_runtime(self, language: str) -> RuntimeAdapter | None:
        with self._lock:
            return self._runtime.get(language.strip().lower())

    def detect(self, project_root: Path) -> list[str]:
        with self._lock:
            items = list(self._static.items())
        return sorted(lang for lang, adapter in items if adapter.detect(project_root))

    def supported_languages(self) -> list[str]:
        with self._lock:
            return sorted(self._static.keys() | self._runtime.keys())


registry = AdapterRegistry()
