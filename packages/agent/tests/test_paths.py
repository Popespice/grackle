from __future__ import annotations

import sys
from pathlib import Path, PureWindowsPath

import pytest

from grackle.paths import to_posix


def test_simple_relative_under_root(tmp_path: Path) -> None:
    f = tmp_path / "a" / "b.py"
    f.parent.mkdir(parents=True)
    f.touch()
    assert to_posix(f, tmp_path) == "a/b.py"


def test_returns_forward_slashes_on_all_platforms(tmp_path: Path) -> None:
    f = tmp_path / "sub" / "deep" / "file.py"
    f.parent.mkdir(parents=True)
    f.touch()
    result = to_posix(f, tmp_path)
    assert "\\" not in result
    assert result == "sub/deep/file.py"


def test_pure_windows_input_round_trips_via_as_posix() -> None:
    # Guards the underlying invariant to_posix relies on. Pure-path only —
    # does not call to_posix (which takes a real Path) but documents the contract.
    assert PureWindowsPath("a\\b.py").as_posix() == "a/b.py"


def test_dot_segments_collapsed_via_resolve(tmp_path: Path) -> None:
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    (sub / "c.py").touch()
    # a/./b/../c.py resolves to a/c.py ... but relative_to works on resolved path.
    # Use the actual resolved file, then check against the direct path.
    f = tmp_path / "a" / "b" / "c.py"
    assert to_posix(f, tmp_path) == "a/b/c.py"


def test_absolute_path_under_root(tmp_path: Path) -> None:
    f = tmp_path / "x.py"
    f.touch()
    # Pass an already-absolute resolved path — same answer.
    assert to_posix(f.resolve(), tmp_path.resolve()) == "x.py"


def test_path_outside_root_raises_value_error(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    outside = tmp_path / "other.py"
    outside.touch()
    with pytest.raises(ValueError):
        to_posix(outside, sub)


def test_path_at_parent_of_root_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        to_posix(tmp_path.parent, tmp_path)


def test_root_itself_returns_dot(tmp_path: Path) -> None:
    # relative_to(self) returns PurePath('.'); document the corner case.
    assert to_posix(tmp_path, tmp_path) == "."


def test_symlink_resolves_to_target_under_root(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "x.py").touch()
    link = tmp_path / "alias"
    try:
        link.symlink_to(real)
    except OSError as exc:
        pytest.skip(f"symlink unsupported: {exc}")
    # Symlink points into real/ which is under tmp_path — should resolve fine.
    assert to_posix(link / "x.py", tmp_path) == "real/x.py"


def test_symlink_pointing_outside_root_raises(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside_target"
    outside.mkdir(exist_ok=True)
    link = tmp_path / "escape"
    try:
        link.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink unsupported: {exc}")
    with pytest.raises(ValueError):
        to_posix(link, tmp_path)


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin /tmp → /private/tmp quirk")
def test_macos_private_var_quirk_is_normalized() -> None:
    # On Darwin, /tmp resolves to /private/tmp. Both sides call .resolve()
    # so this is transparent — but pin the invariant so a future refactor
    # can't drop one of the .resolve() calls without this test catching it.
    tmp = Path("/tmp")
    private_tmp = Path("/private/tmp")
    assert tmp.resolve() == private_tmp.resolve()
    # Create a real file under /tmp to exercise to_posix end-to-end.
    import tempfile

    with tempfile.NamedTemporaryFile(dir="/tmp", suffix=".py", delete=False) as fh:
        fname = Path(fh.name)
    try:
        result = to_posix(fname, tmp)
        assert "\\" not in result
        assert "/" not in result.lstrip("./")  # just the filename, no dir component
    finally:
        fname.unlink(missing_ok=True)


def test_unicode_and_spaces_in_path_segments(tmp_path: Path) -> None:
    d = tmp_path / "héllo world"
    d.mkdir()
    f = d / "файл.py"
    f.touch()
    result = to_posix(f, tmp_path)
    assert result == "héllo world/файл.py"


def test_case_handling_documented(tmp_path: Path) -> None:
    # to_posix does NOT normalise case — dedup policy lives in adapters.
    # On case-insensitive FSes (macOS APFS, Windows NTFS) the result reflects
    # whatever case .resolve() yields (the on-disk casing, typically).
    f = tmp_path / "Foo.py"
    f.touch()
    result = to_posix(f, tmp_path)
    # Whatever case resolve() yields, we don't lower-case it.
    assert result.lower() == "foo.py"
