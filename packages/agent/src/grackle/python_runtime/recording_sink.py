"""Live-stream recording sink (ADR-0020 amendment, Phase 9.3).

Tees a live ``--stream`` producer's trace events to a JSONL file on disk and
registers the finished session in the :class:`~grackle.session_store.SessionStore`
so it is loadable/seekable from the library — without buffering the whole
session in memory and without any wire-schema change.

The ``.part`` -> final rename follows the project's atomic-write convention
(name-append, ``Path.replace``; see ``python_runtime/writer.py``).
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any, TextIO

import structlog

if TYPE_CHECKING:
    from pathlib import Path

    from grackle.session_store import SessionStore

log = structlog.get_logger()


class RecordingSink:
    """Incrementally writes one live trace session to ``<id>.jsonl``.

    Created when a ``trace_session_start`` arrives on a connection while the
    server has a session store; fed one ``TraceEvent`` payload per
    ``trace_event`` via :meth:`write`; closed via :meth:`finalize` on
    ``trace_session_end``, producer disconnect, or server shutdown.

    ``finalize`` is idempotent and safe to call from a ``finally`` block under
    cancellation: the file close + rename happen synchronously before the
    only await (the store write), so a torn finalize still leaves a valid
    ``.jsonl`` on disk.
    """

    def __init__(
        self,
        recordings_dir: Path,
        session_id: str,
        store: SessionStore,
        language: str,
    ) -> None:
        self._session_id = session_id
        self._store = store
        self._language = language
        self._final_path = recordings_dir / f"{session_id}.jsonl"
        self._tmp_path = recordings_dir / f"{session_id}.jsonl.part"
        self._started_wall_ns = time.time_ns()
        self._event_count = 0
        self._finalized = False
        self._broken = False
        self._f: TextIO = self._tmp_path.open("w", encoding="utf-8")

    def write(self, payload: dict[str, Any]) -> None:
        """Append one TraceEvent payload. Never raises — a recording failure
        must never disrupt the live fan-out it rides alongside."""
        if self._broken:
            return
        try:
            self._f.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._event_count += 1
        except Exception as exc:
            self._broken = True
            log.warning(
                "recording sink: write failed — recording disabled for this session",
                session_id=self._session_id,
                error=str(exc),
            )

    async def finalize(self) -> None:
        """Close, atomically rename, and register the session in the store.

        Idempotent. Skips the store write (and deletes the file) for an empty
        or broken recording — an unloadable zero-event session would pollute
        the library and break ``build_seekable``.
        """
        if self._finalized:
            return
        self._finalized = True

        # Close then replace — synchronous, before any await, so the rename
        # survives even if the awaited store write below is interrupted by a
        # subsequent cancellation. Windows requires the handle closed before
        # the file can be replaced.
        try:
            self._f.close()
        except Exception as exc:
            log.warning(
                "recording sink: close failed",
                session_id=self._session_id,
                error=str(exc),
            )
            self._broken = True

        if self._broken or self._event_count == 0:
            self._tmp_path.unlink(missing_ok=True)
            return

        try:
            self._tmp_path.replace(self._final_path)
        except OSError as exc:
            log.warning(
                "recording sink: finalize rename failed",
                session_id=self._session_id,
                error=str(exc),
            )
            return

        from grackle.session_store import SessionMeta

        meta = SessionMeta(
            id=self._session_id,
            label=f"live {self._session_id[:8]}",
            started_ns=self._started_wall_ns,
            ended_ns=time.time_ns(),
            source_path=str(self._final_path.resolve()),
            event_count=self._event_count,
            language=self._language,
        )
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._store.save_session, meta)
        log.info(
            "live session recorded",
            session_id=self._session_id,
            events=self._event_count,
            path=str(self._final_path),
        )


def sweep_orphaned_recordings(recordings_dir: Path) -> None:
    """Delete stale ``.part`` files left by a hard server kill.

    No reliable event count or timestamps survive a kill, so an orphan is
    never finalized into the store — only removed (or, if a finished sibling
    ``.jsonl`` already exists, removed in favor of the finished file).
    """
    if not recordings_dir.is_dir():
        return
    for part in recordings_dir.glob("*.jsonl.part"):
        log.info("recording sink: removing orphaned .part file", path=str(part))
        part.unlink(missing_ok=True)
