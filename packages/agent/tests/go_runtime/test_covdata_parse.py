"""Pure tests for covdata_parse.parse_textfmt — no Go toolchain required."""

from __future__ import annotations

from grackle.go_runtime.covdata_parse import parse_textfmt

_SAMPLE = """\
mode: count
example.com/tinyapp/main.go:10.13,14.2 3 1
example.com/tinyapp/models/user.go:15.13,17.2 1 1
example.com/tinyapp/models/user.go:19.1,21.2 1 0
example.com/tinyapp/services/service.go:12.29,14.2 1 5
"""


def test_basic_blocks() -> None:
    blocks = parse_textfmt(_SAMPLE)
    assert len(blocks) == 4


def test_import_paths() -> None:
    blocks = parse_textfmt(_SAMPLE)
    assert blocks[0]["import_path"] == "example.com/tinyapp/main.go"
    assert blocks[1]["import_path"] == "example.com/tinyapp/models/user.go"


def test_start_lines() -> None:
    blocks = parse_textfmt(_SAMPLE)
    assert blocks[0]["start_line"] == 10
    assert blocks[1]["start_line"] == 15
    assert blocks[2]["start_line"] == 19


def test_counts() -> None:
    blocks = parse_textfmt(_SAMPLE)
    assert blocks[0]["count"] == 1
    assert blocks[2]["count"] == 0  # count-0 block is preserved
    assert blocks[3]["count"] == 5


def test_header_skipped() -> None:
    blocks = parse_textfmt(_SAMPLE)
    assert not any(b["import_path"].startswith("mode") for b in blocks)


def test_blank_lines_skipped() -> None:
    text = "\n\nmode: count\n\nexample.com/m/a.go:1.1,2.2 1 3\n\n"
    blocks = parse_textfmt(text)
    assert len(blocks) == 1
    assert blocks[0]["count"] == 3


def test_malformed_line_skipped() -> None:
    text = "mode: count\nnot a valid line\nexample.com/m/a.go:1.1,2.2 1 1\n"
    blocks = parse_textfmt(text)
    assert len(blocks) == 1


def test_multi_dot_import_path() -> None:
    text = "mode: count\ngithub.com/foo/bar/baz.go:5.1,7.2 2 3\n"
    blocks = parse_textfmt(text)
    assert len(blocks) == 1
    assert blocks[0]["import_path"] == "github.com/foo/bar/baz.go"
    assert blocks[0]["start_line"] == 5


def test_empty_input() -> None:
    assert parse_textfmt("") == []


def test_only_header() -> None:
    assert parse_textfmt("mode: count\n") == []
