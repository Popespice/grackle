# Cross-platform discipline

grackle runs natively on macOS, Windows, and Linux. **No WSL fallback.**
This document is the contributor cheatsheet; ADRs 0001 and 0003 hold the
architectural rationale.

## Path handling (Python)

- **Always use `pathlib.Path`** — never string concatenation or `os.path.join`
  on strings that flow further through the system. The `ruff PTH` ruleset
  (in `pyproject.toml`) makes violations a lint error.
- **Normalize to POSIX form before persisting or transmitting**:
  ```python
  # Wrong: str(path) on Windows yields "src\\foo\\bar.py"
  # Right:
  path.relative_to(root).as_posix()  # → "src/foo/bar.py" everywhere
  ```
- The `grackle.paths` helper (phase 2) provides `to_posix(p, root) -> str`.
  Adapters **must** emit POSIX-style relative paths for node IDs so that
  the same project on Mac and Windows yields identical IDs.
- Open files with explicit encoding: `open(path, encoding="utf-8", errors="replace")`.
  Never rely on locale.

## Path handling (TypeScript/Node)

- Use `node:path.posix` for any path concatenation in scripts.
  The `tools/check-parity.mjs` and `scripts/codegen.mjs` scripts follow this.

## Filesystem awareness

| Property | macOS (APFS) | Windows (NTFS) | Linux (ext4) |
|---|---|---|---|
| Case sensitivity | Insensitive (default) | Insensitive | Sensitive |
| Long paths | Fine | Need `core.longpaths=true` for >260 chars | Fine |
| Symlinks | Fine | Require developer mode | Fine |

**Case deduplication**: adapters store paths as-found, but de-duplicate by
case-folded form on case-insensitive filesystems. Documented limitation: a
project shared across case-sensitive and case-insensitive filesystems may show
ghost duplicates.

**Long paths on Windows**: run `git config --system core.longpaths true` once
after installing Git. Noted in README.

## File watcher (`--watch`, ADR-0027)

`grackle/watcher.py` powers `grackle serve --watch` (live-growing graph, Phase 10.6). Two rules
distinguish it from a naive "re-parse on any FS event" watcher:

- **Hash-gate on content, not on the mtime/FS-event alone.** Every candidate change is
  confirmed by re-hashing the file's **bytes** (SHA-256, matching `cache.py`'s own hashing)
  before it counts as a real change. This is what makes an atomic-save (write-temp-then-rename)
  or a bare `touch` a no-op instead of a spurious rebuild+broadcast — and, because the hash is
  over bytes rather than decoded text, a checkout that only flips line endings is a no-op too.
- **Coarse mtime is a fast-path hint, not a guaranteed-correct gate.** A `(mtime, size)` pair
  that hasn't moved skips re-hashing (cheap on a quiet tree); whenever it *has* moved, the byte
  hash is authoritative. On filesystems whose mtime resolution is coarser than the edit cadence
  (FAT32/exFAT ~2s, older HFS+ ~1s, some network/virtualized mounts), a same-size content edit
  that lands within one mtime bucket can be missed by the stdlib poller — not just delayed, but
  missed until some *later*, distinguishable edit touches the same file again (see ADR-0027's
  Known limitations). The optional `watchfiles` backend does not share *this specific*
  mechanism (no mtime-based fast-path skip against a full-tree baseline) — but it has its own,
  differently-shaped permanent-miss gap: a transient stat/read failure racing a path's one and
  only reported FS event leaves that file's hash frozen at its pre-edit value for the rest of
  the session, since this backend never re-examines a path it wasn't just told changed (see
  ADR-0027's Known limitations).

**The optional `watchfiles` backend never becomes a required dependency for the *distributed
package*.** An end user's `pip install grackle` (or `grackle[watch]`) is unaffected either way.
A **contributor** following this repo's documented bootstrap (`uv sync`, no extras) *does* get
`watchfiles` installed, because it's also listed in the `dev` dependency-group so CI exercises
and type-checks the optional backend by default — pass `--watch-poll` to exercise the stdlib
fallback locally regardless of what's installed. `pip install grackle[watch]` is how an end
user opts into the event-driven backend for lower latency.

## Subprocess design

- Asyncio event loop: use `asyncio.run()`, not `loop.run_until_complete()`.
- Subprocess start method: design for **`spawn`** semantics, not `fork`.
  Windows has no `fork()`; `multiprocessing` defaults to `spawn` on Windows.
  The phase 6 tracer must be `spawn`-compatible.
- Socket binding: always `127.0.0.1`, never Unix-domain sockets.

## Line endings

`.gitattributes` enforces LF for all source files. Git converts on checkout
(`text=auto eol=lf`). Shell scripts (`.ps1`, `.cmd`, `.bat`) use CRLF.
Never set `core.autocrlf=true` in project config — let `.gitattributes` own it.

## Color / ANSI in the terminal

`structlog` auto-loads `colorama` on Windows for ANSI translation.
The JSON log format is unaffected. Never assume ANSI is supported;
always test JSON mode via `GRACKLE_LOG_FORMAT=json`.

## CI matrix

| Trigger | OSes | Rationale |
|---|---|---|
| Pull request | `ubuntu-latest` + `windows-latest` | Cheapest baseline + divergent platform |
| `push: main` | `ubuntu-latest` + `windows-latest` + `macos-latest` | macOS join at merge; 10× minutes off PR critical path |

`fail-fast: false` so both OSes always finish and you see all failures.

## Scripts policy

- **No bash-only scripts** — `tools/check-parity.mjs` (Node) handles what
  would otherwise be a `.sh`. Hooks invoke only cross-platform binaries.
- Hook commands work identically in Git Bash, PowerShell, and modern `cmd.exe`.

## Quick reference

```bash
# Verify POSIX path normalization
python -c "
from pathlib import PureWindowsPath, PurePosixPath
assert PureWindowsPath('src\\\\foo\\\\bar.py').as_posix() == 'src/foo/bar.py'
assert PurePosixPath('src/foo/bar.py').as_posix() == 'src/foo/bar.py'
print('ok')
"

# Verify only loopback is listening after grackle serve
# bash / Git Bash on Windows:
netstat -an | grep -E "7878|5173"
# Expected: only 127.0.0.1:NNNN entries
```
