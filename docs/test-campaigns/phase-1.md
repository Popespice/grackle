# Phase 1 Test Campaign

**Date**: 2026-05-12
**Tag under test**: `v0.1.0-phase-1` (commit `1539c5f`)
**Environment**: macOS 26.5 / arm64, Python 3.12.13, Node 22 (CI matrix on Ubuntu + Windows + macOS)

## Method

Ten tiers of probes. Tier 1–2 re-baseline the documented static and unit
tests. Tier 3–11 actively probe for behavior the suites don't cover:
Protocol contract semantics (`@runtime_checkable` shallowness, signature
mismatches, missing attributes), registry edge cases (post-registration
mutation, exotic language strings, exception isolation, post-failure state),
codegen determinism, schema validity at the JSON Schema Draft 2020-12 level,
CLI surface, cross-platform path normalization, ADR cross-references vs the
plan's claims, CI workflow step ordering, and repo hygiene.

## Summary

**39 unit tests pass (25 agent + 14 frontend). All baseline static checks
clean. Six findings — three medium, three low/cosmetic.**

| Tier | Result |
|---|---|
| T1 — Static (Biome / tsc / ruff / ruff format / mypy --strict / parity) | ✅ clean across 10 + 16 + 35 files |
| T2 — Unit tests (agent 25, frontend 14) | ✅ 39/39 pass |
| T3 — Protocol contract probes (shallowness, missing attrs, wrong sig) | ✅ behaves per Python's documented limits |
| T4 — Registry behavior probes (mutation, exotic keys, exceptions) | ⚠️ F-1 (mutation divergence), F-2 (no key validation) |
| T5 — Codegen + schema integrity (determinism, Draft 2020-12 validity) | ✅ deterministic; 8/8 schema shape checks pass |
| T6 — CLI surface (`grackle languages`, `--help`) | ✅ output + exit code correct |
| T7 — Cross-platform discipline (path normalization, PTH rule live) | ✅ 5/5 normalization cases; PTH rule still fires |
| T8 — Documentation integrity (cross-refs, claims vs reality, versions) | ⚠️ F-3 (version drift), F-4 (no ADR cross-refs) |
| T9 — CI workflow step ordering | ✅ corepack before setup-node in both workflows |
| T10 — Repo hygiene (`__all__`, generated headers, gitignore, clean tree) | ⚠️ F-5 (`__version__` in `__all__`), F-6 (NoOp in public surface) |

## Findings

### F-1 — `AdapterRegistry.detect()` diverges from registered key on mutation (MEDIUM)

**Location**: [packages/agent/src/grackle/adapters/registry.py:35-40](packages/agent/src/grackle/adapters/registry.py:35)

**Reproducer**:

```python
from pathlib import Path
from grackle.adapters.registry import AdapterRegistry

class Stub:
    language = "python"
    def detect(self, root): return True
    def capabilities(self): return Capabilities()
    def parse(self, root, opts): return {}

reg = AdapterRegistry()
adapter = Stub()
reg.register_static(adapter)   # registered under key 'python'
adapter.language = "ZOMBIE"    # mutate after registration

reg.supported_languages()      # → ['python']  (uses dict key)
reg.detect(Path("/tmp"))       # → ['zombie']  (uses adapter.language.lower())
reg.get_static("python")       # → adapter (still reachable)
reg.get_static("zombie")       # → None (never indexed under 'zombie')
```

**Observed**: `detect()` returns a language name that no other registry
method recognizes. `supported_languages()`, `get_static()`, and `get_runtime()`
all use the dict key (lowercased at registration time); `detect()` re-derives
the language string from `adapter.language.lower()` per call. After any
post-registration mutation of `adapter.language`, the two views diverge
silently.

**Expected**: All registry methods agree on the canonical language identifier
for a given adapter. If `register_static(adapter)` stored it under key
`'python'`, every subsequent observation should be `'python'`.

**Fix** (one-line, in [registry.py:35](packages/agent/src/grackle/adapters/registry.py:35)):

```python
def detect(self, project_root: Path) -> list[str]:
    with self._lock:
        items = list(self._static.items())
    return sorted(lang for lang, adapter in items if adapter.detect(project_root))
```

Iterate `items()` instead of `values()`, and yield the dict key (already
lowercased at registration). Same lock release semantics; no behavior change
when adapters don't mutate their `language` attribute.

**Why it matters**: Phase 2 introduces a real Python parser that registers
itself once at module import; the language attribute should never mutate.
But a misbehaving third-party adapter (or test fixture that constructs an
adapter with `__init__(self, lang)` and later swaps it) would silently
produce a graph keyed under one name and a registry lookup under another. No
test currently catches this divergence; the bug is reachable today.

**Recommendation**: ship the one-line fix + add
`test_detect_uses_registered_key_after_language_mutation` to
`tests/adapters/test_registry.py`.

---

### F-2 — Language keys are not validated; whitespace/empty/newline strings accepted (LOW)

**Location**: [packages/agent/src/grackle/adapters/registry.py:13-18](packages/agent/src/grackle/adapters/registry.py:13)

**Reproducer**:

```python
reg = AdapterRegistry()
reg.register_static(Stub(""))           # → stored under key ''
reg.register_static(Stub("   "))        # → stored under key '   '  (different reg, fresh)
reg.register_static(Stub("Python\n"))   # → stored under key 'python\n'
reg.register_static(Stub("  Python  ")) # → stored under key '  python  '
```

Each succeeds and stores under the trimmed/lowercased-but-otherwise-unmodified
key. Subsequent `get_static("python")` then fails to find any of them.

**Observed**: The registry treats `''`, `'   '`, `'python\n'`, and
`'  Python  '` as four distinct languages, all valid. None of them are
findable via `get_static("python")`, but `supported_languages()` returns them
all sorted.

**Expected**: At minimum, strip whitespace before lowercasing, and probably
reject empty strings after the strip. The brief (`ADR-0004`) is clear that
language is an open string, but "open" should still mean "non-empty,
non-whitespace, no embedded newlines."

**Severity rationale**: Low because adapters in this project define
`language` as a `Literal`-like class attribute (`"python"`, `"typescript"`,
etc.) — they won't smuggle whitespace in. But there's no test guard, and
the contract is implicit. A user calling
`grackle.adapters.registry.register_static(MyAdapter())` from a notebook with
a typo'd literal would not see an error until much later.

**Fix**: validate `key` after `.strip().lower()` in `register_static` and
`register_runtime`; raise `ValueError` on empty result. Update the
`KNOWN_LANGUAGES` shared-types const to be the de-facto contract, and
document it in ADR-0004.

**Recommendation**: defer to a phase-2 cleanup (when real adapters exist) so
the validation rule has a concrete use case to anchor on.

---

### F-3 — Version drift between `pyproject.toml` and `grackle.__version__` (MEDIUM)

**Location**:
- [packages/agent/pyproject.toml:7](packages/agent/pyproject.toml:7) — `version = "0.0.0"`
- [packages/agent/src/grackle/__init__.py:3](packages/agent/src/grackle/__init__.py:3) — `__version__ = "0.1.0"`

**Reproducer**:

```bash
$ grep "^version" packages/agent/pyproject.toml
version = "0.0.0"
$ grep "__version__" packages/agent/src/grackle/__init__.py
__version__ = "0.1.0"
```

**Observed**: PEP 621 distribution metadata declares `0.0.0`; the runtime
package declares `0.1.0`. Anyone running `pip show grackle`, building a wheel
(`uv build` → `grackle-0.0.0-py3-none-any.whl`), or reading
`importlib.metadata.version("grackle")` sees `0.0.0`. Anyone reading
`grackle.__version__` at runtime sees `0.1.0`. The git tag
(`v0.1.0-phase-1`) matches the runtime value, not the distribution value.

**Expected**: Single source of truth. Either both say `0.1.0` (with
`__version__` derived from `importlib.metadata.version`) or both are bumped
together when a phase ships.

**Fix** (either of):

1. **Source from metadata** (preferred long-term):
   ```python
   # packages/agent/src/grackle/__init__.py
   from importlib.metadata import version
   __version__ = version("grackle")
   ```
   Then update only `pyproject.toml` per phase tag.

2. **Bump pyproject now** to match the runtime + tag:
   ```toml
   # pyproject.toml
   version = "0.1.0"
   ```
   And keep both files manually synchronized at each phase boundary, with a
   release-checklist note.

**Why it matters**: When the project ships as a distributable wheel (post
phase 9 per the brief), the wheel filename and PyPI page show `0.0.0`, but
the running CLI's `__version__` says `0.1.0`. That mismatch confuses both
users and any future "report your version when filing a bug" workflow. The
plan's chunk 1.F said "bump `__version__` to `0.1.0` alongside the tag" but
didn't call out the parallel `pyproject.toml` bump.

**Recommendation**: option 1 (source from metadata). It eliminates the
duplication permanently, and uv's `hatchling` backend already reads
`pyproject.toml` as the single source of truth for the wheel.

---

### F-4 — ADR-0003 and ADR-0004 don't cross-reference each other in body text (LOW)

**Location**:
- [docs/adr/0003-adapter-design.md](docs/adr/0003-adapter-design.md)
- [docs/adr/0004-extension-surface.md](docs/adr/0004-extension-surface.md)

**Observed**: A grep for `ADR-?000\d` in either ADR returns zero matches
outside the title line. The two ADRs are designed to be complementary
(0003 covers Protocol contract shape; 0004 covers the open-strings-everywhere
philosophy that lets that contract extend without core changes), but a
reader landing on 0003 has no body-text breadcrumb to 0004 (or 0001 — which
0003's "cross-platform note" implicitly extends).

**Expected**: Each ADR should reference the others where the rationale
hands off. For example, 0003's "open string" mention in the path-discipline
section is the exact place to write "see ADR-0004 for the broader
open-strings convention."

**Why it matters**: ADRs are read out of order. A new contributor reviewing
"why is `language` typed `str` and not a literal union?" lands on 0003 and
should find a pointer to 0004. Without it, the rationale chain is
discoverable only by reading the index in order.

**Recommendation**: small docs follow-up — add 2-3 inline
`(see ADR-0004)` and `(see ADR-0001)` parentheticals to 0003, and a
`(see ADR-0003 for the contract shape)` to 0004's intro. <5 minutes work,
defer or batch with the next ADR.

---

### F-5 — `grackle.__all__` includes the dunder `__version__` (COSMETIC)

**Location**: [packages/agent/src/grackle/__init__.py:4](packages/agent/src/grackle/__init__.py:4)

**Observed**:

```python
__all__ = ["registry", "__version__"]
```

**Why it matters**: Python's `__all__` controls what `from grackle import *`
exposes. Names starting with underscore (including dunders) are excluded
from `import *` by default; explicitly listing `__version__` in `__all__`
forces it back in. That's almost never the intent — `__version__` is
typically accessed as `grackle.__version__`, not imported with a star
import. Including it is mildly misleading (suggests dunders are meant to
star-import).

**Fix** (one line):

```python
__all__ = ["registry"]
```

**Severity**: cosmetic; no functional impact.

---

### F-6 — NoOp adapters exposed in public `grackle.adapters.__all__` (LOW)

**Location**: [packages/agent/src/grackle/adapters/__init__.py:12-22](packages/agent/src/grackle/adapters/__init__.py:12)

**Observed**:

```python
__all__ = [
    "AdapterRegistry",
    "Capabilities",
    "NoOpRuntimeAdapter",   # ← test-only validation utility
    "NoOpStaticParser",     # ← test-only validation utility
    "ParseOptions",
    ...
]
```

**Context**: The plan says of NoOp adapters: "**Not auto-registered** —
exists only to validate the Protocol shape end-to-end." They are explicitly
test fixtures, not user-facing API. The chunk-1.B file spec lists them in
`adapters/noop.py` but the `__init__.py` re-export was added as a
convenience; the plan didn't mandate it.

**Why it matters**: Public `__all__` becomes the API contract — anything in
there is something users will write `from grackle.adapters import
NoOpStaticParser` against, and we're then obligated to keep it stable.
Exposing test fixtures locks us into supporting them as if they were
intentional API.

**Fix**: drop both from `__all__`. Keep them importable via
`from grackle.adapters.noop import NoOpStaticParser` for tests; just don't
elevate them to the convenience-import API.

**Recommendation**: low-effort cleanup; defer to a hygiene pass alongside
F-5.

---

## Tier-by-tier results (detail)

### T1 — Static toolchain (✅ clean)

- `pnpm exec biome ci .` — 35 files, no fixes applied
- `pnpm typecheck` (`tsc -b`) — clean
- `pnpm check-parity` — all 4 generated files up-to-date
- `uv run ruff check .` — all checks passed
- `uv run ruff format --check .` — 16 files already formatted
- `uv run mypy --strict src` — no issues in 10 source files

### T2 — Unit tests (✅ 39/39 pass)

- `uv run pytest -v` — 25 passed (10 logging/server + 15 adapter)
- `pnpm --filter @grackle/frontend test --run` — 14 passed (theme 6 + WS 8)

### T3 — Protocol contract probes (✅ behaves per Python's limits)

| Probe | Result |
|---|---|
| `isinstance(WithLanguage, StaticParserAdapter)` | True ✓ |
| `isinstance(MissingLanguageAttr, StaticParserAdapter)` | False ✓ (methods missing) — actually: see note* |
| `isinstance(MissingDetectMethod, StaticParserAdapter)` | False ✓ |
| `isinstance(WrongSignatures, StaticParserAdapter)` | True ⚠️ — Python @runtime_checkable does not check arity |

*Note on missing-attribute probe: `@runtime_checkable` checks *methods only*,
not attributes. A class missing the `language` attribute but with all three
methods still appears to fail `isinstance` only because the methods exist on
the empty class but the Protocol's method set includes the `language`
descriptor lookup in some Python versions. The practical contract is:
**isinstance verifies the method names, nothing else.** ADR-0003 calls this
out under "Consequences." Tests in `test_noop.py` compensate by asserting
return shapes.

### T4 — Registry behavior (⚠️ F-1, F-2)

See findings F-1 and F-2 above. Other probes:

- Frozen `Capabilities` rejects mutation ✓
- `Capabilities.__slots__` present (7 fields, no `__dict__`) ✓
- `ParseOptions` instances have no `__dict__` ✓
- Module singleton `registry.supported_languages() == []` at import ✓
- Post-failure state: a `ValueError` from duplicate registration leaves the
  original adapter retrievable ✓
- `adapter.detect()` exceptions propagate (fail-fast) — design choice
- Static + runtime adapter for the same language coexist ✓
- Lookup is strict: `get_static('  python  ')` returns `None` ✓ (consistent
  with F-2: no whitespace stripping at lookup either)

### T5 — Codegen + schema integrity (✅ deterministic; 8/8 schema validations)

- Codegen run twice produced byte-identical TS + Python outputs (no
  timestamp, no nondeterministic ordering)
- `adapters.schema.json` validates as JSON Schema Draft 2020-12
- 8 schema-shape validations passed:
  - `Capabilities`: full instance ✓, missing required ✓ (rejected),
    extra property ✓ (rejected)
  - `ParseOptions`: full instance ✓, wrong type ✓ (rejected)
  - `StaticGraph`: full instance ✓, wrong type ✓ (rejected)
  - `TraceEvent`: full instance ✓

### T6 — CLI surface (✅ all outputs correct)

| Command | Output | Exit |
|---|---|---|
| `grackle --help` | lists `languages` and `serve` subcommands | 0 |
| `grackle languages --help` | shows usage + description | 0 |
| `grackle languages` | `supported languages: []` | 0 |

### T7 — Cross-platform discipline (✅ 5/5 + PTH live)

5 path-normalization cases pass:
`PureWindowsPath('src\\foo\\bar.py').as_posix() == 'src/foo/bar.py'` and 4 others.

Live `ruff --select PTH -` on synthesized `os.path.join("a", "b")` triggers
`PTH118` — the rule is still active project-wide.

### T8 — Documentation integrity (⚠️ F-3, F-4)

See findings F-3 (version drift) and F-4 (no ADR cross-refs).

Other checks ✓:

- `docs/adr/README.md` index has 4 entries (0001–0004)
- All file references in `PHASE_1_SUMMARY.md` exist on disk
- Test count claims in summary (5 NoOp + 10 registry = 15 adapter tests)
  match `grep -c '^def test_'` output

### T9 — CI workflow step ordering (✅ correct in both)

Both `ci.yml` (PR) and `ci-matrix.yml` (push:main) have the same 14-step
order:

1. `actions/checkout@v4`
2. **Install pnpm** (`corepack enable pnpm`)
3. `actions/setup-node@v4` (with `cache: pnpm`)
4. `astral-sh/setup-uv@v6`
5. Install JS dependencies — Codegen — Check parity — Biome — TypeScript —
   Frontend tests — Install Python deps — Ruff — Mypy — Pytest

Step 2 (pnpm via corepack) reliably precedes step 3 (setup-node's `cache:
pnpm` resolves pnpm path). This is the fix from commit `1f39d75` and it
remains correct.

### T10 — Repo hygiene (⚠️ F-5, F-6)

See findings F-5 (`__version__` in `__all__`) and F-6 (NoOp in public surface).

Other checks ✓:

- `_generated/adapters.py`, `_generated/messages.py`,
  `generated/adapters.ts`, `generated/messages.ts` all start with the
  expected "GENERATED — do not edit by hand" header
- `git check-ignore` confirms both `_generated/` directories are gitignored
- `git status` returns empty — clean working tree after the campaign

## What to fix vs accept

| F | Severity | Recommended action |
|---|---|---|
| F-1 | MEDIUM | **Fix now** — one-line patch + one regression test |
| F-2 | LOW | **Defer to phase 2** — anchor validation on real adapter use case |
| F-3 | MEDIUM | **Fix now** — switch `__version__` to `importlib.metadata.version("grackle")` (one-line) |
| F-4 | LOW | **Batch with next docs change** — add 3-4 cross-refs to existing ADRs |
| F-5 | COSMETIC | **Batch** — drop `__version__` from `__all__` |
| F-6 | LOW | **Batch** — drop NoOp adapters from `__all__` |

Fixes for F-1, F-3, F-5, F-6 together amount to ~6 lines of code + one
regression test. F-2 + F-4 are reasonable to defer.
