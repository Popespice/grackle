"""Tests for go_runtime.capability — no Go toolchain required (monkeypatched)."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from grackle.go_runtime import capability

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    capability.reset_cache()
    yield
    capability.reset_cache()


# ---------------------------------------------------------------------------
# go_executable
# ---------------------------------------------------------------------------


def test_go_executable_found(tmp_path: Any) -> None:
    fake = tmp_path / "go"
    fake.touch()
    with patch("shutil.which", return_value=str(fake)):
        capability.reset_cache()
        assert capability.go_executable() == str(fake)


def test_go_executable_not_found() -> None:
    with patch("shutil.which", return_value=None):
        capability.reset_cache()
        assert capability.go_executable() is None


# ---------------------------------------------------------------------------
# go_version parsing
# ---------------------------------------------------------------------------


def _mock_version(stdout: str) -> Any:
    return patch(
        "subprocess.run",
        return_value=subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr=""),
    )


def test_version_full() -> None:
    with patch("shutil.which", return_value="/usr/local/go/bin/go"):
        capability.reset_cache()
        with _mock_version("go version go1.21.5 linux/amd64\n"):
            assert capability.go_version() == (1, 21, 5)


def test_version_no_patch() -> None:
    with patch("shutil.which", return_value="/usr/local/go/bin/go"):
        capability.reset_cache()
        with _mock_version("go version go1.21 linux/amd64\n"):
            assert capability.go_version() == (1, 21, 0)


def test_version_rc() -> None:
    with patch("shutil.which", return_value="/usr/local/go/bin/go"):
        capability.reset_cache()
        with _mock_version("go version go1.22rc1 linux/amd64\n"):
            assert capability.go_version() == (1, 22, 0)


def test_version_no_go() -> None:
    with patch("shutil.which", return_value=None):
        capability.reset_cache()
        assert capability.go_version() is None


def test_version_subprocess_error() -> None:
    with patch("shutil.which", return_value="/usr/local/go/bin/go"):
        capability.reset_cache()
        with patch("subprocess.run", side_effect=OSError("no go")):
            assert capability.go_version() is None


def test_version_unparseable() -> None:
    with patch("shutil.which", return_value="/usr/local/go/bin/go"):
        capability.reset_cache()
        with _mock_version("something weird\n"):
            assert capability.go_version() is None


# ---------------------------------------------------------------------------
# go_runtime_available
# ---------------------------------------------------------------------------


def test_available_gate_open() -> None:
    with patch("shutil.which", return_value="/usr/local/go/bin/go"):
        capability.reset_cache()
        with _mock_version("go version go1.22.0 linux/amd64\n"):
            assert capability.go_runtime_available() is True


def test_available_gate_too_old() -> None:
    with patch("shutil.which", return_value="/usr/local/go/bin/go"):
        capability.reset_cache()
        with _mock_version("go version go1.19.3 linux/amd64\n"):
            assert capability.go_runtime_available() is False


def test_available_no_go() -> None:
    with patch("shutil.which", return_value=None):
        capability.reset_cache()
        assert capability.go_runtime_available() is False


# ---------------------------------------------------------------------------
# remediation_message branches
# ---------------------------------------------------------------------------


def test_remediation_no_go() -> None:
    with patch("shutil.which", return_value=None):
        capability.reset_cache()
        msg = capability.remediation_message()
        assert "not found on PATH" in msg
        assert "Install Go" in msg


def test_remediation_unparseable() -> None:
    with patch("shutil.which", return_value="/usr/local/go/bin/go"):
        capability.reset_cache()
        with _mock_version("???"):
            msg = capability.remediation_message()
            assert "could not determine its version" in msg


def test_remediation_too_old() -> None:
    with patch("shutil.which", return_value="/usr/local/go/bin/go"):
        capability.reset_cache()
        with _mock_version("go version go1.19.0 linux/amd64\n"):
            msg = capability.remediation_message()
            assert "too old" in msg
            assert "1.20" in msg
