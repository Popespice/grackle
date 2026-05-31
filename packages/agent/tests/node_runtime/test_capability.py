"""Tests for the Node toolchain capability gate (ADR-0022).

No real Node needed: the detection functions are monkeypatched to simulate
present/absent/old toolchains. The version-parsing path is exercised by faking
``shutil.which`` + ``subprocess.run`` and clearing the detection cache.
"""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

import pytest

from grackle.node_runtime import capability

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _clean_cache() -> Iterator[None]:
    """Ensure detection caches don't leak between tests (or from process start)."""
    capability.reset_cache()
    yield
    capability.reset_cache()


def test_available_when_node_new_enough(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capability, "node_version", lambda: (24, 0, 0))
    assert capability.node_runtime_available() is True


def test_unavailable_when_node_too_old(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capability, "node_version", lambda: (22, 5, 0))
    assert capability.node_runtime_available() is False


def test_unavailable_when_node_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capability, "node_version", lambda: None)
    assert capability.node_runtime_available() is False


def test_min_version_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capability, "node_version", lambda: capability.MIN_NODE_VERSION)
    assert capability.node_runtime_available() is True


@pytest.mark.parametrize(
    ("version", "needs_flag"),
    [
        ((22, 6, 0), True),
        ((23, 5, 9), True),
        ((23, 6, 0), False),  # type stripping on by default
        ((24, 12, 0), False),
    ],
)
def test_needs_strip_types_flag(version: tuple[int, int, int], needs_flag: bool) -> None:
    assert capability.needs_strip_types_flag(version) is needs_flag


def test_remediation_message_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capability, "node_executable", lambda: None)
    monkeypatch.setattr(capability, "node_version", lambda: None)
    msg = capability.remediation_message()
    assert "not found" in msg
    assert "22.6" in msg


def test_remediation_message_too_old(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capability, "node_executable", lambda: "/usr/bin/node")
    monkeypatch.setattr(capability, "node_version", lambda: (20, 0, 0))
    msg = capability.remediation_message()
    assert "too old" in msg
    assert "20.0.0" in msg


def test_remediation_message_unparseable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(capability, "node_executable", lambda: "/usr/bin/node")
    monkeypatch.setattr(capability, "node_version", lambda: None)
    msg = capability.remediation_message()
    assert "could not determine its version" in msg


def test_version_parsing_from_subprocess(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/node")

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=["node", "--version"], returncode=0, stdout="v22.9.0\n"
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    capability.reset_cache()
    assert capability.node_version() == (22, 9, 0)


def test_version_probe_failure_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/node")

    def boom(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise OSError("cannot exec")

    monkeypatch.setattr(subprocess, "run", boom)
    capability.reset_cache()
    assert capability.node_version() is None


def test_version_nonzero_exit_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/node")

    def fail(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=["node"], returncode=1, stdout="")

    monkeypatch.setattr(subprocess, "run", fail)
    capability.reset_cache()
    assert capability.node_version() is None
