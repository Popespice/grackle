# ADR-0013 — Runtime trace event schema and sys.monitoring adapter

**Status:** accepted

## Context

Phase 6 implements the "live" half of "local-first live code visualizer for
Python." The static foundation (Phases 1–5) is complete. This phase lands
`PythonRuntimeAdapter` — a tracer that instruments a Python script and
produces `TraceEvent` records that the future WebSocket transport (Phase 6.2)
will push to the browser to overlay on the static graph.

Three design questions needed to be settled before implementing:

1. **Which tracing API?** Python ≥ 3.12 ships PEP 669 (`sys.monitoring`);
   older versions have `sys.settrace` and `sys.setprofile`.
2. **Where is the output staged?** JSONL file on disk vs live streaming vs an
   in-process list.
3. **How are runtime frames mapped to static-graph node IDs?**

## Decisions

### 1. Use `sys.monitoring` (PEP 669, Python 3.12+)

`sys.monitoring` is chosen over `sys.settrace` / `sys.setprofile` for three
reasons:

- **Performance.** PEP 669 advertises ~20× lower overhead than `sys.settrace`
  because monitoring is implemented in the quickening layer of CPython (PEP
  659) and callbacks are only invoked for code objects that have registered
  interest. `grackle`'s project-file filter (`is_project_file`) — which
  returns `sys.monitoring.DISABLE` from `PY_START` callbacks for non-project
  code objects — means stdlib and site-packages functions are probed at most
  once and then permanently silenced.
- **Granularity.** `sys.monitoring` provides distinct event types
  (`PY_START`, `PY_RETURN`, `RAISE`, `LINE`) rather than a unified per-line
  callback, making it easier to subscribe only to the events we need.
- **Future path.** The CPython devs have stated that `sys.monitoring` is the
  intended long-term API; `sys.settrace` may be deprecated in a future release.

`packages/agent/pyproject.toml` already pins `requires-python = ">=3.12"` so
no version guard is needed at runtime.

**`sys.monitoring.DISABLE` scope.** Returning `DISABLE` from a callback is
only valid for `PY_START` (and `PY_RESUME`). Returning it from `PY_RETURN`,
`RAISE`, or `LINE` callbacks causes Python to remove the callback entirely and
log a `ValueError`. The `Tracer` implementation returns `DISABLE` only from
`_on_call` and uses an early `return` (returning `None`) for
non-project files in all other callbacks.

**Tool ID.** Tool IDs 0 (`DEBUGGER_ID`), 1 (`COVERAGE_ID`), and 2
(`PROFILER_ID`) are reserved by CPython convention. grackle uses tool ID 3,
the first freely usable slot.

### 2. JSONL file on disk as the primary output of Phase 6.1

The tracer writes to a JSONL file rather than streaming over the WebSocket.
Rationale:

- **Testability.** File output can be asserted in unit tests without spinning
  up an async server.
- **Decoupling.** The tracer is synchronous (the script runs to completion
  before any event processing). Forcing it into an async pipeline would require
  a thread-safe queue and async/await boilerplate that provides no value in
  Phase 6.1.
- **Replay.** Phase 6.2 adds `grackle serve --trace-source trace.jsonl` which
  replays the file over the WebSocket. File-first means 6.2 gets a real
  artefact to work with from day one.

Writes are atomic: the tracer collects events in memory, then `writer.py`
writes the full list to a `.tmp` sibling and calls `Path.replace()` — the same
atomic-write pattern used by `grackle.cache`.

### 3. Node-ID resolution: `(co_filename, co_firstlineno)` exact match

`sys.monitoring` callbacks receive a `CodeType` object. The two relevant
fields are:

- `code.co_filename`: the absolute path of the source file (normalised to POSIX
  via `to_posix()` relative to the project root).
- `code.co_firstlineno`: the first line of the enclosing function definition.

The static graph produced by the Python parser stores `line` as the definition
line of each `function` / `method` node. An **exact match** on
`(posix_path, co_firstlineno)` is therefore correct and O(1) via a precomputed
dict index (`NodeResolver._sym_index`).

Fallback chain (first match wins):
1. Exact `(posix_path, lineno)` → function/method node ID.
2. Exact `posix_path` → file node ID (covers module-level code, class bodies,
   lambdas, list comprehensions).
3. `"<unresolved>"` — returned for frames outside the project root (stdlib,
   site-packages, `<frozen ...>` sentinels). The `PY_START` callback returns
   `sys.monitoring.DISABLE` for these so they are never probed again.

Node IDs tagged `"<unresolved>"` are not written to the output; the caller
(Tracer) appends them as-is. Phase 6.2 may add a filter to drop them.

## Consequences

**Known limitations:**

- **Module-level code** (`co_firstlineno == 1` for the script's `__main__`
  frame) has no function node and falls back to the file node ID.
- **Lambda / comprehension frames** similarly fall back to the enclosing file
  node because the static parser does not emit nodes for them.
- **`sys.monitoring.DISABLE` is not available for `RAISE` events**, so
  exception events for non-project code still pass through the callback; the
  `is_project_file` check skips them without appending.
- **Overhead on large projects**: for a script that calls thousands of unique
  project functions per second, the per-call resolver lookup and list append
  add ~1–5 µs per event. PEP 669's DISABLE optimisation eliminates the vast
  majority of overhead (all stdlib/site-packages frames).
- **Thread safety**: the `_depth` dict is keyed by `threading.get_ident()` and
  is updated in callbacks called from potentially multiple threads. Python's
  GIL ensures no torn reads/writes on dict operations in CPython 3.12. No
  additional lock is used.

**Cross-refs:** ADR-0003 (RuntimeAdapter Protocol), ADR-0006 (Python ast vs
Tree-sitter — tracer uses the Python static parser for ID resolution),
ADR-0009 (Tree-sitter chassis — not used by the tracer; the runtime path is
orthogonal).
