"""Tests for the Cargo workspace reader."""

from __future__ import annotations

from pathlib import Path

from grackle.rust_parser.workspace import get_crates, read_workspace


def test_read_workspace_none_no_cargo_toml(tmp_path: Path) -> None:
    assert read_workspace(tmp_path) is None


def test_read_workspace_none_no_workspace_section(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "foo"\n')
    assert read_workspace(tmp_path) is None


def test_read_workspace_empty_members(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text("[workspace]\nmembers = []\n")
    result = read_workspace(tmp_path)
    assert result == []


def test_read_workspace_members(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers = ["crates/alpha", "crates/beta"]\n')
    alpha = tmp_path / "crates" / "alpha"
    alpha.mkdir(parents=True)
    (alpha / "Cargo.toml").write_text('[package]\nname = "alpha"\n')
    beta = tmp_path / "crates" / "beta"
    beta.mkdir(parents=True)
    (beta / "Cargo.toml").write_text('[package]\nname = "beta"\n')

    result = read_workspace(tmp_path)
    assert result is not None
    names = {c.name for c in result}
    assert names == {"alpha", "beta"}
    posix_roots = {c.posix_root for c in result}
    assert "crates/alpha" in posix_roots
    assert "crates/beta" in posix_roots


def test_read_workspace_glob_members(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers = ["crates/*"]\n')
    for name in ("one", "two"):
        d = tmp_path / "crates" / name
        d.mkdir(parents=True)
        (d / "Cargo.toml").write_text(f'[package]\nname = "{name}"\n')

    result = read_workspace(tmp_path)
    assert result is not None
    assert len(result) == 2
    assert {c.name for c in result} == {"one", "two"}


def test_read_workspace_missing_member_cargo_toml(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers = ["crates/alpha"]\n')
    # don't create the member dir — should silently skip
    result = read_workspace(tmp_path)
    assert result == []


def test_get_crates_single_crate_fallback(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "mylib"\n')
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("pub fn foo() {}")

    crates = get_crates(tmp_path)
    assert len(crates) == 1
    assert crates[0].name == "mylib"
    assert crates[0].posix_root == ""


def test_get_crates_workspace(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text('[workspace]\nmembers = ["a"]\n')
    a = tmp_path / "a"
    a.mkdir()
    (a / "Cargo.toml").write_text('[package]\nname = "a"\n')

    crates = get_crates(tmp_path)
    assert len(crates) == 1
    assert crates[0].name == "a"


def test_tiny_rust_app_fixture() -> None:
    fixture = Path(__file__).parents[4] / "fixtures" / "tiny-rust-app"
    assert fixture.exists(), "tiny-rust-app fixture not found"

    crates = get_crates(fixture)
    assert len(crates) == 3
    names = {c.name for c in crates}
    assert names == {"models", "api", "app"}
    roots = {c.posix_root for c in crates}
    assert "crates/models" in roots
    assert "crates/api" in roots
    assert "crates/app" in roots
