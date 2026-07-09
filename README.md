# grackle

A local-first live code visualizer for Python, TypeScript, Go, and Rust.

> **Status**: Active solo development — contributions closed at this time. Fork freely under MIT. [Why?](./CONTRIBUTING.md)

---

## What it does

grackle parses your codebase and renders a live, interactive graph showing files, classes, and functions as nodes — with imports, calls, and inheritance as edges. Run your code once and watch the graph come alive with real execution data; edit a file and watch the graph grow.

- **Static graph** across four languages — Python (`ast`), TypeScript, Go, and Rust (Tree-sitter) — with cross-language edges (HTTP routes, subprocess spawns) and cycle detection, no instrumentation needed
- **Runtime overlay** — call events stream over a local WebSocket from a runtime adapter per language: Python via `sys.monitoring` (near-zero overhead), TypeScript/Node via the V8 Inspector, Go and Rust via compiler coverage instrumentation
- **Time-travel debugging** — scrub a trace to inspect a function's captured arguments and return value at that instant, with name-based redaction of sensitive values (opt-in, Python-only today)
- **Explanation layer** — click any edge to see the exact source line that justifies it; pick a node and firing to read the causal call chain that led to it
- **Analysis platform** — flame graphs, coverage/heat aggregation, a session library for saved traces, and differential analysis (`grackle diff`, CI-usable) between two traces or a trace and the static graph
- **Watch mode** — `grackle serve --watch` re-parses on file changes and animates the diff into the live graph, preserving layout and camera instead of a jarring rebuild
- Pluggable adapter architecture (`docs/adr/0003-adapter-design.md`) — new languages register a static parser and/or runtime adapter without touching the wire protocol

---

## Requirements

- **Python 3.12+** — install via [uv](https://docs.astral.sh/uv/getting-started/installation/)
- **Node 22 LTS** — install via [nvm](https://github.com/nvm-sh/nvm) or the [Windows installer](https://nodejs.org/en/download)
- **pnpm 11+** — install via `npm install -g pnpm` or corepack: `corepack enable pnpm`

Optional, only needed to trace a project in that language (each adapter is capability-gated — grackle gives a clean error, not a crash, when a toolchain is missing):

- **Go 1.20+** — for the Go runtime adapter (`go build -cover`)
- **Rust + `llvm-tools-preview`** — for the Rust runtime adapter (`rustup component add llvm-tools-preview`)

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
