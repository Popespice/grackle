# ADR-0027 — Watch Mode: Live-Growing Graph

**Status:** Accepted (implemented in Phase 10.6, 2026-07-08)
**Date:** 2026-07-08
**Phase:** 10.6

---

## Context

grackle's north star is a view that grows live as a system is built — "point grackle at a
filesystem and watch the graph grow as files are added and hooked up" (see the north-star
vision and `~/.claude/plans/plan-out-phase-10-mighty-lovelace.md`). Chunks 10.1–10.5 shipped
the "why they fire" and "why they connect" halves of Phase 10 (value capture, time-travel
inspector, edge evidence, causal path). This ADR governs the remaining, deliberately-tailed
piece: making the *static graph itself* grow live.

Today the graph is effectively one-shot per browser tab. `_push_static_graph` re-parses the
project and pushes a `static_graph` message **only when a client connects**
(`server.py`) — nothing re-pushes to an *already-connected* client when a source file changes,
so the graph in the browser goes stale the moment you edit code until you reload the page.

## Decision

### 1. Full `static_graph` re-push is the MVP — no new wire message type

A watch-triggered rebuild reuses the exact `static_graph` message every connect already
receives (`protocol.make_static_graph`), broadcast to every connection via the existing
`broadcast()` fan-out (`python_runtime/live_buffer.py`, ADR-0016's transport) over the same
`connections: set[ServerConnection]` registry the server already maintains. **No new message
type, no schema change — `pnpm check-parity` stays a no-op.** A `graph_delta` incremental
protocol (send only what changed, not the whole graph) is future work (§Future work); the
frontend already replaces the whole graph wholesale on any `static_graph` receipt (`setGraph`),
so a full re-push needs zero frontend change to *reach* the browser. The resulting layout
scramble on re-push is intentionally left to chunk 10.7 (graph-diff animation) — 10.6's job is
only to get a correct, timely re-push to the browser.

### 2. Two watcher backends: stdlib polling default, optional `watchfiles`, never required

`grackle/watcher.py` implements two async-generator backends sharing one pure snapshot/diff/
hash-gate core:

- **`_watch_poll`** (always available) — a deadline-scheduled `asyncio.sleep` loop, cadence
  mirroring `node_runtime/launcher.py`'s coverage-poll loop (each wake scheduled off the
  *previous target*, not off "now", so a slow scan doesn't stack back-to-back zero-wait ticks).
  Re-stats the whole parseable tree every `--watch-interval` seconds (default 0.3s).
- **`_watch_watchfiles`** — event-driven, via the *optional* `watchfiles` package
  (`pip install grackle[watch]`). `watch_changes()` picks it automatically when importable
  (`--watch-poll` forces the stdlib path regardless, for determinism/testing/network-drive
  edge cases).

`watchfiles` is added to `[project.optional-dependencies]`, **never** to the base
`dependencies` list — preserving the project's no-required-new-Python-deps invariant and
de-risking wheel availability on the Windows CI leg for anyone who `pip install`s the
distributed package. It is additionally present in the `dev` dependency-group so CI
type-checks and exercises the optional path; a `[[tool.mypy.overrides]]` entry
(`ignore_missing_imports`) is defense-in-depth for anyone running `mypy --strict` without the
dev group synced. **This means a contributor following the documented bootstrap (`uv sync`,
no extras) *does* get `watchfiles` installed** — deliberately, so the dev/CI environment
exercises the richer backend by default — whereas an end user (`pip install grackle` /
`grackle[watch]`) does not. "Zero extra installs" in `docs/cross-platform.md` refers to that
end-user/distributed-package sense, not the contributor dev-loop; a contributor who wants to
exercise the stdlib fallback locally passes `--watch-poll`.

### 3. Hash-gate before rebuild — content hash, not the FS event, is the source of truth

Both backends only ever yield a batch of changed paths after confirming the file's **content**
changed: SHA-256 of bytes, matching `cache.py`'s own `_hash_file` hashing exactly (bytes, never
decoded text — the same gate that makes the on-disk parse cache immune to CRLF/LF checkout
differences). A cheap `(mtime_ns, size)` pre-filter avoids re-hashing unchanged files every
tick; the hash is authoritative whenever that pre-filter trips. This is what makes an
atomic-save (write-temp-then-rename — CREATE+RENAME, often no byte change) or a bare `touch`
produce **zero** re-parses and **zero** re-broadcasts — the single biggest source of spurious
rebuild storms a naive mtime-or-FS-event watcher would otherwise generate on every editor save.

### 4. Extension + directory filtering — the load-bearing self-trigger guard

A rebuild's own cache writes (`.grackle/cache/{manifest.json,<hash>.json,*.tmp}`) are, from the
filesystem's point of view, changes under the watched root. Watching them unfiltered would
**self-trigger an infinite rebuild loop**: rebuild → cache write → watcher fires → rebuild → ….
Two independent guards, either sufficient alone, both kept:

1. **Extension allow-list** (`_PARSEABLE_EXTS`) — `.py .ts .tsx .mts .cts .go .rs`, mirroring
   each language walker's own hardcoded discovery. Cache sidecars are `.json`/`.tmp` — never
   matched.
2. **Directory deny-list** (`_EXCLUDED_DIRS`) — `.grackle` (own cache), `.git`, `node_modules`,
   `target`, `__pycache__`, `.venv`, `dist`, `build`. Pruned *during* the walk
   (`Path.walk()`'s in-place `dirnames` mutation, stdlib 3.12+) rather than filtered after a
   full traversal, so a large `node_modules`/`target` doesn't cost a full descent every tick.

The extension list is **hardcoded**, not sourced from a new `registry.static_extensions()`
accessor: `StaticParserAdapter` does not expose an `.extensions` attribute today (only
`RuntimeAdapter` does), so a registry accessor would require touching the Protocol and all four
adapters — churn with no payoff for a fixed, seven-element set. If a fifth static-parser
language is added later and this list is not updated, its files are silently excluded from
watch triggering (the graph still parses them fine at connect time) — an accepted, documented
edge case for the MVP.

**The directory deny-list must be matched against the path *relative to `--root`*, never the
raw absolute path — this was a real bug caught in review, not a hypothetical.** `watchfiles`
reports absolute changed-file paths; an early version of `_watch_watchfiles`'s filter checked
every component of that absolute path against `_EXCLUDED_DIRS`. If the *served project itself*
happened to sit under an ancestor directory literally named one of the excluded names (e.g. a
checkout under `~/build/<repo>`, `/opt/dist/<app>`, or any CI workspace convention using
`build`/`target`/`.venv` as a path segment above the project — all plausible, common layouts),
every file's absolute path contained the excluded segment, so the filter rejected everything —
`--watch` would silently detect zero changes for the server's entire lifetime, with no error or
warning, while `--watch-poll` (whose `Path.walk()`-based pruning only ever inspects directories
*below* root, structurally incapable of seeing root's own ancestors) worked fine on the
identical project. Fixed: `_watch_filter` now computes the path *relative to `root`*
(`to_posix(p, root)`) before checking components, matching `_watch_poll`'s semantics exactly.
Regression-tested (`tests/test_watcher.py::test_watch_watchfiles_root_under_excluded_ancestor_name`,
mutation-verified against the original bug).

**`_EXCLUDED_DIRS` is a watcher-only concept — the static parsers themselves have no directory
denylist.** `python_parser/walker.py` and the tree-sitter walkers use a bare `rglob`, excluding
only what `ParseOptions.exclude_patterns` names (empty by default; `serve` has no `--exclude`
flag). This means a project with real, hand-written source living under a directory that
happens to share a name with `_EXCLUDED_DIRS` (most plausibly an in-tree `.venv`, or source
nested under a directory literally named `build`/`dist`) is fully represented in the *static
graph* pushed at connect time, but edits to it will never trigger a watch rebuild — the graph
silently goes stale for that subtree specifically, with no error. See §Known limitations; not
fixed in this chunk (the guard's purpose — preventing the watcher from observing its own cache
writes — is satisfied either way, and a project intentionally naming a source directory
`node_modules` or `.venv` is vanishingly rare).

### 5. The watch-triggered rebuild runs on a dedicated executor; the connect-time parse stays inline

**This decision was revised during review** (see §Alternatives rejected for the original
inline-everywhere design and why it didn't survive). The connect-time parse
(`_push_static_graph`) still runs synchronously, inline, on the event loop — a naturally rarer,
bounded event (one parse per new browser tab). But `_watch_loop`'s rebuild, which recurs
continuously and unpredictably for the life of a `--watch` session, runs on a **dedicated
single-worker `concurrent.futures.ThreadPoolExecutor`** owned by `serve()`
(`watch_executor`, created only when `watch=True`).

Adversarial review empirically demonstrated the original inline design's cost with a
monkeypatched slow `_build_static_graph` standing in for a large/slow real parse:

- A shutdown request issued while an inline rebuild was in flight was delayed by the rebuild's
  **full duration** (measured: a request issued at t=0.147s did not complete until t=3.104s for
  a 3s synthetic rebuild) — `watch_task.cancel()` in `serve()`'s `finally` cannot be *delivered*
  until the synchronous call returns control to the event loop.
- The same inline call **starves every other connected client** for its duration — an
  independent 0.1s-interval heartbeat coroutine recorded a single tick over a 2s synthetic
  rebuild instead of ~20, meaning another tab's ping/pong keepalive, live `--stream` trace
  forwarding, or any other message handling stalls for as long as the rebuild takes. A rebuild
  approaching the `websockets` library's default 20s `ping_timeout` risks that library force
  -disconnecting healthy, unrelated clients for "missing" a pong the loop was never free to
  process.

Moving the rebuild to `loop.run_in_executor(executor, _build_static_graph, root, meta_cache)`
fixes the starvation case completely (the event loop is free to service other connections
while the executor thread works) and meaningfully improves — but does not fully close — the
shutdown-delay case: cancelling the `await run_in_executor(...)` call lets `_watch_loop`'s own
task complete its cancellation promptly (unblocking `serve()`'s `finally` and, in turn,
`store.close()`), but **the underlying OS thread cannot be forcibly interrupted** (Python has
no safe thread-preemption primitive) and keeps running in the background until the parse
naturally finishes. Empirically: `asyncio.run()`'s own shutdown sequence calls
`shutdown_default_executor()`, which *would* block waiting for an orphaned task on the loop's
shared default executor — this is why `watch_executor` is **our own, explicitly-owned**
executor (not `loop.run_in_executor(None, ...)`), shut down in `serve()`'s `finally` with
`wait=False, cancel_futures=True` so that call itself never blocks. Even so, CPython's
`concurrent.futures.thread` module registers an `atexit` hook that joins every
`ThreadPoolExecutor` worker thread at interpreter shutdown *regardless* of how the executor
was told to shut down — so a pathologically slow or stuck parse can still delay the Python
*process*'s final exit, even though every asyncio-level shutdown step (task cancellation,
store close, WS server close) completes promptly. This residual limitation is fundamental to
non-preemptible Python threads, not something this chunk's fix can fully close; see §Known
limitations.

`CacheManager` and `meta_cache` both tolerate the executor-based concurrency this introduces:
`CacheManager` is explicitly documented safe for multiple threads (and processes) sharing a
root (per-root `threading.Lock` + OS-level flock), and `meta_cache`'s only race is two threads
redundantly recomputing the same *deterministic* value for an identical topology signature —
never corruption, at worst duplicate work whose results agree.

### 6. Idle skip — no rebuild work when no client is connected

`_watch_loop` still advances its snapshot every tick (so drift never accumulates while idle),
but skips the parse + broadcast entirely when `connections` is empty — there is nothing to
broadcast to, and the next connect gets a fresh parse via `_push_static_graph` regardless.

### 7. Cross-platform posture

- **Byte hashing, not text** — CRLF/LF checkout differences never manufacture a false change
  (`docs/cross-platform.md`'s line-ending contract).
- **POSIX-normalized paths** — every changed path is reconstructed via the snapshot's POSIX key
  (`to_posix`), matching node-ID normalization exactly; a native path is only ever
  reconstructed as `root / posix_key` for the caller (`CacheManager.evict`), which itself
  normalizes internally.
- **Coarse Windows/FAT mtime — a real, undismissable gap, not just a delay.** The `(mtime,
  size)` pre-filter is a *fast path*, not the correctness gate; the byte hash is authoritative
  whenever the pre-filter can't rule a file out. But if a filesystem's mtime resolution is
  coarser than the edit cadence (FAT32/exFAT ~2s, older HFS+ ~1s, some network/virtualized
  mounts), a same-size content edit that lands within one coarse mtime bucket is **not
  detected at all** by `_watch_poll` — not merely delayed to "the next distinguishable tick", as
  an earlier draft of this ADR claimed. If no *later*, distinguishable edit ever touches that
  same file again, the live graph silently stays wrong for the rest of the session. This is an
  accepted limitation of a polling design on such filesystems, not a correctness bug in the
  traditional sense (no crash, no wrong node IDs) — but it is a real, permanent-until-a-later-
  edit gap, and `docs/cross-platform.md` is worded to match this precisely rather than overstate
  the guarantee. `_watch_watchfiles` does **not** share *this specific* mechanism (it has no
  mtime/size fast-path skip against a full-tree baseline) — **but it has its own, differently-
  shaped version of the same class of gap**, corrected below and in `_stat_and_hash`'s
  docstring: an earlier draft of this ADR claimed `_watch_watchfiles` "does not share this
  exposure" at all, which a second review round showed to be false.
- **Mid-write tolerance, and its backend asymmetry.** A `stat`/`read` that raises `OSError`
  falls back to the file's last known-good hash with its `(mtime, size)` poisoned to a sentinel
  (`_stat_and_hash`), so the fallback is trusted for at most one tick, never indefinitely. This
  contract assumes a *next opportunity* to re-verify exists. `_watch_poll` always provides one
  (a full-tree re-scan every tick, regardless of which files changed). `_watch_watchfiles` does
  **not**: a path is only re-examined when a *new* FS event names it again. A transient failure
  (a brief AV-scan/OneDrive/Dropbox lock, a permission race) that races a file's **one and only**
  reported event therefore leaves that file's tracked hash permanently stale for the rest of the
  session on the `watchfiles` backend — the event-driven mirror of `_watch_poll`'s coarse-mtime
  gap above, via a different mechanism, not engineered around in this chunk. grackle's own
  writers are atomic (temp-then-`Path.replace`), so they never expose this race to the watcher
  themselves; third-party tools/AV/sync clients can.
- **No `.gitignore` respect** — matches the existing parser walkers, which also don't consult
  `.gitignore` (only `ParseOptions.exclude_patterns`). Out of scope here; noted as future work.

## Consequences

- **Whole-graph analysis (cross-language edges, Tarjan cycles, hub-score) recomputes on every
  rebuild**, not incrementally — acceptable because those stages are pure functions of the
  merged per-file partials and the per-file parse cache already makes re-parsing an unchanged
  file free. Measured on `fixtures/stress-2k` (209 Python files, 2814 nodes / 465 edges):
  warm-cache full rebuild after a single-file edit ≈ **55 ms** (49 ms parse + 6 ms
  `enrich_metadata`); the watcher's own idle per-tick cost (stat every parseable file, hash
  only files whose `(mtime, size)` moved) ≈ **10 ms** when nothing changed. These numbers were
  measured directly against the parse/enrich pipeline and the watcher's snapshot pass, not
  re-measured through the full `watch_executor` dispatch added during review (§5) — a
  `ThreadPoolExecutor` submit/await round-trip is sub-millisecond overhead, negligible against
  either number, but the figures above are not literally post-fix wall-clock measurements of
  the shipped call path. Both are comfortably inside the default 300 ms `--watch-interval` and
  inside the dedicated `watch_executor` thread; a project an order of magnitude larger (where
  the executor thread itself might take multiple seconds) is the natural ceiling to revisit for
  an incremental whole-graph-analysis pass.
- **Polling has an inherent latency floor** (`--watch-interval`, default 300 ms) that
  `watchfiles` avoids by reacting to OS filesystem-event APIs directly; the trade is the
  optional dependency. Both are exposed; `--watch-poll` lets a user force determinism (or work
  around a `watchfiles` install/wheel/network-drive issue) without losing `--watch` entirely.
- **Stale cache sidecars for files that later get evicted only on their next detected change**
  — `cache.evict()` is called for every changed path in a watch batch, reclaiming the manifest
  entry + sidecar immediately; a file deleted while the server isn't running (or before
  `--watch` was ever used) only gets cleaned up once the watcher observes *some* change to it.
  Not a correctness issue (an orphaned sidecar is inert), just a minor disk-hygiene note.
- **`meta_cache` grows unboundedly for the life of a long `--watch` session.** Before this
  chunk, `meta_cache` (agent-side hub-score/cycles memoization keyed by graph-topology
  signature) only grew when a new browser tab connected — bounded by how many tabs a human
  opens in one server run. `--watch` adds a new, continuously-recurring growth source: every
  structural edit (any change to node/edge counts or the edge-kind checksum) during a session
  adds a never-evicted entry. Each entry is individually small (capped at 50 hub-score rows +
  100 cycle rows), so this is a slow accumulation, not an acute leak, but a multi-hour/
  multi-day `--watch` session with continuous active editing — exactly the intended use case —
  grows this dict without bound, with no restart-free way to reclaim it. Not addressed in this
  chunk; a follow-up would need an LRU cap or a TTL, keyed by last-access rather than insertion.
- **A pathologically slow or stuck rebuild can still delay final process exit** even though
  every asyncio-level shutdown step is now prompt (§5) — CPython's `concurrent.futures.thread`
  atexit hook joins worker threads regardless of how the executor was told to shut down. A
  future fix would need `multiprocessing` (real OS-level termination) or a cooperatively
  cancellable parser; neither fits this chunk's scope. Not observed as a practical problem at
  the measured stress-2k scale (worst case ≈ 55ms).
- **10.7 (not this chunk) handles the layout scramble** — a watch-triggered `static_graph`
  rebuilds the frontend's graphology instance from scratch today (`setGraph`), so an edit
  currently re-lays-out the whole graph rather than growing it in place. Acceptable for this
  chunk; the fix is graph-diff animation, deliberately scoped out here.

## Future work

- **A `graph_delta` wire protocol** (send only added/removed/changed nodes and edges instead of
  the whole graph) — deferred; full re-push is the MVP and, at measured stress-2k scale, is not
  the bottleneck.
- **`.gitignore` respect** — the watcher (and the underlying parser walkers) currently only
  honor `ParseOptions.exclude_patterns`; consulting `.gitignore` would reduce noise on trees
  with large ignored build artifacts that happen to share a parseable extension.
- **`registry.static_extensions()`** — if a fifth static-parser language is added, revisit
  whether the hardcoded `_PARSEABLE_EXTS` should instead be sourced from the registry (would
  require adding an `.extensions` attribute to `StaticParserAdapter` and all adapters).
- **Reconciling the parser-exclusion / watcher-exclusion asymmetry** (§4) — either give `serve`
  an `--exclude` flag that both the parser and the watcher honor consistently, or accept and
  keep documenting the gap; not resolved in this chunk.
- **`meta_cache` eviction** (an LRU cap or access-time TTL) for very long `--watch` sessions
  with continuous structural edits — see §Consequences.
- **`multiprocessing`-based rebuild, or a cooperatively cancellable parser** — the only ways to
  fully close the residual "a stuck parse can delay final process exit" gap (§5); a
  significantly larger change than this chunk's scope.

## Alternatives rejected

- **Requiring `watchfiles`** (drop the stdlib poller): rejected — would add a required new
  Python dependency, with attendant wheel-availability risk on the Windows CI leg, for a
  feature (`--watch`) most sessions don't use. The optional-with-fallback shape has no such
  cost. (An earlier draft of this bullet claimed this would be "the first required dependency
  in the project's history" — false: `tree-sitter`, `tree-sitter-typescript`, and
  `tree-sitter-go` were added as required dependencies in Phase 4, and `tree-sitter-rust` in
  Phase 5; all remain in `pyproject.toml`'s unconditional `dependencies` list. The real,
  narrower rationale — avoiding a required dependency for one opt-in flag, on top of the
  existing tree-sitter footprint — still holds; the ADR should not have overstated it.)
- **A `registry.static_extensions()` accessor now**: rejected for this chunk — `extensions` is
  a `RuntimeAdapter`-only attribute today; adding it to the static-parser Protocol and all four
  adapters is real churn for a fixed seven-element set with no near-term second consumer.
  Recorded as future work instead.
- **Building the watch-triggered rebuild inline, matching the connect-time parse (the original
  §5 decision)**: rejected after review — see §5's mechanism section for the empirical
  measurements (shutdown delayed by the rebuild's full duration; every other connected client
  starved for the same duration) that led to revising this decision mid-chunk.
- **`loop.run_in_executor(None, ...)` (the loop's shared default executor) for the rebuild**:
  rejected in favor of a dedicated, explicitly-owned `ThreadPoolExecutor` — `asyncio.run()`'s
  shutdown sequence calls `shutdown_default_executor()`, which blocks waiting for the default
  executor's outstanding work, silently reintroducing the same shutdown-delay problem the
  executor move was meant to fix. An explicitly-owned executor, shut down with `wait=False` in
  `serve()`'s own `finally`, is not touched by that automatic teardown.
- **A no-mtime-prefilter, hash-every-tick design**: rejected as unnecessarily expensive on a
  quiet tree at scale — the `(mtime, size)` pre-filter is a pure fast path with the byte hash
  still authoritative whenever it can't rule a file out, so no correctness is traded away
  (except the coarse-mtime gap in §7/§Known limitations, which this alternative would have
  closed at a real, unmeasured performance cost on large trees — not adopted for the MVP).

## Constraints honored

- **Bind only to `127.0.0.1`** — unaffected; watch mode adds no networking surface.
- **POSIX path discipline (ADR-0001)** — every changed path routes through `to_posix`; native
  reconstruction for cache eviction is the sole exception, and even that hands a native `Path`
  to `CacheManager.evict`, which normalizes internally.
- **Open strings, not enums (ADR-0004)** — N/A; no new wire vocabulary.
- **No *required* new Python dependency** — `watchfiles` is `[project.optional-dependencies]`
  only; the base install is dependency-identical to pre-10.6.
- **`mypy --strict`**, Ubuntu + Windows CI green (`check-parity` a no-op — no schema touched).

## Known limitations

- **No `.gitignore` respect** (see Future work) — a large ignored directory sharing a
  parseable extension (rare, since build outputs are typically not `.py`/`.ts`/`.go`/`.rs`) is
  still walked and watched.
- **A fifth static-parser language's extensions must be added to `_PARSEABLE_EXTS` by hand** —
  silently excluded from watch triggering (not from parsing) until then.
- **Coarse-mtime same-size same-content-bucket edits on `_watch_poll` can be missed
  permanently, not just delayed** (see §7) — an accepted limitation of a polling design on
  filesystems with mtime resolution coarser than the edit cadence; there is no guarantee a
  "later distinguishable tick" ever arrives if the file is never touched again.
  `_watch_watchfiles` has its own differently-shaped version of this same class of gap — see
  the next item; a second review round showed an earlier draft's claim that it "does not share
  this exposure" was false.
- **`_watch_watchfiles` can also permanently miss a change**, via a different mechanism: a
  transient stat/read failure racing a path's one-and-only reported FS event leaves that
  file's hash frozen at its pre-edit value for the rest of the session, because this backend
  (unlike `_watch_poll`) never re-examines a path it wasn't just told changed. See `_stat_and_
  hash`'s docstring and §7.
- **The tree-sitter `Parser` singleton's thread-safety under the new watch_executor concurrency
  is unaudited** — `tree_sitter_runtime.get_parser()`'s cached, shared `Parser` instance can now
  be reached from two real OS threads for the first time (the connect-time inline parse and the
  `watch_executor` thread) for TypeScript/Go/Rust projects. Upstream tree-sitter does not
  document concurrent multi-threaded `.parse()` on one `Parser` as safe. No crash was reproduced
  in review (the pinned binding appears to serialize under the GIL for this call today), but
  that serialization is not a documented contract and could change in a future tree-sitter
  release. Not fixed in this chunk; would need either a per-thread `Parser` or an explicit lock
  around tree-sitter `.parse()` calls, both larger changes than this chunk's scope.
- **`CacheManager`'s blocking `threading.Lock` now sees real cross-thread contention** — the
  `watch_executor` thread's rebuild and the still-inline connect-time parse both go through
  `CacheManager` on the same root; if a new client connects while the watch thread holds that
  lock (per-file manifest get/set/evict, typically brief), the main event-loop thread blocks
  synchronously for that hold's duration — a smaller-scale instance of the same starvation class
  §5's fix was built to prevent, not itself created by that fix but newly exercisable at real
  concurrency because of it. Expected to be brief (single-file lock hold) in practice; not
  addressed further in this chunk.
- **Posix-key case-insensitive collapse is not guaranteed on every OS** — `to_posix` (see its
  corrected docstring) canonicalizes case on Windows but not on macOS/Linux, since POSIX
  `realpath` has no canonical-case concept to query. The `_watch_watchfiles` coalesced-batch
  dedup (`affected: dict[str, Path]`, §4) assumes distinct-case references to one physical file
  collapse to one key; on macOS this can produce a phantom duplicate snapshot entry (one spurious
  extra rebuild) and, for a genuine case-only rename, a stale lingering key that only clears on
  server restart. Bounded impact (no crash, no data loss, self-heals on restart); not fixed here
  — a real fix belongs in `to_posix` itself (project-wide, since node IDs/cache keys share it),
  well beyond this chunk's scope.
- **Other, pre-existing `server.py` request handlers still use `loop.run_in_executor(None, ...)`**
  (the loop's shared default executor) — `trace_seek_request`/`trace_query_request`/session
  list/load handlers in `_receive_loop`, unrelated to and unmodified by this chunk. §5's fix
  narrows this ADR's "shutdown never hangs" framing to the watch-triggered rebuild specifically;
  an in-flight request on one of these pre-existing handlers at shutdown time can still reproduce
  the same `shutdown_default_executor()`-blocks class of delay via a different, older code path.
  Out of scope for this chunk; noted so the ADR's framing isn't read as broader than it is.
- **`_EXCLUDED_DIRS` creates a parse/watch mismatch, not just a self-trigger guard** (see §4)
  — a project with real source under a directory named `.venv`/`build`/`dist`/etc. is fully
  parsed and shown in the static graph at connect time, but edits to it never trigger a watch
  rebuild, with no error or indicator. `serve` has no `--exclude` flag to work around this.
- **`meta_cache` has no eviction** — grows by one entry per distinct graph-topology signature
  for the life of a `--watch` session with continuous structural edits (see §Consequences).
- **A pathologically slow or stuck rebuild can delay final process exit**, even though every
  asyncio-level shutdown step is prompt — Python cannot forcibly interrupt a running thread
  (see §5, §Consequences). Not observed as a practical problem at measured stress-2k scale.
- **A watchfiles-reported coalesced delete+add batch for the same path is resolved by a single
  fresh `path.exists()` + stat/hash check, not by trusting either reported event** — correct,
  but introduces a narrow TOCTOU window (the file could be deleted between the `exists()` check
  and the subsequent stat/read) shared with every other filesystem-based check in this codebase;
  the fallback path (leave the prior entry as-is) bounds the resulting imperfection to at most
  one extra tick, never a crash or an infinite loop.
