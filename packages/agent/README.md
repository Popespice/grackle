# grackle (agent)

Python WebSocket agent for the grackle live code visualizer.

## Setup

```bash
# From packages/agent/
uv sync
```

## Running

```bash
uv run grackle serve          # default: 127.0.0.1:7878
uv run grackle serve --port 9000
```

## Testing

```bash
uv run pytest
uv run pytest -q              # quiet output
```

## Lint / typecheck

```bash
uv run ruff check src tests
uv run mypy --strict src
```

## Cross-platform notes

- The agent binds to `127.0.0.1` only (never `0.0.0.0`).
- All file paths in API responses use POSIX-style forward slashes regardless of OS.
- Runs natively on macOS, Windows, and Linux. No WSL or Cygwin required.
- Long paths on Windows: `git config --system core.longpaths true` if you hit 260-char limits.
