# ADR-0012 — Cross-language edges: HTTP route matching + subprocess references

**Status:** accepted

## Context

Phase 4's `parse_all` merged per-language graphs into a polyglot result but produced two disjoint
subgraphs side-by-side with no edges spanning the language boundary. Phase 5 introduces the first
relationships that cross languages: HTTP route matching (Python `requests.get('/api/users')` →
TypeScript `app.get('/api/users', …)`) and subprocess references (Python
`subprocess.run(['./scripts/build.ts'])` → that file's node in the graph).

Two approaches were considered for extracting these relationships:

**Heuristic regex on source text (chosen):** Scan each source file with language-specific regexes
during the existing walker pass. Emit `CrossLanguageHint` dicts (`kind`, `node_id`, `payload`) into
`graph.metadata.cross_language_hints`. After all languages are parsed, `parse_all` resolves hints
into `cross_language_call` / `cross_language_spawn` edges by matching client paths to server paths
and subprocess commands to file node IDs.

**Symbol-table-based (rejected for Phase 5):** Walk the full resolved symbol graph for each
language and emit cross-language edges from type annotations or module boundaries. Deferred because
it requires complete cross-language type inference and is significantly more complex to implement
across four language grammars. Can be revisited in Phase 6+.

## Decision

### Two edge kinds

- `cross_language_call` (display name "HTTP call", dashed line) — connects an HTTP client call site
  (file node) to the HTTP server handler file that registers the matching route.
- `cross_language_spawn` (display name "Subprocess", dotted line) — connects a subprocess
  invocation site to the file node for `argv[0]` when it resolves to a file in the project tree.

Two edge kinds rather than one: the semantic meaning is distinct (synchronous data call vs process
spawn); the visual styling is distinct (dashed vs dotted); users may want to filter one without the
other using the existing legend toggles.

### HTTP path normalisation

Before comparing client and server paths, both sides are normalised:

1. Strip surrounding whitespace and lowercase.
2. Trim trailing slash (`/users/` → `/users`).
3. Collapse parameter patterns — `{id}`, `:id`, and `<id>` all become `{param}`.

This means a Python client calling `/users/{id}` and a TypeScript server registering `/users/:id`
are treated as the same route. The normalisation is deterministic and reversible (the original
path is preserved in `edge.metadata.http_path`).

A server path is indexed only when it has **≥ 2 non-empty segments** (e.g. `/api/users` has two:
`api` and `users`). Trivial single-segment paths like `/`, `/health`, or `/ping` are excluded
because they appear across too many unrelated client/server pairs and produce noise.

### Subprocess matching

`argv[0]` only. The command is normalised by stripping leading `./` characters and converting
backslashes to `/`. It is then matched against graph file node IDs by:

1. Exact match (`cmd_norm == file_id`).
2. Suffix match (`file_id.endswith("/" + cmd_norm)`).

No `PATH` lookup. No `which`. If `argv[0]` resolves outside the project root, no edge is emitted.

### Hint plumbing

Hints are extracted during the same walker pass that builds nodes/edges. Each adapter's walker
overrides `hints_for_file(source, file_id) -> list[dict]` (a hook on `TreeSitterWalker`), which
calls the language-specific `extract_hints` function. `PythonAstWalker` calls `extract_hints`
inline. Hints accumulate in `graph.metadata.cross_language_hints` (a list of dicts) and survive
the `parse_all` merge step where they are read and cleared before the final graph is returned.

### Framework coverage (allow-list)

The regex allow-list is intentionally narrow. Only the listed frameworks are recognised; unmatched
patterns are silently ignored (ADR-0004 open-string surface: unknown kinds are not errors).

**Python:** `requests` and `httpx` (HTTP client); Flask `@app.route`, FastAPI `@router.get/post/…`,
Django `path()` (HTTP server); `subprocess.run/Popen/call([...])`, `os.system('...')` (subprocess).

**TypeScript:** `fetch`, `axios.{get,post,put,delete,patch,request}` (HTTP client); Express/Fastify/Hono
`app.{get,post,put,delete,patch}`, `router.{get,post,put,delete,patch}` (HTTP server);
`exec/spawn/fork`, `execa` (subprocess).

**Go:** `http.{Get,Post,Put,Delete,Patch,NewRequest}` (HTTP client); `*.HandleFunc`, `http.Handle`
(HTTP server); `exec.Command` (subprocess).

**Rust:** `reqwest::get`, `reqwest::Client.{get,post,…}` (HTTP client); `.route(…)` (Axum/Actix
HTTP server); `Command::new` (subprocess).

## Consequences

**Known limitations:**

- **False positives.** A generic path like `/api/config` appearing in both a test harness and a
  real handler will generate spurious edges. The ≥2-segment filter reduces but does not eliminate
  this. Users can filter cross-language edge kinds out using the existing legend toggles.
- **False negatives.** Dynamic URL construction — `f"/users/{user_id}"`, template literals,
  `String::from` chains — is not matched. Only string literals are recognised. Future enhancement
  could extract format-string templates and match patterns; literal-only is acceptable scope for
  Phase 5.
- **Subprocess `argv[0]` only.** `subprocess.run(['node', 'scripts/build.ts'])` emits a hint
  with command `node`, which resolves to nothing unless a file named `node` is in the project
  tree. Use `subprocess.run(['./scripts/build.ts'])` to reference a project file directly.
- **Import-side resolution.** Hints fire at the file level (node_id = file POSIX path). The edge
  connects files, not individual function call sites, matching the existing node granularity for
  Phase 5.

**Cross-refs:** ADR-0004 (open-string extension surface), ADR-0009 (Tree-sitter chassis —
hints are extracted in the same walker pass as nodes/edges).
