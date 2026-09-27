"""The CLI's two session-library entry points against a corrupt database.

Campaign C4, probe T6-1 (``docs/test-campaigns/phase-12.md``). ``grackle serve
--store`` and ``grackle learn --from-store`` both call ``SessionStore.open`` with
no error handling, so a file that is not a SQLite database surfaces as a raw
``sqlite3.DatabaseError`` traceback. Reproduced against the real entry point
(subprocess, exit 1): the last lines of stderr are the traceback ending in
``session_store.py ... conn.execute("PRAGMA journal_mode=WAL")`` /
``sqlite3.DatabaseError: file is not a database``. The file itself is left
byte-for-byte untouched — that half is correct today, and the tests hold a fix
to it: the library is user data, so the fix must report, never recreate.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner, Result

from grackle import ml_bridge
from grackle import server as grackle_server
from grackle.cli import main

if TYPE_CHECKING:
    from pathlib import Path

_NOT_A_DATABASE = b"this is not a SQLite database\n" * 64


@pytest.fixture
def corrupt_db(tmp_path: Path) -> Path:
    db = tmp_path / "sessions.db"
    db.write_bytes(_NOT_A_DATABASE)
    return db


@pytest.fixture(autouse=True)
def _no_global_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    """``serve`` reconfigures structlog process-wide and then runs the server
    forever; neither may happen inside the test process. A fix that degrades
    to "serve without the store" reaches the stub and returns at once."""
    monkeypatch.setattr("grackle.cli.configure_logging", lambda *a, **k: None)

    async def _no_serve(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(grackle_server, "serve", _no_serve)


def _assert_reported_cleanly(result: Result, db: Path) -> None:
    assert not isinstance(result.exception, sqlite3.Error), (
        f"unhandled {type(result.exception).__name__}: {result.exception} — "
        "the real CLI prints a Python traceback here"
    )
    assert db.name in result.output, "the message should name the offending file"
    assert db.read_bytes() == _NOT_A_DATABASE, "the user's library file was modified"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T6-1: `grackle serve --store` on a corrupt database dies with an "
        "unhandled sqlite3.DatabaseError traceback (docs/test-campaigns/phase-12.md)"
    ),
)
def test_serve_reports_a_corrupt_store_without_a_traceback(
    tmp_path: Path, corrupt_db: Path
) -> None:
    result = CliRunner().invoke(
        main, ["serve", "--root", str(tmp_path), "--store", str(corrupt_db), "--port", "0"]
    )
    _assert_reported_cleanly(result, corrupt_db)


@pytest.mark.skipif(not ml_bridge.learn_available(), reason="grackle_nn is not installed")
@pytest.mark.xfail(
    strict=True,
    reason=(
        "T6-1: `grackle learn --from-store` on a corrupt database dies with an "
        "unhandled sqlite3.DatabaseError traceback (docs/test-campaigns/phase-12.md)"
    ),
)
def test_learn_reports_a_corrupt_store_without_a_traceback(
    tmp_path: Path, corrupt_db: Path
) -> None:
    result = CliRunner().invoke(
        main, ["learn", "--root", str(tmp_path), "--from-store", str(corrupt_db)]
    )
    _assert_reported_cleanly(result, corrupt_db)
