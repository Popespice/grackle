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
  (`PY_START`, `PY_RETURN`, `PY_UNWIND`, `RAISE`, `LINE`) rather than a
  unified per-line callback, making it easier to subscribe only to the
  events we need.
- **Future path.** The CPython devs have stated that `sys.monitoring` is the
  intended long-term API; `sys.settrace` may be deprecated in a future release.

`packages/agent/pyproject.toml` already pins `requires-python = ">=3.12"` so
no version guard is needed at runtime.

**Subscribed events:**

| Event       | Purpose                                                      | Emits a TraceEvent? |
|-------------|--------------------------------------------------------------|---------------------|
| `PY_START`  | Project-frame entry (call)                                   | Yes — `"call"`      |
| `PY_RETURN` | Project-frame exit via normal return                         | Yes — `"return"`    |
| `PY_UNWIND` | Project-frame exit via exception propagation                 | No — depth bookkeeping only |
| `RAISE`     | Exception observed in a project frame                        | Yes — `"exception"` |
| `LINE`      | Per-line callback (opt-in via `TraceOptions.include_line_events`) | Yes — `"line"`  |

**Why PY_UNWIND?** Without it, the `_depth` counter would not be decremented
for frames that exit via exception (only `PY_RETURN` was firing for those).
Every later event on the same thread would then report a wrong (inflated)
`frame_depth`. `PY_UNWIND` runs the same `depth -= 1` step as `PY_RETURN`
but does not emit a separate event (the `RAISE` callback already recorded
the exception). Catching the exception inside a frame does not fire
`PY_UNWIND` — the frame is not unwound.

**Why not `PY_YIELD` / `PY_RESUME`?** Generator suspend/resume events are
intentionally **not** subscribed. Depth bookkeeping for suspended generator
frames is non-trivial (each `yield` exits the frame from the interpreter's
perspective; each iteration re-enters it) and the static graph has no
representation of "suspended" frames. The current code emits a single
`call` event when the generator is first invoked and a single `return`
event when it terminates. **Known limitation:** while a generator is
suspended, the depth of frames running outside it may differ from what a
naive eager-call stack would report. This has not been observed to cause
incorrect heat-map output and is documented for future revisit.

**`sys.monitoring.DISABLE` scope.** Returning `DISABLE` from a callback is
only valid for `PY_START` (and `PY_RESUME`). Returning it from `PY_RETURN`,
`PY_UNWIND`, `RAISE`, or `LINE` callbacks causes Python to remove the
callback entirely and log a `ValueError`. The `Tracer` implementation
returns `DISABLE` only from `_on_call` and uses an early `return` for
non-project files in all other callbacks.

**Tool ID.** Tool IDs 0 (`DEBUGGER_ID`), 1 (`COVERAGE_ID`), and 2
(`PROFILER_ID`) are reserved by CPython convention. grackle uses tool ID 3,
the first freely usable slot.

**Teardown order matters.** `_stop()` first clears the subscription bitmask
(`set_events(id, 0)`), then unregisters every callback (`register_callback(
id, event, None)`), then releases the tool ID (`free_tool_id(id)`). Calling
only `free_tool_id` leaves callbacks registered: they keep firing for the
rest of the process and crash during interpreter shutdown when
`sys.meta_path is None`. This caused a CI failure in the original
implementation and is regression-tested by
`tests/python_runtime/test_tracer.py::test_stop_*`.

**`BaseException`, not `Exception`.** `run()` wraps `runpy.run_path` in a
`try / except BaseException:` block (catching `Exception` would miss
`SystemExit` and `KeyboardInterrupt`). Without this, a script that calls
`sys.exit()` would bypass `return self._events` entirely; the test harness
would then see an unhandled `SystemExit` and the trace would be lost.

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
writes the full list to a tmp sibling and calls `Path.replace()` — the same
atomic-write pattern used by `grackle.cache`. The tmp filename is built by
appending `".tmp"` to the destination's full name (`foo.jsonl` →
`foo.jsonl.tmp`), **not** by `Path.with_suffix(".tmp")`. With
`with_suffix`, `foo.tar.gz` would collapse to `foo.tar.tmp` and collide
with any unrelated `foo.tar.tmp`. The append form is collision-free.

**Memory.** The event list is held fully in memory until the script
completes. For long-running scripts this can be unbounded; the
`TraceOptions.max_events` cap exists as a hard upper bound. `grackle trace`
exposes this as `--max-events`, validated to be `>= 1` by the CLI.
Streaming-as-we-go is deferred to a future phase: it would require a
thread-safe queue and is not justified by current use cases (the tiny
fixture emits 49 events; even large projects fit comfortably in memory at
PEP 669 overhead).

### 3. Node-ID resolution: `(co_filename, co_firstlineno)` exact match

`sys.monitoring` callbacks receive a `CodeType` object. The three relevant
fields are:

- `code.co_filename`: the absolute path of the source file (normalised to POSIX
  via `to_posix()` relative to the project root).
- `code.co_firstlineno`: the first line of the enclosing function definition
  — or the first decorator's line, if the function is decorated.
- `code.co_name`: the function's name, or `"<module>"` for module-level code.

The static graph produced by the Python parser stores `line` as the
definition line of each `function` / `method` / `class` node. **Both ends
agree on the decorator rule:** `python_parser.visitors` writes
`decorator_list[0].lineno` (not the `def` line) when decorators are
present, so the runtime exact-match succeeds for decorated functions.
Without this agreement, every decorated function would fall back to the
file-node ID — a silent correctness bug.

An **exact match** on `(posix_path, co_firstlineno)` is therefore correct
and O(1) via a precomputed dict index (`NodeResolver._sym_index`).

**Module-frame special case.** Module-level code has `co_firstlineno = 1`,
which collides with any function defined on line 1. The resolver detects
this by checking `co_name == "<module>"` and going straight to the
file-node index — bypassing the function/method lookup that would
misresolve the module event to that function.

Fallback chain (first match wins):
1. **If `co_name == "<module>"`:** exact `posix_path` → file node ID.
2. Exact `(posix_path, co_firstlineno)` → function/method node ID.
3. Exact `posix_path` → file node ID (covers lambdas, comprehensions, class
   bodies).
4. `"<unresolved>"` — returned for frames outside the project root (stdlib,
   site-packages, `<frozen ...>` sentinels). The `PY_START` callback returns
   `sys.monitoring.DISABLE` for these so they are never probed again.

**Normalization caching.** Every callback used to call `is_project_file`
then `resolve` — each of which independently ran `Path.resolve()` and
`Path.relative_to()`. The resolver now caches the result of
`_normalize_filename(co_filename)` in a per-instance dict keyed by the raw
filename. The cache is bounded by the number of distinct code-object
filenames touched during the session (a small constant — one per project
file plus a handful of stdlib sentinels before `DISABLE` silences them).

Node IDs equal to `"<unresolved>"` may appear in the output if a callback
fires for a project file whose static-graph entry does not exist (e.g. a
file added after `grackle parse` ran but before `grackle trace` finished).
Phase 6.2 may add an explicit filter; Phase 6.1 surfaces them as-is so
they're visible in tests.

## Consequences

**Known limitations:**

- **Module-level code** (`co_firstlineno == 1` for the script's `__main__`
  frame) is mapped to the file node ID via the `<module>` special case.
- **Lambda / comprehension frames** similarly fall back to the enclosing file
  node because the static parser does not emit nodes for them.
- **Generator depth drift** — see §1; `PY_YIELD`/`PY_RESUME` are not
  subscribed, so depth values for code observed while a generator is
  suspended may differ from a naive eager-call stack count. Resolves on its
  own when the generator returns.
- **`sys.monitoring.DISABLE` is only valid from `PY_START`**, so callbacks for
  the other events still pass through the project-file filter; the
  `is_project_file` check skips them without appending.
- **Overhead on large projects**: for a script that calls thousands of unique
  project functions per second, the per-call resolver lookup and list append
  add ~1–5 µs per event. PEP 669's DISABLE optimisation eliminates the vast
  majority of overhead (all stdlib/site-packages frames).
- **Thread safety**: the `_depth` dict is keyed by `threading.get_ident()` and
  is updated in callbacks called from potentially multiple threads. Python's
  GIL ensures no torn reads/writes on dict operations in CPython 3.12. No
  additional lock is used.
- **Eager event collection**: `RuntimeAdapter.trace()` returns an
  `Iterator[TraceEvent]` but the current implementation collects all events
  in a list first and then `yield from`s. Phase 6.2 may stream events; the
  Iterator type signature reserves the option.
- **`runpy` arg/cwd surface**: `Tracer.run()` invokes the target script via
  `runpy.run_path(str(script), run_name="__main__")`, which leaves
  `sys.argv` and the current working directory unchanged. Scripts that
  depend on `sys.argv[1:]` or a specific `os.getcwd()` must arrange those
  before calling `grackle trace`. The CLI `--help` text documents this.

**Cross-refs:** ADR-0003 (RuntimeAdapter Protocol), ADR-0006 (Python ast vs
Tree-sitter — tracer uses the Python static parser for ID resolution),
ADR-0009 (Tree-sitter chassis — not used by the tracer; the runtime path is
orthogonal).
