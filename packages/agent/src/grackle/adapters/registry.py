import threading
from pathlib import Path
from typing import Any

from grackle.adapters.base import ParseOptions, RuntimeAdapter, StaticGraph, StaticParserAdapter

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

    def parse_all(self, root: Path, options: ParseOptions) -> StaticGraph:
        """Detect all languages, parse each, and return a merged graph.

        Sets ``graph.language`` to the sorted ``+``-joined detected languages and
        stores per-language node counts in ``graph.metadata.languages``.
        Cross-language edges (HTTP routes, subprocess refs) are resolved from
        per-language hints before the combined graph is returned.

        Raises ValueError if no language is detected.
        """
        from grackle.cross_language import resolve_cross_language_edges

        detected = self.detect(root)
        if not detected:
            raise ValueError(f"no static parsers detected for project at: {root}")

        all_nodes: list[Any] = []
        all_edges: list[Any] = []
        all_hints: list[Any] = []
        language_counts: dict[str, int] = {}

        for lang in detected:
            adapter = self.get_static(lang)
            if adapter is None:
                continue
            graph = adapter.parse(root, options)
            all_nodes.extend(graph["nodes"])
            all_edges.extend(graph["edges"])
            language_counts[lang] = len(graph["nodes"])
            metadata = graph.get("metadata") or {}
            all_hints.extend(metadata.get("cross_language_hints", []))

        cross_edges = resolve_cross_language_edges(all_hints, all_nodes)
        all_edges.extend(cross_edges)

        combined_lang = "+".join(sorted(detected))
        result: StaticGraph = {
            "version": 1,
            "language": combined_lang,
            "nodes": all_nodes,
            "edges": all_edges,
            "metadata": {"languages": language_counts},
        }
        return result


registry = AdapterRegistry()
