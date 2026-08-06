"""Live-stream recording sink (ADR-0020 amendment, Phase 9.3 + Phase 12.0).

Tees a live ``--stream`` producer's trace events to a JSONL file on disk and
registers the finished session in the :class:`~grackle.session_store.SessionStore`
so it is loadable/seekable from the library — without buffering the whole
session in memory and without any wire-schema change.

The incremental-write mechanism (open ``.part`` exclusive-create, per-event
binary write, truncate-then-atomic-rename finalize) lives in
:class:`~grackle.python_runtime.writer.JsonlPartWriter` — extracted in
Phase 12.0 so the CLI's ``-o`` and ``--stream`` tee paths can reuse it too.
This class is a thin policy wrapper around that core: it never raises (a
recording failure must never disrupt the live fan-out it rides alongside),
discards an empty (zero-event) recording, and registers the finished session
in the store.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import TYPE_CHECKING

import structlog

from grackle.python_runtime.writer import JsonlPartWriter

if TYPE_CHECKING:
    from pathlib import Path
    from typing import Any, BinaryIO

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

    A thin policy wrapper around :class:`~grackle.python_runtime.writer.JsonlPartWriter`
    (Phase 12.0): never raises (both :meth:`write` and :meth:`finalize` catch
    and log), discards an empty (zero-event) recording — an unloadable
    zero-event session would pollute the library and break ``build_seekable``
    — and discards on any finalize failure too, since a lingering ``.part``
    would block a later same-id recording (exclusive-create would otherwise
    raise ``FileExistsError``).

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
        self._started_wall_ns = time.time_ns()
        self._finalized = False
        self._writer = JsonlPartWriter(recordings_dir / f"{session_id}.jsonl")

    # -- test seams (regression anchor: tests/test_recording_sink.py reaches
    # these five names directly and must not need to change) --------------
    @property
    def _f(self) -> BinaryIO:
        return self._writer._f  # noqa: SLF001

    @_f.setter
    def _f(self, value: BinaryIO) -> None:
        self._writer._f = value  # noqa: SLF001

    @property
    def _event_count(self) -> int:
        return self._writer.count

    @property
    def _broken(self) -> bool:
        return self._writer.broken

    @property
    def _tmp_path(self) -> Path:
        return self._writer.part_path

    @property
    def _final_path(self) -> Path:
        return self._writer.final_path

    def write(self, payload: dict[str, Any]) -> None:
        """Append one TraceEvent payload. Never raises — a recording failure
        must never disrupt the live fan-out it rides alongside."""
        try:
            self._writer.write(payload)
        except Exception as exc:
            log.warning(
                "recording sink: write failed — will salvage events written so far on finalize",
                session_id=self._session_id,
                error=str(exc),
            )

    async def finalize(self) -> None:
        """Finalize the recording and register the session.

        Idempotent. An empty recording (no events) is discarded. A recording
        broken by a failed :meth:`write` is salvaged by
        ``JsonlPartWriter.finalize`` (truncated to the last fully-written
        event) so the events already written and broadcast to consumers are
        not thrown away over one bad write. Any finalize failure discards
        the recording — like :meth:`write`, this method logs and moves on,
        never raises.
        """
        if self._finalized:
            return
        self._finalized = True

        if self._writer.count == 0:
            self._discard()
            return

        try:
            self._writer.finalize()
        except Exception as exc:
            log.warning(
                "recording sink: finalize failed — discarding recording",
                session_id=self._session_id,
                error=str(exc),
            )
            self._discard()
            return

        from grackle.session_store import SessionMeta

        meta = SessionMeta(
            id=self._session_id,
            label=f"live {self._session_id[:8]}",
            started_ns=self._started_wall_ns,
            ended_ns=time.time_ns(),
            source_path=str(self._writer.final_path.resolve()),
            event_count=self._writer.count,
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
            events=self._writer.count,
            path=str(self._writer.final_path),
        )

    def _discard(self) -> None:
        """Drop the recording, logging when the ``.part`` could not be removed.

        ``JsonlPartWriter.discard`` never raises, but a leftover ``.part``
        matters here: it blocks a later same-id recording (exclusive-create
        would raise ``FileExistsError``) until ``sweep_orphaned_recordings``
        clears it at the next server start, so the operator needs a line
        explaining why.
        """
        if not self._writer.discard():
            log.warning(
                "recording sink: cleanup unlink failed",
                session_id=self._session_id,
                path=str(self._writer.part_path),
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
