from __future__ import annotations

from typing import TYPE_CHECKING

from grackle.adapters.base import ParseOptions
from grackle.cache import CacheManager
from grackle.python_parser.walker import PythonAstWalker

if TYPE_CHECKING:
    from pathlib import Path

    from grackle.adapters.base import StaticGraph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_walker(root: Path, *, patterns: tuple[str, ...] = ()) -> PythonAstWalker:
    cache = CacheManager(root)
    opts = ParseOptions(exclude_patterns=patterns)
    return PythonAstWalker(root, opts, cache)


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


def test_empty_file(tmp_path: Path) -> None:
    _write(tmp_path, "empty.py", "")
    graph = _make_walker(tmp_path).walk()
    assert graph["version"] == 1
    assert graph["language"] == "python"
    file_nodes = [n for n in graph["nodes"] if n["kind"] == "file"]
    assert any(n["id"] == "empty.py" for n in file_nodes)


def test_single_class(tmp_path: Path) -> None:
    _write(tmp_path, "models.py", "class User:\n    pass\n")
    graph = _make_walker(tmp_path).walk()
    assert "models.py:User" in _ids(graph, "class")


def test_nested_class(tmp_path: Path) -> None:
    src = "class Outer:\n    class Inner:\n        pass\n"
    _write(tmp_path, "mod.py", src)
    graph = _make_walker(tmp_path).walk()
    assert "mod.py:Outer" in _ids(graph, "class")
    assert "mod.py:Outer.Inner" in _ids(graph, "class")


def test_top_level_function(tmp_path: Path) -> None:
    _write(tmp_path, "utils.py", "def helper():\n    pass\n")
    graph = _make_walker(tmp_path).walk()
    assert "utils.py:helper" in _ids(graph, "function")


def test_method_on_class(tmp_path: Path) -> None:
    src = "class Auth:\n    def login(self):\n        pass\n"
    _write(tmp_path, "auth.py", src)
    graph = _make_walker(tmp_path).walk()
    assert "auth.py:Auth.login" in _ids(graph, "method")


def test_async_function(tmp_path: Path) -> None:
    _write(tmp_path, "tasks.py", "async def run():\n    pass\n")
    graph = _make_walker(tmp_path).walk()
    func_nodes = [n for n in graph["nodes"] if n["id"] == "tasks.py:run"]
    assert func_nodes[0]["metadata"]["is_async"] is True


def test_decorator_chain(tmp_path: Path) -> None:
    src = "@staticmethod\n@property\ndef decorated():\n    pass\n"
    _write(tmp_path, "deco.py", src)
    graph = _make_walker(tmp_path).walk()
    func_nodes = [n for n in graph["nodes"] if n["id"] == "deco.py:decorated"]
    assert "staticmethod" in func_nodes[0]["metadata"]["decorators"]
    assert "property" in func_nodes[0]["metadata"]["decorators"]


def test_type_checking_import(tmp_path: Path) -> None:
    src = (
        "from __future__ import annotations\n"
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n"
        "    import heavy\n"
    )
    _write(tmp_path, "srv.py", src)
    graph = _make_walker(tmp_path).walk()
    heavy_edges = [e for e in graph["edges"] if e["target"] == "heavy"]
    assert len(heavy_edges) == 1
    assert heavy_edges[0]["metadata"].get("type_checking") is True


# ---------------------------------------------------------------------------
# Multi-file project
# ---------------------------------------------------------------------------


def test_multiple_files_aggregated(tmp_path: Path) -> None:
    _write(tmp_path, "a.py", "class A:\n    pass\n")
    _write(tmp_path, "b.py", "class B:\n    pass\n")
    graph = _make_walker(tmp_path).walk()
    class_ids = _ids(graph, "class")
    assert "a.py:A" in class_ids
    assert "b.py:B" in class_ids


def test_files_in_subdirectory(tmp_path: Path) -> None:
    _write(tmp_path, "pkg/sub.py", "def helper():\n    pass\n")
    graph = _make_walker(tmp_path).walk()
    assert "pkg/sub.py:helper" in _ids(graph, "function")


# ---------------------------------------------------------------------------
# Exclude patterns
# ---------------------------------------------------------------------------


def test_exclude_pattern_skips_file(tmp_path: Path) -> None:
    _write(tmp_path, "main.py", "class Main:\n    pass\n")
    _write(tmp_path, "test_main.py", "class TestMain:\n    pass\n")
    graph = _make_walker(tmp_path, patterns=("test_*.py",)).walk()
    class_ids = _ids(graph, "class")
    assert "main.py:Main" in class_ids
    assert "test_main.py:TestMain" not in class_ids


def test_exclude_pattern_by_path(tmp_path: Path) -> None:
    _write(tmp_path, "src/real.py", "class Real:\n    pass\n")
    _write(tmp_path, "src/skip.py", "class Skip:\n    pass\n")
    graph = _make_walker(tmp_path, patterns=("src/skip.py",)).walk()
    class_ids = _ids(graph, "class")
    assert "src/real.py:Real" in class_ids
    assert "src/skip.py:Skip" not in class_ids


# ---------------------------------------------------------------------------
# Cache behaviour
# ---------------------------------------------------------------------------


def test_cache_hit_on_second_walk(tmp_path: Path) -> None:
    _write(tmp_path, "cached.py", "class C:\n    pass\n")
    walker = _make_walker(tmp_path)
    graph1 = walker.walk()
    graph2 = walker.walk()
    # Both walks return the same nodes regardless of cache path taken.
    assert _ids(graph1, "class") == _ids(graph2, "class")


def test_modified_file_cache_miss(tmp_path: Path) -> None:
    path = _write(tmp_path, "changing.py", "class Old:\n    pass\n")
    _make_walker(tmp_path).walk()
    path.write_text("class New:\n    pass\n", encoding="utf-8")
    graph = _make_walker(tmp_path).walk()
    class_ids = _ids(graph, "class")
    assert "changing.py:New" in class_ids
    assert "changing.py:Old" not in class_ids


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_syntax_error_skipped_with_warning(tmp_path: Path) -> None:
    _write(tmp_path, "good.py", "class Good:\n    pass\n")
    _write(tmp_path, "bad.py", "def broken(\n")  # unterminated
    graph = _make_walker(tmp_path).walk()
    # Good file still produces nodes.
    assert "good.py:Good" in _ids(graph, "class")
    # Warning recorded.
    warnings = graph.get("metadata", {}).get("parse_warnings", [])
    assert any("bad.py" in w for w in warnings)


def test_graph_version_and_language(tmp_path: Path) -> None:
    _write(tmp_path, "x.py", "")
    graph = _make_walker(tmp_path).walk()
    assert graph["version"] == 1
    assert graph["language"] == "python"
