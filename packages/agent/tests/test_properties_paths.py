"""Campaign T8-4 (docs/test-campaigns/phase-12.md): properties of
``grackle.paths.to_posix``, the one function every node id, cache key and
manifest entry goes through.

Generated over Unicode names, reserved Windows stems (``CON``, ``NUL``, …),
trailing dots and spaces, and long names, on real files created under a fresh
temp directory (a name the local filesystem cannot create, or would store
under a different name, is ``assume``-d away rather than asserted about):

- **Round trip.** The result joins the segments with ``/``, re-resolves to the
  same file, is relative, and has no empty, ``.`` or ``..`` segment.
- **No backslash.** On Windows ``\\`` is a separator, so it can never appear.
  On POSIX it is an ordinary filename character and passes through verbatim —
  the result contains one exactly when a segment does (so the invariant
  "never contains ``\\``" holds on POSIX only for backslash-free names).
- **Never escapes the root.** A path whose ``..`` segments leave the root
  raises ``ValueError``; one that stays inside returns the normalized path.
  A symlink out of the root raises; a symlink within it resolves to its
  target's id.

Finding T8-4 is ledgered at the bottom: ``to_posix`` raising on an escaping
symlink is correct, but none of the four static walkers catches it, so one
such symlink in a project aborts the whole parse.
"""

from __future__ import annotations

import itertools
import os
import posixpath
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from hypothesis import assume, example, given
from hypothesis import strategies as st

from grackle.adapters.base import ParseOptions, StaticParserAdapter
from grackle.go_parser.adapter import GoStaticParser
from grackle.paths import to_posix
from grackle.python_parser.adapter import PythonStaticParser
from grackle.rust_parser.adapter import RustStaticParser
from grackle.typescript_parser.adapter import TypeScriptStaticParser

if TYPE_CHECKING:
    from collections.abc import Callable

_LEDGER = "(docs/test-campaigns/phase-12.md)"
_WINDOWS = sys.platform == "win32"

_RESERVED_STEMS = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "CONIN$",
        "CONOUT$",
        *(
            f"{dev}{i}"
            for dev in ("COM", "LPT")
            for i in (*"123456789", "\u00b9", "\u00b2", "\u00b3")
        ),
    }
)
_WINDOWS_FORBIDDEN = frozenset('<>:"|?*\\') | {chr(c) for c in range(32)}


def _windows_unsafe(name: str) -> bool:
    """A name Windows reserves or cannot store as given. Never touched on
    Windows at all: opening ``PRN`` or ``COM1`` reaches a device, not a file."""
    stem = name.split(".", 1)[0].rstrip(" .").upper()
    return (
        stem in _RESERVED_STEMS
        or any(c in _WINDOWS_FORBIDDEN for c in name)
        or name.rstrip(" .") != name
    )


# --- segment names -----------------------------------------------------------

# No lone surrogates (not encodable) and no unassigned code points (APFS
# refuses them with EILSEQ, which would only feed the assume() below). On
# Windows, also none of the characters NTFS forbids (backslash included: it is
# a separator there, exercised through the separators instead).
_any_char = st.characters(
    exclude_categories=("Cs", "Cn"),
    exclude_characters="".join(sorted(_WINDOWS_FORBIDDEN)) + "/" if _WINDOWS else "/\x00",
)
_unicode_name = st.text(_any_char, min_size=1, max_size=16)
_reserved_name = st.builds(
    lambda stem, case, ext: (stem.lower() if case else stem) + ext,
    st.sampled_from(sorted(_RESERVED_STEMS)),
    st.booleans(),
    st.sampled_from(["", ".py", ".txt", ".tar.gz"]),
)
_trailing_name = st.builds(
    lambda base, tail: base + tail,
    st.text(st.sampled_from("ab_é"), min_size=1, max_size=6),
    st.sampled_from([".", " ", "..", ". ", " .", "  "]),
)
# At most 255 bytes whatever the encoding: ASCII up to 255 chars, and 3-byte
# CJK up to 85. Shorter on Windows, where the whole path stays under MAX_PATH.
_long_ascii, _long_cjk = ((60, 120), (20, 40)) if _WINDOWS else ((100, 255), (40, 85))
_long_name = st.one_of(
    st.text(st.sampled_from("abcxyz0189_-."), min_size=_long_ascii[0], max_size=_long_ascii[1]),
    st.text(st.sampled_from("热点计算"), min_size=_long_cjk[0], max_size=_long_cjk[1]),
)
# Reserved stems and trailing dots/spaces are names Windows cannot store as
# given, so they are POSIX-only probes; there they are ordinary names.
segment = (
    st.one_of(_unicode_name, _long_name)
    if _WINDOWS
    else st.one_of(_unicode_name, _reserved_name, _trailing_name, _long_name)
).filter(lambda s: s not in (".", ".."))

_case = itertools.count()
_SEPARATORS = ["/", "\\"] if _WINDOWS else ["/"]


def _names(directory: Path) -> set[str]:
    """The names a directory actually stores."""
    return {entry.name for entry in directory.iterdir()}


def _fresh_root(base: Path) -> Path:
    root = base / f"root{next(_case)}"
    root.mkdir()
    return root


def _create_chain(root: Path, segments: list[str], separators: list[str]) -> Path:
    """Create root/seg1/.../segN as directories plus a final file, joined with
    the given separators; ``assume`` away anything the filesystem refuses or
    would store under a different name."""
    if _WINDOWS:
        assume(not any(_windows_unsafe(s) or "\\" in s for s in segments))
    joined = segments[0]
    for sep, seg in zip(separators, segments[1:], strict=False):
        joined += sep + seg
    path = Path(str(root) + os.sep + joined)
    if _WINDOWS:
        assume(len(str(path)) < 240)  # stay clear of MAX_PATH on runners without long paths
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=False)
    except OSError:
        assume(False)
    parent = root
    for seg in segments:
        # The filesystem stored exactly this name (no Unicode normalization,
        # no trailing-dot stripping, no merge with an earlier segment).
        assume(seg in _names(parent))
        parent = parent / seg
    return path


@pytest.fixture(scope="module")
def base(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("t8_4")


# ===========================================================================
# Properties that hold
# ===========================================================================


@given(
    segments=st.lists(segment, min_size=1, max_size=3),
    separators=st.lists(st.sampled_from(_SEPARATORS), min_size=2, max_size=2),
)
def test_round_trip_on_real_files(base: Path, segments: list[str], separators: list[str]) -> None:
    root = _fresh_root(base)
    path = _create_chain(root, segments, separators)
    result = to_posix(path, root)
    assert result == "/".join(segments)
    assert (root / result).resolve() == path.resolve()
    assert not result.startswith("/")
    assert not any(part in ("", ".", "..") for part in result.split("/"))
    if _WINDOWS:
        assert "\\" not in result
    else:
        assert ("\\" in result) == any("\\" in s for s in segments)


_lexical = st.lists(st.sampled_from(["a", "b", "ab", ".", ".."]), min_size=1, max_size=7)


@given(parts=_lexical)
def test_dot_dot_never_escapes_the_root(base: Path, parts: list[str]) -> None:
    """Nothing exists below the root and there are no symlinks, so resolution
    is purely lexical: the answer is ``posixpath.normpath`` of the parts, or
    ``ValueError`` when that climbs out."""
    root = _fresh_root(base)
    expected = posixpath.normpath("/".join(parts))
    path = root.joinpath(*parts)
    if expected == ".." or expected.startswith("../"):
        with pytest.raises(ValueError):
            to_posix(path, root)
    else:
        assert to_posix(path, root) == expected


def _symlinks_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except OSError as exc:
        # Only "this account may not create symlinks at all" (Windows without
        # Developer Mode, ERROR_PRIVILEGE_NOT_HELD) skips the test. Any other
        # failure belongs to this one generated example — a name the filesystem
        # already holds beside the link — so it is not an example, and the
        # property keeps running.
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip(f"symlinks unavailable: {exc}")
        assume(False)


@given(inner=_unicode_name, leaf=_unicode_name)
def test_a_symlink_resolves_to_its_target_or_raises_when_it_leaves(
    base: Path, inner: str, leaf: str
) -> None:
    # casefold: on a case-insensitive filesystem (APFS, NTFS) "L_IN" is "l_in".
    assume(
        inner.casefold() not in ("l_in", "l_out")
        and inner not in (".", "..")
        and leaf not in (".", "..")
    )
    if _WINDOWS:
        assume(not (_windows_unsafe(inner) or _windows_unsafe(leaf)))
    root = _fresh_root(base)
    outside = _fresh_root(base)
    try:
        (root / inner).mkdir()
        (root / inner / leaf).touch()
        (outside / leaf).touch()
    except OSError:
        assume(False)
    assume(inner in _names(root) and leaf in _names(root / inner))
    _symlinks_or_skip(root / "l_in", root / inner)
    _symlinks_or_skip(root / "l_out", outside)
    assert to_posix(root / "l_in" / leaf, root) == f"{inner}/{leaf}"
    with pytest.raises(ValueError):
        to_posix(root / "l_out" / leaf, root)


# ===========================================================================
# Finding T8-4 (ledgered): one escaping symlink aborts the whole static parse
# ===========================================================================

_PARSERS: dict[str, tuple[Callable[[], StaticParserAdapter], str]] = {
    "py": (PythonStaticParser, "def f():\n    return 1\n"),
    "ts": (TypeScriptStaticParser, "export function f() { return 1; }\n"),
    "go": (GoStaticParser, "package main\n\nfunc f() int { return 1 }\n"),
    "rs": (RustStaticParser, "fn f() -> i32 { 1 }\n"),
}


@pytest.mark.xfail(
    strict=True,
    raises=ValueError,
    reason=(
        "T8-4: every static walker calls to_posix on each globbed file with no "
        "guard, so a single symlink resolving outside the project root raises "
        "ValueError out of parse() — grackle parse/serve fail for the whole "
        f"project (the watcher already skips such links) {_LEDGER}"
    ),
)
@given(
    ext=st.sampled_from(sorted(_PARSERS)),
    link=st.text(st.sampled_from("abz_"), min_size=1, max_size=6),
    depth=st.integers(0, 2),
)
@example(ext="go", link="_", depth=0)
def test_static_parse_survives_a_symlink_that_leaves_the_root(
    base: Path, ext: str, link: str, depth: int
) -> None:
    parser_cls, source = _PARSERS[ext]
    root = _fresh_root(base)
    outside = _fresh_root(base)
    (root / f"main.{ext}").write_text(source, encoding="utf-8")
    (outside / f"shared.{ext}").write_text(source, encoding="utf-8")
    where = root.joinpath(*(["pkg"] * depth))
    where.mkdir(parents=True, exist_ok=True)
    _symlinks_or_skip(where / f"{link}.{ext}", outside / f"shared.{ext}")
    graph = parser_cls().parse(root, ParseOptions())
    ids = {node["id"] for node in graph["nodes"]}
    assert any(i.startswith(f"main.{ext}") for i in ids)
    assert not any(f"{link}.{ext}" in i for i in ids)
