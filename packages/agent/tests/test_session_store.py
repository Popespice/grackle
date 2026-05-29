"""Tests for grackle.session_store (Phase 8.3).

Design notes:
- All fixtures use tmp_path for hermetic file isolation.
- The public API (open / save_session / list_sessions / get_session / close)
  is tested as a black box.
- Idempotency of save_session is verified via repeated saves with the same id.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from grackle.session_store import SessionMeta, SessionStore

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_meta(
    id: str,
    label: str = "test session",
    started_ns: int = 1_000_000_000,
    ended_ns: int = 2_000_000_000,
    source_path: str = "traces/out.jsonl",
    event_count: int = 42,
    language: str = "python",
) -> SessionMeta:
    return SessionMeta(
        id=id,
        label=label,
        started_ns=started_ns,
        ended_ns=ended_ns,
        source_path=source_path,
        event_count=event_count,
        language=language,
    )


# ---------------------------------------------------------------------------
# test_open_creates_db
# ---------------------------------------------------------------------------


def test_open_creates_db(tmp_path: Path) -> None:
    """Store opens and creates the SQLite file (including parent dirs)."""
    db_path = tmp_path / "subdir" / "sessions.db"
    assert not db_path.exists()
    store = SessionStore.open(db_path)
    store.close()
    assert db_path.exists()


# ---------------------------------------------------------------------------
# test_save_and_list
# ---------------------------------------------------------------------------


def test_save_and_list(tmp_path: Path) -> None:
    """Save two sessions, list returns both ordered by started_ns descending."""
    db_path = tmp_path / "sessions.db"
    store = SessionStore.open(db_path)

    meta1 = _make_meta("id-1", started_ns=1_000, label="first")
    meta2 = _make_meta("id-2", started_ns=2_000, label="second")
    store.save_session(meta1)
    store.save_session(meta2)

    sessions = store.list_sessions()
    store.close()

    assert len(sessions) == 2
    # Most recent first (started_ns desc)
    assert sessions[0].id == "id-2"
    assert sessions[0].label == "second"
    assert sessions[1].id == "id-1"
    assert sessions[1].label == "first"


# ---------------------------------------------------------------------------
# test_get_session
# ---------------------------------------------------------------------------


def test_get_session(tmp_path: Path) -> None:
    """get_session retrieves by id; returns None for unknown id."""
    db_path = tmp_path / "sessions.db"
    store = SessionStore.open(db_path)

    meta = _make_meta("abc-123", event_count=99, language="go")
    store.save_session(meta)

    found = store.get_session("abc-123")
    assert found is not None
    assert found.id == "abc-123"
    assert found.event_count == 99
    assert found.language == "go"

    missing = store.get_session("does-not-exist")
    assert missing is None

    store.close()


# ---------------------------------------------------------------------------
# test_idempotent_save
# ---------------------------------------------------------------------------


def test_idempotent_save(tmp_path: Path) -> None:
    """Saving same id twice updates the record (INSERT OR REPLACE)."""
    db_path = tmp_path / "sessions.db"
    store = SessionStore.open(db_path)

    original = _make_meta("dup-id", event_count=10, label="original")
    store.save_session(original)

    updated = _make_meta("dup-id", event_count=99, label="updated")
    store.save_session(updated)

    sessions = store.list_sessions()
    assert len(sessions) == 1  # only one row
    assert sessions[0].event_count == 99
    assert sessions[0].label == "updated"

    fetched = store.get_session("dup-id")
    assert fetched is not None
    assert fetched.event_count == 99

    store.close()


# ---------------------------------------------------------------------------
# test_all_fields_round_trip
# ---------------------------------------------------------------------------


def test_all_fields_round_trip(tmp_path: Path) -> None:
    """All SessionMeta fields survive a save/get round-trip."""
    db_path = tmp_path / "sessions.db"
    store = SessionStore.open(db_path)

    original = SessionMeta(
        id="rt-1",
        label="round-trip test",
        started_ns=123_456_789,
        ended_ns=987_654_321,
        source_path="data/trace.jsonl",
        event_count=1234,
        language="typescript",
    )
    store.save_session(original)
    fetched = store.get_session("rt-1")
    store.close()

    assert fetched is not None
    assert fetched.id == original.id
    assert fetched.label == original.label
    assert fetched.started_ns == original.started_ns
    assert fetched.ended_ns == original.ended_ns
    assert fetched.source_path == original.source_path
    assert fetched.event_count == original.event_count
    assert fetched.language == original.language
