"""Lazy singleton parser loader for Tree-sitter grammars.

Each language's grammar is imported at most once (on first request); the
resulting Parser is cached and reused. If a grammar package cannot be
imported, the failure is logged and re-raised as LookupError so callers
can decide whether to skip adapter registration.

See ADR-0009 for grammar version pinning strategy and cross-OS wheel matrix.
"""

from __future__ import annotations

import importlib
import logging
import threading

from tree_sitter import Parser

_logger = logging.getLogger(__name__)

_parsers: dict[str, Parser] = {}
_failures: dict[str, Exception] = {}
_lock = threading.Lock()

# language-name → (module_name, factory_function_name)
_GRAMMAR_FACTORIES: dict[str, tuple[str, str]] = {
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "tsx": ("tree_sitter_typescript", "language_tsx"),
    "go": ("tree_sitter_go", "language"),
    "rust": ("tree_sitter_rust", "language"),
}


def _build_parser(language: str) -> Parser:
    from tree_sitter import Language

    if language not in _GRAMMAR_FACTORIES:
        raise LookupError(f"no tree-sitter grammar registered for {language!r}")

    module_name, fn_name = _GRAMMAR_FACTORIES[language]
    try:
        mod = importlib.import_module(module_name)
    except ImportError as exc:
        raise ImportError(
            f"tree-sitter grammar package {module_name!r} is not installed: {exc}"
        ) from exc

    fn = getattr(mod, fn_name, None)
    if fn is None:
        raise AttributeError(
            f"{module_name}.{fn_name} not found; grammar package may be the wrong version"
        )

    lang = Language(fn())
    return Parser(lang)


def get_parser(language: str) -> Parser:
    """Return a cached Parser for ``language``.

    Thread-safe. Parsers are constructed at most once per language per process.

    Raises:
        LookupError: if no grammar is registered for ``language`` or the
            grammar package failed to import (includes the original error).
    """
    with _lock:
        if language in _parsers:
            return _parsers[language]
        if language in _failures:
            exc = _failures[language]
            raise LookupError(
                f"tree-sitter grammar for {language!r} previously failed to load: {exc}"
            ) from exc

        try:
            parser = _build_parser(language)
        except (ImportError, LookupError, AttributeError) as exc:
            _logger.warning("tree-sitter grammar for %r unavailable: %s", language, exc)
            _failures[language] = exc
            raise LookupError(f"tree-sitter grammar for {language!r} unavailable: {exc}") from exc

        _parsers[language] = parser
        return parser


def _reset_for_testing() -> None:
    """Clear all cached parsers and failures. For test isolation only."""
    with _lock:
        _parsers.clear()
        _failures.clear()
