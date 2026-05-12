import dataclasses
from pathlib import Path, PurePosixPath, PureWindowsPath

from grackle.adapters.base import (
    Capabilities,
    ParseOptions,
    RuntimeAdapter,
    StaticParserAdapter,
)
from grackle.adapters.noop import NoOpRuntimeAdapter, NoOpStaticParser


def test_noop_static_is_static_parser_adapter() -> None:
    assert isinstance(NoOpStaticParser(), StaticParserAdapter)


def test_noop_runtime_is_runtime_adapter() -> None:
    assert isinstance(NoOpRuntimeAdapter(), RuntimeAdapter)


def test_noop_capabilities_all_false() -> None:
    caps = NoOpStaticParser().capabilities()
    assert caps == Capabilities()
    assert not any(dataclasses.asdict(caps).values())


def test_noop_static_parse_returns_empty_graph(tmp_path: Path) -> None:
    parser = NoOpStaticParser()
    graph = parser.parse(tmp_path, ParseOptions())
    assert graph["version"] == 1
    assert graph["language"] == "noop"
    assert graph["nodes"] == []
    assert graph["edges"] == []


def test_path_normalization_cross_platform() -> None:
    windows = PureWindowsPath("src\\foo\\bar.py")
    posix = PurePosixPath("src/foo/bar.py")
    assert windows.as_posix() == "src/foo/bar.py"
    assert posix.as_posix() == "src/foo/bar.py"
    assert windows.as_posix() == posix.as_posix()
