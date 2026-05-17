"""Tests for TSWalker."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from grackle.adapters.base import ParseOptions
from grackle.cache import CacheManager
from grackle.typescript_parser.walker import TSWalker

if TYPE_CHECKING:
    from grackle.adapters.base import StaticGraph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_walker(root: Path, *, patterns: tuple[str, ...] = ()) -> TSWalker:
    cache = CacheManager(root)
    opts = ParseOptions(exclude_patterns=patterns)
    return TSWalker(root, opts, cache)


def _write(root: Path, rel: str, source: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return path


def _ids(graph: StaticGraph, kind: str) -> set[str]:
    return {n["id"] for n in graph["nodes"] if n["kind"] == kind}


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------


def test_walk_empty_dir_returns_empty_graph(tmp_path: Path) -> None:
    graph = _make_walker(tmp_path).walk()
    assert graph["version"] == 1
    assert graph["language"] == "typescript"
    assert graph["nodes"] == []
    assert graph["edges"] == []


def test_walk_single_ts_file(tmp_path: Path) -> None:
    _write(tmp_path, "index.ts", "export function hello(): void {}")
    graph = _make_walker(tmp_path).walk()
    assert "index.ts" in _ids(graph, "file")
    assert "index.ts:hello" in _ids(graph, "function")


def test_walk_tsx_file(tmp_path: Path) -> None:
    _write(tmp_path, "App.tsx", "export function App(): null { return null; }")
    graph = _make_walker(tmp_path).walk()
    assert "App.tsx" in _ids(graph, "file")


def test_walk_multiple_files(tmp_path: Path) -> None:
    _write(tmp_path, "a.ts", "export class A {}")
    _write(tmp_path, "b.ts", "export class B {}")
    graph = _make_walker(tmp_path).walk()
    assert len(_ids(graph, "file")) == 2
    assert "a.ts:A" in _ids(graph, "class")
    assert "b.ts:B" in _ids(graph, "class")


# ---------------------------------------------------------------------------
# Exclusion
# ---------------------------------------------------------------------------


def test_walk_excludes_pattern(tmp_path: Path) -> None:
    _write(tmp_path, "src/main.ts", "export class Main {}")
    _write(tmp_path, "node_modules/lib.ts", "export class Lib {}")
    graph = _make_walker(tmp_path, patterns=("node_modules/*",)).walk()
    assert "src/main.ts" in _ids(graph, "file")
    assert not any("node_modules" in n["id"] for n in graph["nodes"])


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


def test_walk_result_is_cached(tmp_path: Path) -> None:
    _write(tmp_path, "index.ts", "export class X {}")
    walker = _make_walker(tmp_path)
    graph1 = walker.walk()

    # Second walker with shared cache dir should hit the cache
    walker2 = _make_walker(tmp_path)
    graph2 = walker2.walk()

    ids1 = {n["id"] for n in graph1["nodes"]}
    ids2 = {n["id"] for n in graph2["nodes"]}
    assert ids1 == ids2


# ---------------------------------------------------------------------------
# POSIX IDs
# ---------------------------------------------------------------------------


def test_node_ids_are_posix(tmp_path: Path) -> None:
    _write(tmp_path, "nested/deep/file.ts", "export class Foo {}")
    graph = _make_walker(tmp_path).walk()
    for node in graph["nodes"]:
        assert "\\" not in node["id"]
