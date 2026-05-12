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
