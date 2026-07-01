"""Live-stream recording sink (ADR-0020 amendment, Phase 9.3).

Tees a live ``--stream`` producer's trace events to a JSONL file on disk and
registers the finished session in the :class:`~grackle.session_store.SessionStore`
so it is loadable/seekable from the library — without buffering the whole
session in memory and without any wire-schema change.

The ``.part`` -> final rename follows the project's atomic-write convention
(name-append, ``Path.replace``; see ``python_runtime/writer.py``).

The recording file is written in **binary** mode and each event is encoded to
UTF-8 bytes with an explicit ``\\n`` terminator. Binary mode avoids text-mode
newline translation (so the byte offset we track is exact on every platform,
including Windows), lets us advance ``_last_good_offset`` by ``len(encoded)``
without a per-event ``tell()`` flush on the hot path, and makes the
broken-write salvage a single ``truncate(offset)``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from typing import TYPE_CHECKING, Any, BinaryIO

import structlog

if TYPE_CHECKING:
    from pathlib import Path

    from grackle.session_store import SessionStore

log = structlog.get_logger()

# Conservative allow-list for a producer-supplied session_id used as a single
# filename segment. First char excludes '.' and '-' (no hidden files, no
# argv-injection if a path is later handed to a CLI); the rest allows
# alphanumerics plus '.', '_', '-'; capped at 128 chars. A uuid4 (hex + '-')
# passes. Note: Windows reserved device stems (CON/NUL/PRN/AUX/COMn/LPTn) still
# match this pattern — they cannot escape recordings_dir, so the worst case is a
# failed exclusive-create that is caught and logged, not a path-escape.
_SAFE_SESSION_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$")


def is_safe_session_id(session_id: str) -> bool:
    """True if *session_id* is safe to use as a single-segment filename.

    Producer session_ids are untrusted wire input from a local client.
    Rejects anything that could escape ``recordings_dir`` via a path
    separator or a relative/parent segment, plus leading dots/dashes and
    overly long ids.
    """
    return bool(_SAFE_SESSION_ID.match(session_id))


class RecordingSink:
    """Incrementally writes one live trace session to ``<id>.jsonl``.

    Created when a ``trace_session_start`` arrives on a connection while the
    server has a session store; fed one ``TraceEvent`` payload per
    ``trace_event`` via :meth:`write`; closed via :meth:`finalize` on
    ``trace_session_end``, producer disconnect, or server shutdown.

    ``finalize`` is idempotent and safe to call from a ``finally`` block under
    cancellation: the truncate + close + rename happen synchronously before the
    only await (the store write), so a torn finalize still leaves a valid
    ``.jsonl`` on disk.

    Raises:
        FileExistsError: if *session_id* is already being recorded by
            another connection (the ``.part`` file already exists) — opening
            in exclusive-create mode makes a same-id collision fail loudly
            instead of silently truncating the other recorder's file.
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
        self._last_good_offset = 0
        self._finalized = False
        self._broken = False
        self._f: BinaryIO = self._tmp_path.open("xb")

    def write(self, payload: dict[str, Any]) -> None:
        """Append one TraceEvent payload. Never raises — a recording failure
        must never disrupt the live fan-out it rides alongside."""
        if self._broken:
            return
        try:
            data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
            self._f.write(data)
            # Advance count and offset together, only on a fully successful
            # write, so they can never disagree (a partial/failed write leaves
            # both untouched and the salvage truncates the partial bytes away).
            self._event_count += 1
            self._last_good_offset += len(data)
        except Exception as exc:
            self._broken = True
            log.warning(
                "recording sink: write failed — will salvage events written so far on finalize",
                session_id=self._session_id,
                error=str(exc),
            )

    async def finalize(self) -> None:
        """Truncate any partial tail, atomically rename, and register the session.

        Idempotent. An empty recording (no events) is discarded — an
        unloadable zero-event session would pollute the library and break
        ``build_seekable``. A recording broken by a failed :meth:`write` is
        salvaged: the file is truncated to ``_last_good_offset`` (the end of
        the last fully-written event) so the events already written and
        broadcast to consumers are not thrown away over one bad write. Every
        step (truncate, close, rename, store write) is independently guarded so
        a transient failure here can never propagate out and disrupt the
        connection's receive loop — like :meth:`write`, this method logs and
        moves on, never raises.
        """
        if self._finalized:
            return
        self._finalized = True

        if self._event_count == 0:
            self._close_and_discard()
            return

        if self._broken:
            try:
                self._f.truncate(self._last_good_offset)
            except Exception as exc:
                log.warning(
                    "recording sink: could not salvage partial recording — discarding",
                    session_id=self._session_id,
                    error=str(exc),
                )
                self._close_and_discard()
                return

        try:
            self._f.close()
        except OSError as exc:
            log.warning(
                "recording sink: close failed — discarding recording",
                session_id=self._session_id,
                error=str(exc),
            )
            self._unlink_tmp()
            return

        try:
            self._tmp_path.replace(self._final_path)
        except OSError as exc:
            log.warning(
                "recording sink: finalize rename failed — discarding recording",
                session_id=self._session_id,
                error=str(exc),
            )
            # Unlink the orphaned .part so it does not block a later same-id
            # recording (exclusive-create would otherwise raise FileExistsError).
            self._unlink_tmp()
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
        try:
            await loop.run_in_executor(None, self._store.save_session, meta)
        except Exception as exc:
            log.warning(
                "recording sink: save_session failed — file written but not registered",
                session_id=self._session_id,
                error=str(exc),
            )
            return
        log.info(
            "live session recorded",
            session_id=self._session_id,
            events=self._event_count,
            path=str(self._final_path),
        )

    def _close_and_discard(self) -> None:
        with contextlib.suppress(OSError):
            self._f.close()
        self._unlink_tmp()

    def _unlink_tmp(self) -> None:
        try:
            self._tmp_path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning(
                "recording sink: cleanup unlink failed",
                session_id=self._session_id,
                error=str(exc),
            )


def sweep_orphaned_recordings(recordings_dir: Path, *, min_age_s: float = 30.0) -> None:
    """Delete stale ``.part`` files left by a hard server kill.

    No reliable event count or timestamps survive a kill, so an orphan is
    never finalized into the store — only removed. ``min_age_s`` guards
    against deleting a ``.part`` file actively being written by a
    concurrently-starting recording (e.g. a second ``serve --store`` run
    sharing the same store directory) — only files older than the threshold
    are swept. This is a best-effort heuristic, not an ownership protocol;
    a still-active but idle recording older than the threshold could in
    principle be swept by a concurrent peer (acceptable for the local-first,
    single-instance norm).
    """
    if not recordings_dir.is_dir():
        return
    now = time.time()
    for part in recordings_dir.glob("*.jsonl.part"):
        try:
            age_s = now - part.stat().st_mtime
        except OSError:
            continue
        if age_s < min_age_s:
            continue
        log.info(
            "recording sink: removing orphaned .part file", path=str(part), age_s=round(age_s, 1)
        )
        part.unlink(missing_ok=True)
