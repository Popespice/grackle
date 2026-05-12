# grackle

A local-first live code visualizer for Python.

> **Status**: Active solo development — contributions closed at this time. Fork freely under MIT. [Why?](./CONTRIBUTING.md)

---

## What it does

grackle traces your Python code and renders a live, interactive graph showing files, classes, and functions as nodes — with imports, calls, and inheritance as edges. Run your code once and watch the graph come alive.

- Static graph: built from `ast` analysis, no instrumentation needed
- Runtime overlay: call events stream via WebSocket using `sys.monitoring` (Python 3.12+, near-zero overhead)
- Pluggable adapter architecture — TypeScript, Go, and Rust parsers are on the roadmap

---

## Requirements

- **Python 3.12+** — install via [uv](https://docs.astral.sh/uv/getting-started/installation/)
- **Node 22 LTS** — install via [nvm](https://github.com/nvm-sh/nvm) or the [Windows installer](https://nodejs.org/en/download)
- **pnpm 9** — enabled via corepack: `corepack enable pnpm`

---

## Quickstart

### POSIX (macOS / Linux)

```bash
# Install dependencies
pnpm install
cd packages/agent && uv sync && cd ../..

# Start the agent + frontend together
pnpm dev
```

Then open [http://localhost:5173](http://localhost:5173).

### PowerShell (Windows)

```powershell
# Install dependencies
pnpm install
Push-Location packages/agent; uv sync; Pop-Location

# Start the agent + frontend together
pnpm dev
```

> **Windows note**: if you encounter path-length errors when cloning, run:
> `git config --system core.longpaths true`

---

## Architecture

See [`docs/adr/`](./docs/adr/) for architecture decision records explaining every major choice.

See [`docs/cross-platform.md`](./docs/cross-platform.md) for the cross-platform development discipline (path handling, encoding, CI matrix).

---

## License

MIT — © 2026 Connor Allen
