"""Tests for the tree-sitter singleton parser loader."""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest

from grackle.tree_sitter_runtime import _reset_for_testing, get_parser


def setup_function() -> None:
    _reset_for_testing()


def teardown_module() -> None:
    _reset_for_testing()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_get_parser_returns_parser_for_typescript() -> None:
    from tree_sitter import Parser

    parser = get_parser("typescript")
    assert isinstance(parser, Parser)


def test_get_parser_returns_parser_for_go() -> None:
    from tree_sitter import Parser

    parser = get_parser("go")
    assert isinstance(parser, Parser)


def test_get_parser_caches_same_instance() -> None:
    p1 = get_parser("typescript")
    p2 = get_parser("typescript")
    assert p1 is p2


def test_get_parser_different_languages_are_different_instances() -> None:
    ts = get_parser("typescript")
    go = get_parser("go")
    assert ts is not go


def test_parser_can_parse_source() -> None:
    parser = get_parser("typescript")
    tree = parser.parse(b"const x: number = 1;")
    assert tree.root_node.type == "program"


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


def test_get_parser_thread_safety() -> None:
    """Multiple threads racing on the same language get the same parser instance."""
    results: list[object] = []
    barrier = threading.Barrier(4)

    def worker() -> None:
        barrier.wait()
        results.append(get_parser("typescript"))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 4
    assert all(r is results[0] for r in results)


# ---------------------------------------------------------------------------
# Graceful missing-grammar handling
# ---------------------------------------------------------------------------


def test_get_parser_raises_for_unknown_language() -> None:
    with pytest.raises(LookupError, match="no tree-sitter grammar registered"):
        get_parser("cobol")


def test_get_parser_raises_on_import_failure(caplog: pytest.LogCaptureFixture) -> None:
    import importlib

    original = importlib.import_module

    def broken(name: str, *args: object, **kwargs: object) -> object:
        if name == "tree_sitter_typescript":
            raise ImportError("simulated missing wheel")
        return original(name, *args, **kwargs)  # type: ignore[arg-type]

    with (
        patch("grackle.tree_sitter_runtime.importlib.import_module", side_effect=broken),
        pytest.raises(LookupError, match="unavailable"),
    ):
        get_parser("typescript")

    assert "typescript" in caplog.text


def test_failed_load_is_cached_no_retry() -> None:
    """A failed load is cached; subsequent calls raise without re-importing."""
    import importlib

    original = importlib.import_module
    call_count = 0

    def broken(name: str, *args: object, **kwargs: object) -> object:
        nonlocal call_count
        if name == "tree_sitter_typescript":
            call_count += 1
            raise ImportError("simulated missing wheel")
        return original(name, *args, **kwargs)  # type: ignore[arg-type]

    with patch("grackle.tree_sitter_runtime.importlib.import_module", side_effect=broken):
        with pytest.raises(LookupError):
            get_parser("typescript")
        with pytest.raises(LookupError):
            get_parser("typescript")

    assert call_count == 1


def test_failed_load_error_message_mentions_previous_failure() -> None:
    import importlib

    original = importlib.import_module

    def broken(name: str, *args: object, **kwargs: object) -> object:
        if name == "tree_sitter_typescript":
            raise ImportError("simulated missing wheel")
        return original(name, *args, **kwargs)  # type: ignore[arg-type]

    with patch("grackle.tree_sitter_runtime.importlib.import_module", side_effect=broken):
        with pytest.raises(LookupError):
            get_parser("typescript")
        # second call — cache hit path
        with pytest.raises(LookupError, match="previously failed"):
            get_parser("typescript")
