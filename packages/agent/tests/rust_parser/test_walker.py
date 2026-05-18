"""Tests for RustWalker."""

from __future__ import annotations

from pathlib import Path

from grackle.adapters.base import ParseOptions
from grackle.cache import CacheManager
from grackle.rust_parser.walker import RustWalker

FIXTURE = Path(__file__).parents[4] / "fixtures" / "tiny-rust-app"


def test_walker_returns_rust_language() -> None:
    cache = CacheManager(FIXTURE)
    graph = RustWalker(FIXTURE, ParseOptions(), cache).walk()
    assert graph["language"] == "rust"


def test_walker_finds_rs_files(tmp_path: Path) -> None:
    (tmp_path / "main.rs").write_text("fn main() {}")
    cache = CacheManager(tmp_path)
    graph = RustWalker(tmp_path, ParseOptions(), cache).walk()
    assert any(n["kind"] == "file" for n in graph["nodes"])


def test_walker_excludes_patterns(tmp_path: Path) -> None:
    (tmp_path / "main.rs").write_text("fn keep() {}")
    skip_dir = tmp_path / "target" / "debug"
    skip_dir.mkdir(parents=True)
    (skip_dir / "build.rs").write_text("fn skip() {}")
    cache = CacheManager(tmp_path)
    opts = ParseOptions(exclude_patterns=("target/*",))
    graph = RustWalker(tmp_path, opts, cache).walk()
    file_ids = {n["id"] for n in graph["nodes"] if n["kind"] == "file"}
    assert "main.rs" in file_ids
    assert not any("target" in fid for fid in file_ids)


def test_walker_cache_hit(tmp_path: Path) -> None:
    (tmp_path / "lib.rs").write_text("pub fn foo() {}")
    cache = CacheManager(tmp_path)
    opts = ParseOptions()
    g1 = RustWalker(tmp_path, opts, cache).walk()
    g2 = RustWalker(tmp_path, opts, cache).walk()
    assert len(g1["nodes"]) == len(g2["nodes"])
