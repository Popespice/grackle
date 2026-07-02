# ADR-0025 — Value Capture: Sampled Args/Returns on the Trace Event Wire

**Status:** Accepted (implemented in Phase 10.2, 2026-07-01)
**Date:** 2026-07-01
**Phase:** 10.2

---

## Context

Phase 10's headline is turning grackle from a *frequency* visualizer into a *causal,
time-travelable* one (see the north-star vision and `~/.claude/plans/plan-out-phase-10-mighty-lovelace.md`).
Chunk 10.1 shipped the pure foundation for this: `python_runtime/value_repr.py`, a
security-hardened value→string formatter (never calls a live user `__repr__`, never
consumes a lazy iterator, redacts sensitive names before any repr machinery runs,
bounded by size/depth/item caps) — with **zero wiring** into the tracer, wire schema,
or CLI (PR #49, main `9189497`).

10.2 is the chunk that wires it in: capture sampled argument and return values in the
Python `sys.monitoring` tracer (ADR-0013) and put them on the wire, so 10.3's
time-travel inspector has something to scrub.

This is deliberately the **first wire-schema change since Phase 8.3** — every phase
from 6 through 9 (runtime overlay, streaming, aggregation, native adapters) shipped
with `check-parity` a no-op. Ending that streak was a decision made at Phase 10
planning time (locked 2026-06-30): captured values ride as a **first-class typed
`values` field on `TraceEvent`**, not stuffed into the existing open `metadata` bag.
`metadata`'s schema description had drifted to describe an `args_repr?`/`retval_repr?`
scheme that was never implemented — this ADR retires that stale text.

Capture must be conservative on every axis that matters for a debugger that reads
values out of a user's running program: **who can turn it on** (opt-in), **how much**
(sampled, bounded), and **what happens to the output** (persists to disk, so it's a
privacy surface, not just a wire concern).

## Decision

### 1. Mechanism — returns are free, args need a verified frame

`PY_RETURN`'s callback signature already includes the return value
(`_on_return(code, offset, retval)`), so **capturing a return value costs nothing
extra to read** — no frame introspection needed.

`PY_START`'s callback (`_on_call(code, offset)`) does not receive argument values —
only the code object and bytecode offset. To read the just-bound parameters, the
tracer calls `sys._getframe(1)` **directly inside `_on_call`** (not through a nested
helper — every additional call frame shifts the index by one) and verifies
`frame.f_code is code` before trusting `frame.f_locals`. `PY_START` fires after
CPython has already bound parameters (positional, keyword, defaults, `*args`,
`**kwargs`) into the new frame's fast locals but before its first bytecode
instruction executes; the new frame is the "current" frame at that point, so a
Python-level call from within it (dispatching to our callback) chains `f_back`
through the C-level dispatch transparently, landing `sys._getframe(1)` on exactly
that frame.

The identity check exists because this is a fragile, version-sensitive technique:
dispatch-shape differences across Python 3.12/3.13/3.14, or **a resumed
generator/coroutine frame** (whose `f_locals` at a later `PY_START`-adjacent point no
longer reflect the original entry args), could otherwise attribute the wrong frame's
locals to the wrong event. **On a mismatch, the tracer degrades to no-args capture —
it never degrades the event itself.** The `call` event is always emitted with its
core fields (`event`/`node_id`/`ts_ns`/`thread_id`/`frame_depth`); only the optional
`values.args` payload is skipped.

Only **declared parameters** are read — `code.co_varnames[: co_argcount +
co_kwonlyargcount]` (positional-only params are a prefix of `co_argcount`, so no
separate handling is needed), plus the `*args`/`**kwargs` names when
`CO_VARARGS`/`CO_VARKEYWORDS` is set on `code.co_flags`. Ordinary function-body
locals are never read. Synthetic dot-prefixed names (CPython's implicit `.0`
iterator parameter on a generator expression's frame — list/dict/set comprehensions
no longer create a separate frame at all since PEP 709's inlining, landed in 3.12)
are filtered out, so such frames capture nothing rather than a raw iterator repr
under a meaningless `.0` label. `safe_repr`'s own never-consume guard (10.1) means
even an unfiltered lazy iterator would render as a placeholder without being
advanced — the filter is a correctness/clarity improvement on top of that, not the
only line of defense.

### 2. Safe-repr contract (10.1, referenced not re-litigated)

All formatting goes through `value_repr.format_arg`/`safe_repr` (chunk 10.1):
exact-type dispatch (kills name-spoofing), no live user `__repr__` invocation, no
lazy-iterator consumption, redaction-before-read for sensitive names, a bounded
total-character budget, and graceful degradation (`<unreprable: ClassName>`) on any
internal failure rather than propagating into the tracer's hot path. 10.2 does not
change any of this — it only calls the module's existing public API
(`format_arg`, `safe_repr`) from two new call sites.

### 3. Wire decision — first-class typed `values` field

`TraceEvent` (`packages/shared-types/schema/trace.schema.json`) gains an optional
`values` object (`{args?: ArgValue[], ret?: string, ret_truncated?: boolean}`) and a
sibling `ArgValue` def (`{name, repr, redacted?, truncated?}`), **not** added to
`required`. Three places must stay in sync on every future field change to this
shape:

1. The JSON Schema (source of truth, feeds codegen).
2. `packages/shared-types/src/messages.ts` — the hand-written canonical TS
   `TraceEvent`, which is the **public API** and is explicitly **not**
   parity-guarded (`verify-parity.mjs` diffs generated-artifact byte parity and
   message-`type` discriminator consts, not this hand-written shape).
3. `packages/agent/src/grackle/adapters/base.py`'s `TraceEvent` TypedDict — the
   Python analogue, also review-guarded rather than parity-guarded.

`pnpm codegen` regenerates the gitignored `src/generated/trace.ts` /
`_generated/trace.py` sanity-check artifacts from the schema; `pnpm check-parity`
diffs a fresh regen against the committed generated files, so it does catch a
forgotten `pnpm codegen` after a schema edit (previously a no-op since Phase
8.3, since there was nothing in `trace.schema.json` for it to regenerate
differently). **This does not extend to the three-way hand-sync above.**
`check-parity` diffs generated-artifact byte parity and message-`type`
discriminator consts only — it has no code path that reads `messages.ts`'s
`ArgValue`/`TraceValues`/`TraceEvent.values` shapes or `base.py`'s TypedDicts
and compares them against `trace.schema.json`. A field renamed or removed from
one of the three hand-synced places without updating the other two would ship
with `check-parity` and CI both green; keeping items 1–3 above in agreement is
enforced by human review alone, the same posture `graph.schema.json`/
`GraphNode` has always had (`adapters/base.py`'s existing comment: "Parity ...
is enforced by review during schema changes"). `KNOWN_MESSAGE_TYPES` (17
entries) is unchanged — `values` is a field on the existing `trace_event`
message, not a new message type.

**Default runs stay byte-identical.** `new_trace_event`'s trailing `values` parameter
defaults to `None` and is only added to the returned dict when non-`None` — unlike
`metadata` (always defaulted to `{}`), an absent `values` key must actually be
*absent*, not present-and-empty, so a trace captured without `--capture-values`
produces the exact same JSONL as before this chunk.

### 4. Consent + sampling posture

- **Opt-in, default OFF** (`--capture-values`, `TraceOptions.capture_values = False`)
  — the consent posture. No frontend toggle is needed for this chunk; 10.3 decides
  whether/how to expose one.
- **Python-only.** Node (sampling/coverage), Go, and Rust (coverage) adapters have no
  zero-touch channel that surfaces live values the way `sys.monitoring`'s callbacks
  do. `grackle trace --capture-values` on a non-Python script raises a clean
  `click.UsageError` naming the unsupported language, rather than silently ignoring
  the flag.
- **Sampled per-node budget** (`--capture-first-n`, default 100): once a `node_id`
  has captured 100 events' worth of values, further calls/returns for that node stop
  attaching `values` — but **the call/return event itself is always emitted**. The
  budget gates capture, never emission, so heat-map/coverage/flame-graph data stays
  complete regardless of capture settings; only the phase-10 value payload thins out
  on hot functions.
- **Size caps** (`--max-value-len`/`--max-value-items`/`--max-value-depth`, mirroring
  `ValueCaptureLimits` defaults 120/10/3) bound the cost of formatting each value.
- **`--no-redact`** is an explicit escape hatch for local debugging; redaction is
  name-based and on by default.

### 5. Data-at-rest / privacy surface

Captured values — **even redacted ones** — flow through the same pipeline as every
other trace event field: they persist to `-o`/`--output` JSONL files, to
`--stream --store` recordings (`recording_sink.py`), and to the SQLite session
library. This is a real change to what's stored on disk, not merely a wire-protocol
addition. Name-based redaction (`SENSITIVE_NAME_PARTS` in `value_repr.py`) catches
parameters/fields *named* like a credential; it does **not** scan value *content* —
a token held in a string argument named `s` or `data` is not redacted. Content/entropy
scanning is explicitly out of scope for this chunk. Anyone consuming a `-o` capture
or a `--store` session library file should treat it as containing plaintext
application data whenever `--capture-values` was used.

### 6. Future work

- Node/V8 Inspector value capture (would need CDP's `Runtime.getProperties`/pause
  semantics — a materially different mechanism from `sys.monitoring`'s free
  callback args).
- Per-line local-variable capture (Phase 11 candidate — this chunk captures only at
  function call/return boundaries).
- A server-side `trace_ancestors_at` causal-path query (ADR-0026, chunk 10.5) that
  will consume these values to answer "why did this fire, with what inputs."
- A frontend capture-consent toggle (10.3 decides whether one is needed beyond the
  CLI's default-off posture).

## Alternatives rejected

- **Untyped `metadata` bag** (stuff `values` into the existing open `metadata`
  object): keeps `check-parity` a no-op forever, but loses the structural guarantee
  that `args`/`ret` are typed consistently across the TS/Python canonical types —
  and the stale `metadata` schema description already shows what happens when an
  ad-hoc shape isn't kept honest. Rejected in favor of a first-class field, ending
  the no-wire-change streak deliberately rather than accidentally.
- **`sys.settrace`/`sys.setprofile` for arg capture instead of the verified-frame
  technique on `sys.monitoring`**: would mean running two tracing mechanisms side by
  side (ADR-0013 already committed to `sys.monitoring` for ~20× lower overhead) —
  rejected as needless complexity and overhead regression.
- **Always capturing values (no opt-in flag)**: rejected outright on the consent
  posture — a debugger that silently persists a running program's argument values
  to disk by default is a privacy footgun, not a feature.
- **Per-call budget instead of per-`node_id` budget**: a global cap would starve
  low-frequency functions in favor of whichever function happens to run first;
  per-`node_id` ensures every distinct call site gets its own sampling window.

## Constraints honored

- POSIX path discipline (ADR-0001) — untouched by this chunk; no new path-bearing
  fields.
- Open strings, not enums, on extension surfaces (ADR-0004) — `values`/`ArgValue`
  are typed structured fields, not an open-string surface, and don't change that
  posture elsewhere.
- `RuntimeAdapter` is a `@runtime_checkable` Protocol, not an ABC (ADR-0003) —
  unchanged; `TraceOptions` gains fields, no Protocol shape change.
- `mypy --strict` on all new Python code.
- Bind only to `127.0.0.1` — N/A (no networking changes).
- Cross-platform: CI matrix now fans across Python 3.12 and 3.13 on every OS
  (Ubuntu + Windows for PR gate; + macOS on push-to-main) specifically to stress
  the frame-capture technique's version sensitivity before merge.

## Known limitations

- The verified-frame technique is inherently best-effort across Python versions;
  the identity check is the safety net, not a guarantee that args are captured in
  every case. Returns are unaffected by this — they are always captured when
  `--capture-values` and budget allow, with no frame dependency.
- Name-based redaction misses secrets held in innocuously-named fields/parameters.
- No structured value columns in the SQLite session store — values ride in the
  JSONL payload only; querying by captured value content is out of scope.
- Frontend consumption of `values` (the time-travel inspector) is 10.3's job; this
  chunk only adds the field and the capture path.
