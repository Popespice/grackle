"""SQLite-backed session library for grackle trace sessions.

Stores metadata about completed trace sessions.  The JSONL file itself stays
on disk wherever it was written; only the path reference is persisted here.

WAL mode is enabled so readers do not block writers and vice-versa.  All
writes use ``INSERT OR REPLACE`` so ``save_session`` is idempotent — calling
it twice with the same ``id`` updates the record in place.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_DDL = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    ended_ns INTEGER NOT NULL,
    source_path TEXT NOT NULL,
    event_count INTEGER NOT NULL,
    language TEXT NOT NULL
);
"""


@dataclass
class SessionMeta:
    """Metadata for one recorded trace session."""

    id: str
    label: str
    started_ns: int
    ended_ns: int
    source_path: str  # POSIX path to the JSONL file
    event_count: int
    language: str  # open string per ADR-0004


class SessionStore:
    """SQLite-backed store for trace session metadata.

    Use ``SessionStore.open(db_path)`` to create or open a store.  The
    backing database is created (along with any missing parent directories)
    if it does not already exist.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @classmethod
    def open(cls, db_path: Path) -> SessionStore:
        """Open (or create) the SQLite store at *db_path*.

        Creates parent directories if they do not exist.  WAL journal mode is
        enabled immediately after opening so concurrent reads and writes do not
        block each other.
        """
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(_DDL)
        conn.commit()
        return cls(conn)

    def save_session(self, meta: SessionMeta) -> None:
        """Insert or replace a session record.

        Idempotent: calling with the same ``id`` updates the existing row.
        """
        self._conn.execute(
            """
            INSERT OR REPLACE INTO sessions
                (id, label, started_ns, ended_ns, source_path, event_count, language)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                meta.id,
                meta.label,
                meta.started_ns,
                meta.ended_ns,
                meta.source_path,
                meta.event_count,
                meta.language,
            ),
        )
        self._conn.commit()

    def list_sessions(self) -> list[SessionMeta]:
        """Return all sessions ordered by ``started_ns`` descending."""
        rows = self._conn.execute(
            """
            SELECT id, label, started_ns, ended_ns, source_path, event_count, language
            FROM sessions
            ORDER BY started_ns DESC
            """
        ).fetchall()
        return [
            SessionMeta(
                id=row[0],
                label=row[1],
                started_ns=row[2],
                ended_ns=row[3],
                source_path=row[4],
                event_count=row[5],
                language=row[6],
            )
            for row in rows
        ]

    def get_session(self, session_id: str) -> SessionMeta | None:
        """Return session by id, or ``None`` if not found."""
        row = self._conn.execute(
            """
            SELECT id, label, started_ns, ended_ns, source_path, event_count, language
            FROM sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        return SessionMeta(
            id=row[0],
            label=row[1],
            started_ns=row[2],
            ended_ns=row[3],
            source_path=row[4],
            event_count=row[5],
            language=row[6],
        )

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()
