# ADR-0010: Rust Adapter Integration

**Status:** accepted  
**Date:** 2026-05-18  
**Deciders:** Connor Allen

---

## Context

Phase 5.1 adds Rust to the set of statically-parsed languages. ADR-0006 named Rust as a future Tree-sitter language and ADR-0009 established the Tree-sitter chassis. This ADR records the Rust-specific decisions layered on top of that chassis.

---

## Decision 1: Rust traits map to the `interface` node kind

**Decision:** Emit `trait` declarations with `kind = "interface"` and `metadata.subkind = "trait"`.

**Alternatives considered:**

- **New `trait` kind** — Would require additions to `KNOWN_NODE_KINDS`, `kinds.py`, `tokens.css`, and the `GraphLegend`. No user-visible value: traits serve the same structural role (typed contract) as interfaces and enums across other languages. Frontend rendering is identical.
- **Re-use `class` kind** — Rejected: traits are not classes; `interface` is the correct semantic match.

**Why `interface` is correct:** Traits define contracts that other types implement, exactly like Go interfaces and TypeScript interfaces. The `subkind` field preserves Rust-specific semantics for any future analysis that distinguishes trait methods from interface methods.

---

## Decision 2: Cargo workspace enumeration via filesystem glob, not `cargo` CLI

**Decision:** Parse `workspace.members` globs from the root `Cargo.toml` using `pathlib.Path.glob`; never invoke `cargo` as a subprocess.

**Rationale:**

- **No subprocess, no PATH dependency.** The Go adapter's `go.mod` reader sets the precedent: read the manifest file, don't shell out. Invoking `cargo metadata` would require Cargo to be installed and on PATH (not guaranteed in all environments), introduce potential subprocess injection, and be slower.
- **Offline operation.** `cargo` may attempt network fetches (crates.io registry checks). Pure filesystem reads are always offline.
- **Sufficient for graph construction.** `workspace.members` is a simple list of paths/globs. No workspace-level dependency resolution is needed to build a static symbol graph.

**Limitation:** Does not handle `exclude` or `default-members` workspace directives. If a `Cargo.toml` specifies `[workspace] exclude = [...]`, excluded paths are still walked. This edge case is unlikely to matter for the fixtures or typical projects where grackle is used.

---

## Decision 3: tree-sitter core library bumped to `>=0.25,<0.26`

**Decision:** Upgrade the `tree-sitter` core library pin from `>=0.23,<0.24` to `>=0.25,<0.26`. Grammar pins for Go and TypeScript remain at `>=0.23,<0.24`.

**Context:** All available `tree-sitter-rust` Python packages (versions 0.21.x through 0.23.x) use Language ABI version 15. The `tree-sitter` library `0.23.x` only supports ABI versions 13–14. `tree-sitter` `0.25.x` is the first minor version that supports ABI version 15 while remaining backward-compatible with ABI versions 14 (Go/TypeScript grammars). `0.24.x` was verified not to support ABI 15. Go (0.23.x, ABI 14) and TypeScript (0.23.x, ABI 14) pass all 142 existing tests unchanged after the upgrade.

**Grammar pins not changed:** `tree-sitter-go>=0.23,<0.24` and `tree-sitter-typescript>=0.23,<0.24` remain. No 0.24.x or 0.25.x versions are published for these grammars; the 0.23.x versions are ABI 14 and work with tree-sitter 0.25.x.

---

## Decision 4: Method calls resolve via `field_expression` function nodes

**Decision:** In tree-sitter-rust's AST, `obj.method()` is represented as a `call_expression` whose `function` child is a `field_expression`, not a `method_call_expression`. The call collector handles `field_expression` by returning its full text (e.g. `"self.create"`, `"repo.list"`).

**Rationale:** Unlike tree-sitter-go (which uses `call_expression` + `selector_expression`) or tree-sitter-typescript, tree-sitter-rust uses `field_expression` uniformly for both field access and method calls. The full text of the `field_expression` node gives the canonical `receiver.method` form needed by the resolver.

---

## Out of scope (future phases)

- **Procedural macros / derive macros.** Attribute nodes (`#[derive(...)]`) are ignored.
- **Generic monomorphization tracking.** Generic parameters are stripped during type-name extraction; `Vec<User>` resolves as `Vec`.
- **`build.rs` execution.** Build scripts are not parsed (they're `.rs` files and will be enumerated, but their generated code is not traced).
- **Conditional compilation** (`#[cfg(...)]`). All items are included regardless of compile-time features.
- **`workspace.exclude` / `workspace.default-members`.** Not handled; see Decision 2.

---

## Cross-references

- ADR-0003: Adapter Protocol design — `RustStaticParser` implements `StaticParserAdapter`
- ADR-0006: Python ast vs Tree-sitter — Rust was explicitly named as a future Tree-sitter language
- ADR-0009: Tree-sitter chassis — `RustWalker` extends `TreeSitterWalker`
