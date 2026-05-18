"""Tests for GoWalker."""

from __future__ import annotations

from typing import TYPE_CHECKING

from grackle.adapters.base import ParseOptions
from grackle.cache import CacheManager
from grackle.go_parser.walker import GoWalker

if TYPE_CHECKING:
    from pathlib import Path


def test_walker_returns_static_graph(tmp_path: Path) -> None:
    (tmp_path / "main.go").write_text("package main\n\nfunc main() {}\n")
    walker = GoWalker(tmp_path, ParseOptions(), CacheManager(tmp_path))
    graph = walker.walk()
    assert graph["language"] == "go"
    assert graph["version"] == 1
    assert isinstance(graph["nodes"], list)
    assert isinstance(graph["edges"], list)


def test_walker_emits_file_and_function(tmp_path: Path) -> None:
    (tmp_path / "main.go").write_text('package main\n\nfunc Greet() string { return "hello" }\n')
    walker = GoWalker(tmp_path, ParseOptions(), CacheManager(tmp_path))
    graph = walker.walk()
    ids = {n["id"] for n in graph["nodes"]}
    assert "main.go" in ids
    assert "main.go:Greet" in ids


def test_walker_excludes_patterns(tmp_path: Path) -> None:
    (tmp_path / "main.go").write_text("package main\nfunc main() {}\n")
    (tmp_path / "vendor.go").write_text("package main\nfunc Vendor() {}\n")
    walker = GoWalker(
        tmp_path, ParseOptions(exclude_patterns=("vendor.go",)), CacheManager(tmp_path)
    )
    graph = walker.walk()
    ids = {n["id"] for n in graph["nodes"]}
    assert "main.go" in ids
    assert "vendor.go" not in ids


def test_walker_handles_parse_error_gracefully(tmp_path: Path) -> None:
    (tmp_path / "bad.go").write_bytes(b"\xff\xfe invalid utf-8 \x00")
    walker = GoWalker(tmp_path, ParseOptions(), CacheManager(tmp_path))
    graph = walker.walk()
    # Should not raise; graph may be empty or have warnings
    assert isinstance(graph["nodes"], list)
