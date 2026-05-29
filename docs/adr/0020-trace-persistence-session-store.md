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

Auto-saving a *live* `--stream` producer (rather than a replay file) is still future work: it needs the server to record the producer's event stream to its own JSONL file on `trace_session_end`.  The schema and store fully support it; only the recording sink is unimplemented.

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

## Consequences

**Positive:**
- Sessions survive server restarts and can be compared across runs (prerequisite for 8.4 differential analysis).
- Zero new runtime dependencies — `sqlite3` is stdlib.
- `session_load_request` reuses the existing seekable-session machinery end-to-end — loaded sessions get full seek **and** aggregate-query support, not a degraded subset.
- `serve --store --trace-source` populates the library out of the box, so the feature is usable end-to-end (not just an unwired flag).

**Negative / known limits:**
- Auto-saving a *live* `--stream` producer to the store is not yet wired (only replay/trace-source files are indexed).  The store and schema support it; only the server-side recording sink is unimplemented.  Until then, capture a live run with `--stream --output FILE` and serve that file with `--store --trace-source FILE`.
- The store stores `source_path` as whatever string the caller provides.  On Windows this may be an absolute path with drive letter; the cross-platform implications are noted but not guarded — the load path uses `Path(meta.source_path)` which handles both.
- No retention policy or size cap on the database.  At ~200 bytes per session record (metadata only, not blobs), 1M sessions = ~200 MB — acceptable for local-first use.
