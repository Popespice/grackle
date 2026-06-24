"""Tests for rust_runtime.capability — no Rust toolchain required (monkeypatched)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from grackle.rust_runtime import capability

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    capability.reset_cache()
    yield
    capability.reset_cache()


def _proc(stdout: str = "", returncode: int = 0) -> Any:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


# ---------------------------------------------------------------------------
# cargo_executable / rustc_executable
# ---------------------------------------------------------------------------


def test_cargo_found(tmp_path: Path) -> None:
    fake = tmp_path / "cargo"
    fake.touch()
    with patch("shutil.which", return_value=str(fake)):
        capability.reset_cache()
        assert capability.cargo_executable() == str(fake)


def test_cargo_not_found() -> None:
    with patch("shutil.which", return_value=None):
        capability.reset_cache()
        assert capability.cargo_executable() is None


def test_rustc_found(tmp_path: Path) -> None:
    fake = tmp_path / "rustc"
    fake.touch()
    with patch("shutil.which", return_value=str(fake)):
        capability.reset_cache()
        assert capability.rustc_executable() == str(fake)


# ---------------------------------------------------------------------------
# rustc_version
# ---------------------------------------------------------------------------


def test_version_stable() -> None:
    with patch("shutil.which", return_value="/usr/bin/rustc"):
        capability.reset_cache()
        with patch("subprocess.run", return_value=_proc("rustc 1.75.0 (82e1608df 2023-12-21)")):
            assert capability.rustc_version() == (1, 75, 0)


def test_version_nightly() -> None:
    with patch("shutil.which", return_value="/usr/bin/rustc"):
        capability.reset_cache()
        with patch("subprocess.run", return_value=_proc("rustc 1.80.0-nightly (abc 2024-05-01)")):
            assert capability.rustc_version() == (1, 80, 0)


def test_version_no_rustc() -> None:
    with patch("shutil.which", return_value=None):
        capability.reset_cache()
        assert capability.rustc_version() is None


def test_version_subprocess_error() -> None:
    with patch("shutil.which", return_value="/usr/bin/rustc"):
        capability.reset_cache()
        with patch("subprocess.run", side_effect=OSError("gone")):
            assert capability.rustc_version() is None


def test_version_unparseable() -> None:
    with patch("shutil.which", return_value="/usr/bin/rustc"):
        capability.reset_cache()
        with patch("subprocess.run", return_value=_proc("something weird")):
            assert capability.rustc_version() is None


# ---------------------------------------------------------------------------
# host_triple
# ---------------------------------------------------------------------------


def test_host_triple_parsed() -> None:
    vv_output = "rustc 1.75.0\nbinary: rustc\nhost: aarch64-apple-darwin\nrelease: 1.75.0\n"
    with patch("shutil.which", return_value="/usr/bin/rustc"):
        capability.reset_cache()
        with patch("subprocess.run", return_value=_proc(vv_output)):
            assert capability.host_triple() == "aarch64-apple-darwin"


def test_host_triple_no_rustc() -> None:
    with patch("shutil.which", return_value=None):
        capability.reset_cache()
        assert capability.host_triple() is None


# ---------------------------------------------------------------------------
# rust_runtime_available
# ---------------------------------------------------------------------------


def _patch_all_present(
    *,
    cargo: str = "/usr/bin/cargo",
    rustc: str = "/usr/bin/rustc",
    version: str = "rustc 1.75.0",
    sysroot: str = "/sysroot",
    host: str = "x86_64-unknown-linux-gnu",
    profdata_exists: bool = True,
    llvm_cov_exists: bool = True,
) -> Any:
    """Context manager that makes all capability probes succeed."""
    import contextlib

    @contextlib.contextmanager
    def _cm() -> Iterator[None]:
        profdata_path = Path(sysroot) / "lib" / "rustlib" / host / "bin" / "llvm-profdata"
        llvm_cov_path = Path(sysroot) / "lib" / "rustlib" / host / "bin" / "llvm-cov"

        def fake_which(name: str) -> str | None:
            if name == "cargo":
                return cargo
            if name == "rustc":
                return rustc
            return None

        def fake_run(cmd: list[str], **kwargs: Any) -> Any:
            joined = " ".join(str(c) for c in cmd)
            if "--version" in joined and "rustc" in joined:
                return _proc(version)
            if "--print" in joined and "sysroot" in joined:
                return _proc(sysroot)
            if "-vV" in joined:
                return _proc(f"host: {host}\n")
            return _proc()

        def fake_exists(self: Path) -> bool:
            if self == profdata_path:
                return profdata_exists
            if self == llvm_cov_path:
                return llvm_cov_exists
            return False

        capability.reset_cache()
        with (
            patch("shutil.which", side_effect=fake_which),
            patch("subprocess.run", side_effect=fake_run),
            patch.object(Path, "exists", fake_exists),
        ):
            capability.reset_cache()
            yield

    return _cm()


def test_available_all_present() -> None:
    with _patch_all_present():
        assert capability.rust_runtime_available() is True


def test_available_no_cargo() -> None:
    with patch("shutil.which", return_value=None):
        capability.reset_cache()
        assert capability.rust_runtime_available() is False


def test_available_too_old() -> None:
    with _patch_all_present(version="rustc 1.59.0"):
        assert capability.rust_runtime_available() is False


def test_available_no_profdata() -> None:
    with _patch_all_present(profdata_exists=False):
        assert capability.rust_runtime_available() is False


def test_available_no_llvm_cov() -> None:
    with _patch_all_present(llvm_cov_exists=False):
        assert capability.rust_runtime_available() is False


# ---------------------------------------------------------------------------
# remediation_message branches
# ---------------------------------------------------------------------------


def test_remediation_no_cargo() -> None:
    with patch("shutil.which", return_value=None):
        capability.reset_cache()
        msg = capability.remediation_message()
        assert "Rust was not found on PATH" in msg
        assert "rustup" in msg


def test_remediation_too_old() -> None:
    with _patch_all_present(version="rustc 1.59.0"):
        msg = capability.remediation_message()
        assert "too old" in msg
        assert "1.60" in msg


def test_remediation_no_tools() -> None:
    with _patch_all_present(profdata_exists=False, llvm_cov_exists=False):
        msg = capability.remediation_message()
        assert "llvm-tools-preview" in msg


# ---------------------------------------------------------------------------
# build_rustflags
# ---------------------------------------------------------------------------


def test_build_rustflags_empty_env() -> None:
    flags = capability.build_rustflags({})
    assert flags == "-Cinstrument-coverage"


def test_build_rustflags_existing() -> None:
    flags = capability.build_rustflags({"RUSTFLAGS": "-Copt-level=0"})
    assert flags == "-Copt-level=0 -Cinstrument-coverage"
