# ADR-0020 — Trace persistence and session store

**Status:** Accepted  
**Date:** 2026-05-29  
**Phase:** 8.3

---

## Context

The ring buffer in `server.py` is ephemeral: completed live sessions and file-replay sessions are gone when the server restarts.  The plan for Phase 8 identified persistence as a prerequisite for differential analysis (8.4): you cannot compare run A vs run B unless A was saved somewhere.

The tee sink (`--stream --output FILE`, shipped as PR #30) already solves "capture to file while streaming."  What remained is:
1. A **durable index** of captured sessions so they can be listed and re-loaded.
2. A **server-side flag** (`--store`) that hooks into live sessions and records their metadata.
3. A **`SessionLibraryPanel`** in the frontend for browsing and loading stored sessions.

---

## Decision

### `SessionStore` — stdlib `sqlite3`, WAL mode

`packages/agent/src/grackle/session_store.py`.  SQLite chosen because:
- Zero new runtime dependencies (stdlib `sqlite3`).
- WAL mode (`PRAGMA journal_mode=WAL`) enables concurrent reads without blocking writes.  WAL alone does not make a single `sqlite3.Connection` safe under truly concurrent calls, so every access (`save_session` / `list_sessions` / `get_session` / `close`) is additionally serialized through a `threading.Lock` — making the store safe to call from asyncio executor threads.
- The session record is small metadata only; the JSONL blobs stay on disk at their existing path.  The store is an index, not a blob store.

Schema:
```sql
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    ended_ns INTEGER NOT NULL,
    source_path TEXT NOT NULL,
    event_count INTEGER NOT NULL,
    language TEXT NOT NULL
);
```

`save_session` uses `INSERT OR REPLACE` — idempotent re-saves on server restart for the same session id.

Store location: `--store PATH` (e.g. `.grackle/sessions.db`).  The `SessionStore.open()` classmethod creates parent directories and the database if needed.  Local-first invariant holds: no path outside the user's machine is ever used.

### `grackle serve --store PATH`

New CLI option on the `serve` command.  When provided:
- `SessionStore.open(path)` is called before the server starts.
- The store reference is threaded through `serve()` → `_handler` closure → `_receive_loop`.
- `_receive_loop` handles `session_list_request` and `session_load_request`.
- The store is `close()`d when `serve()` exits (idempotent), so WAL state is flushed cleanly on shutdown.

**Production write path.**  When both `--store` and `--trace-source` are given, the trace file is indexed into the store at startup (`_register_trace_source` → `save_session`).  The id is a stable `uuid5` over the file's absolute path, so re-serving the same file updates one row rather than accumulating duplicates.  This gives `save_session` a real caller, populates the library, and lets the trace be re-loaded after a restart (without re-passing `--trace-source`).  `source_path` is the absolute local path, read back via `Path` at load time.

**Concurrency.**  `session_list` / `session_load` (and the startup write) are serialized through a `threading.Lock` inside `SessionStore` and the read paths are offloaded to `run_in_executor`, so SQLite is never touched concurrently and never blocks the event loop.

~~Auto-saving a *live* `--stream` producer (rather than a replay file) is still future work~~ — implemented in Phase 9.3; see the Phase 9.3 Amendment section below.

### New message types

```
session_list_request   {}                            → session_list_response
session_list_response  { sessions: SessionMeta[] }
session_load_request   { session_id }                → trace_session_start (seekable=true)
```

`session_load_request` does not have a dedicated response type — the agent replies with the existing `trace_session_start (seekable=true)` + `trace_session_end` sequence, making the loaded session indistinguishable from a file-replay session.  On load, the agent builds a `JsonlIndex` **and** a `TraceAggregates` for the file (one pass via `build_seekable`) and registers them under the loaded session's id in the server's per-session `seekable_sessions` map.  Because both seek **and** aggregate queries resolve against that map (not a single file-replay id), the entire seek/aggregate machinery — including cumulative-heat — works on loaded sessions identically to a `--trace-source` replay.  A `session_load_request` that cannot be served (no `--store`, unknown id, or a missing source file) is logged and ignored.

`SessionMeta` shape (both wire and `SessionStore`):
```
{ id, label, started_ns, ended_ns, source_path, event_count, language }
```

### `SessionLibraryPanel`

`packages/frontend/src/panels/SessionLibraryPanel.tsx`.  Registered in `panels/init.ts` (right-dock, order 90).

Behaviour:
- On connect, calls `requestSessionList()` and renders the results.
- Each session row shows label, event count, and language.  Clicking calls `sendSessionLoad(session_id)`.
- "Refresh" button re-fetches the list (useful after a new session is saved).
- Empty state distinguishes "no sessions" from "server has no --store" (both return an empty list — the message guides the user to use `--store`).

---

## Amendment — Phase 9.3 (2026-06-30)

The live-stream recording sink deferred above is now implemented: `packages/agent/src/grackle/python_runtime/recording_sink.py` (`RecordingSink`).  When `serve --store PATH` is active, every inbound producer session (`trace_session_start` / `trace_event*` / `trace_session_end`) is tee'd to `<store_dir>/recordings/<session_id>.jsonl` and registered via the existing `save_session` — no wire-schema change, the sink only consumes the three message types ADR-0014 already defined.

Finalization (close the `.jsonl.part`, atomically `Path.replace()` it, and call `save_session`) fires on whichever of three events happens first: a clean `trace_session_end`, the producer's WebSocket disconnecting without one (handled in `_receive_loop`'s `finally`), or the server itself shutting down mid-stream (the `finally` finalize is wrapped in `asyncio.shield` so the outer cancellation cannot tear the close+rename before it completes). A zero-event session (producer connected and dropped before any `trace_event`) is finalized to a deleted, unregistered file rather than polluting the library with an unloadable row. A startup sweep (`sweep_orphaned_recordings`) removes `.part` files left behind by a prior hard kill.

This closes the negative/known-limit item below — "auto-saving a live `--stream` producer to the store is not yet wired" no longer applies.

## Amendment — Phase 12.0 (2026-08-05)

The write-then-atomically-rename mechanism `RecordingSink` introduced in Phase 9.3 was policy-free at its core but entangled with server/store concerns (asyncio, `structlog`, session registration). Phase 12.0 extracts that core into `packages/agent/src/grackle/python_runtime/writer.py::JsonlPartWriter` — open `<final>.part` exclusive-create, write one JSON line per event with no per-event flush/fsync, track the byte offset of the last fully-written event, and `finalize()` by truncating any torn tail, closing, and atomically `Path.replace()`-ing into place — so the `grackle trace` CLI's `-o` and `--stream --output` (tee) paths can reuse the identical mechanism, not just the server's live-session recorder.

**Flush policy, stated explicitly:** there is still no per-event `flush()`/`fsync()`. Two distinct buffers sit between an event and the platter, and it matters which one each failure mode costs you:

1. Python's userspace `BufferedWriter` (8 KiB by default) — **lost on SIGKILL/crash**, because the process dies before it can hand those bytes to the kernel. At typical event sizes that is a tail of tens of events.
2. The kernel page cache — **survives process death** (the write already reached the kernel), but is lost on power loss or an OS crash.

So the durability target is *process-kill minus the last write-buffer's worth*, and explicitly **not** power-loss. Documentation must not round this up to "every event written so far survives" — the CLI help and README state the buffered-tail caveat. This was already true of `RecordingSink`; Phase 12.0 generalizes the mechanism without changing it. A coarse time- or count-based flush (bounding the loss window without a syscall per event) is possible future work, not built — the hot path is a `sys.monitoring` callback, so a per-event `flush()` would add a `write(2)` to every traced call and return.

**Byte format.** Every JSONL emitter in the agent — `write_jsonl`, `JsonlPartWriter`, and therefore `RecordingSink` — writes UTF-8 bytes with an explicit `\n`, never text mode. `write_jsonl` previously used `Path.write_text`, which applies universal-newline translation and so emitted CRLF on Windows; that made the same `grackle trace -o` produce different bytes per OS and diverged from `RecordingSink` (LF everywhere since Phase 9.3). Phase 12.0 makes it binary, so byte-identity between the one-shot and incremental writers holds on every platform and grackle only ever *emits* LF. `read_jsonl` still accepts either.

**Keep-vs-discard policy split.** `JsonlPartWriter` itself is policy-free: `write()`/`finalize()` raise on I/O failure and `finalize()` leaves the `.part` file in place on failure — it has no opinion on what a caller should do next. The two callers diverge deliberately: `RecordingSink` (server-side, a disposable recording) discards on any failure, including an empty (zero-event) session, so a broken or unloadable row never pollutes the session library. The `grackle trace -o`/`--stream` CLI paths (agent-scoped, no server) *keep* the surviving `.part` file and report its path in the error — for a user-invoked trace, the file IS the product, and a partial trace with real events is more useful kept than silently discarded.

**An existing `.part` is refused, never cleared.** The CLI's first design cleared a stale `.part` at the start of a run so a prior kill could not block a re-run. That is self-defeating: the stale `.part` holds exactly the salvaged events this whole mechanism exists to produce, and the moment a user re-runs the command that died, they are deleted. It is also unsafe under concurrency — POSIX `unlink` succeeds on a file another process still holds open, so a second `grackle trace -o` writing the same path leaves the first one filling an unlinked inode and then renaming the *second* run's in-progress file into place, reporting success over another run's data. `JsonlPartWriter`'s exclusive-create `"xb"` is therefore left as the sole arbiter (no TOCTOU window), and a pre-existing `.part` becomes an actionable error telling the user to move it aside or choose another path.

**Write failures never reach the traced program.** Both CLI sinks swallow a failed `write()` and report it afterwards from `writer.broken`. A sink that lets the error escape hands it to `sys.monitoring`, which propagates it into the *traced program* at an arbitrary instruction boundary: the program aborts mid-run, and any `except`/`finally` live in its call stack executes — real rollbacks, retries and alerts firing because grackle's disk filled up. A tracer must not change the semantics of what it observes, and the "the run stops early" this would buy is not a property the design can enforce anyway, since the traced program decides whether it stops. `writer.broken` is also the *only* surviving signal when `--max-events` fires after a write failure, because `Tracer.run` re-raises `TraceCapExceeded` before it reaches its own `_sink_exc` check.

**Scope of the CLI reuse.** Only adapters whose `trace_streaming()` emits the same event stream `trace()` would (`RuntimeAdapter.streaming_trace_parity`, `adapters/base.py`) get the incremental `-o` path — Python today. Node's `trace()` (a CPU sampling profiler) and `trace_streaming()` (precise-coverage polling) are different instruments, not two deliveries of one stream, so substituting one for the other would silently change what gets recorded; Go and Rust have no streaming trace at all. Those three languages keep the original collect-then-`write_jsonl`-once behavior for `-o`. The `--stream` tee path (which already only exists for streaming-capable adapters) is unaffected by this gating and now writes-then-sends through the same `JsonlPartWriter`, strengthening the tee's existing "file count ≥ sent count" losslessness invariant to hold at every instant, including a mid-stream kill — not only at the end of a completed run.

## Consequences

**Positive:**
- Sessions survive server restarts and can be compared across runs (prerequisite for 8.4 differential analysis).
- Zero new runtime dependencies — `sqlite3` is stdlib.
- `session_load_request` reuses the existing seekable-session machinery end-to-end — loaded sessions get full seek **and** aggregate-query support, not a degraded subset.
- `serve --store --trace-source` populates the library out of the box, so the feature is usable end-to-end (not just an unwired flag).
- **(Phase 9.3)** Live `--stream` sessions are now auto-recorded and registered without any extra flag beyond `--store` — see the Phase 9.3 Amendment above.
- **(Phase 12.0)** `grackle trace -o`/`--stream --output` no longer buffer the whole run in memory — a killed tracing process (not just a killed *traced script*, which was already safe) keeps every event written so far in `FILE.jsonl.part`, and memory stays flat regardless of run length — see the Phase 12.0 Amendment above.

**Negative / known limits:**
- The store stores `source_path` as whatever string the caller provides.  On Windows this may be an absolute path with drive letter; the cross-platform implications are noted but not guarded — the load path uses `Path(meta.source_path)` which handles both.
- No retention policy or size cap on the database.  At ~200 bytes per session record (metadata only, not blobs), 1M sessions = ~200 MB — acceptable for local-first use.  The same applies to the new `recordings/` directory of JSONL files — no retention/cleanup policy beyond the startup orphan-`.part` sweep.
