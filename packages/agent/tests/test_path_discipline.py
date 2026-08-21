"""Path-discipline enforcement: `.relative_to(` calls stay confined to
``grackle.paths`` (test campaign tier T3-4/T3-5).

docs/cross-platform.md and ``grackle.paths``'s own module docstring establish
the convention: ``grackle.paths.to_posix`` is "the single sanctioned location
for ``Path.relative_to`` calls in the agent codebase." Every other path that
crosses the wire or persists (node IDs, annotation keys, manifest entries)
must be produced through it, so the same project on macOS/Linux/Windows
yields identical POSIX-relative IDs. Until this test existed, that invariant
was convention-only — nothing caught a stray direct ``.relative_to(`` call
anywhere else in the tree.

Two call sites are known, investigated exceptions and are allow-listed
below: they are pure containment predicates (they call ``.relative_to`` only
for its ``ValueError`` side effect, to check "is X inside Y", and never keep
or convert the returned relative path), never constructing a POSIX-relative
string that crosses the wire — that's the argument for why they're fine, but
nothing enforced the distinction before this test:

- ``cli.py`` (the ``trace`` command): validates SCRIPT is inside --root.
- ``python_runtime/node_resolution.py`` (``_normalize``): validates a
  ``co_filename`` is inside the project root before falling through to the
  real ``to_posix`` call on the very next line.

This scan is deliberately unrelated in *shape* to the module-scope-only AST
walker in ``test_ml_bridge_import_hygiene.py`` (which scans for function-local
``grackle_nn`` imports and, for that check, must deliberately not recurse
into function/class bodies). Here the opposite is true: both allow-listed
call sites above are themselves inside function bodies, and a future
unauthorized call could appear anywhere in the tree — module scope, a nested
function, a comprehension, wherever. ``ast.walk`` already recurses into
everything, which is exactly what's needed and is simpler than a custom
walker.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC_DIR = Path(__file__).parents[1] / "src" / "grackle"
_PATHS_MODULE = _SRC_DIR / "paths.py"

# The two known, investigated, legitimate exceptions (see module docstring).
# If this set stops matching reality in EITHER direction — a new file shows
# up, or one of these two stops calling relative_to() — the main test below
# fails and says so explicitly.
_ALLOWED_RELATIVE_TO_FILES = frozenset(
    {
        "cli.py",
        "python_runtime/node_resolution.py",
    }
)


def _find_relative_to_calls(source: str) -> list[int]:
    """Return the line number of every ``....relative_to(...)`` call in
    *source*, anywhere in the AST — module scope, inside a function or
    class body, nested in a comprehension, wherever.

    Deliberately uses ``ast.walk`` rather than a hand-rolled recursive
    ``ast.walk``, unlike the import-hygiene scanner in
    ``test_ml_bridge_import_hygiene.py``, matters here because both real
    call sites this scan must allow-list (``cli.py``, ``node_resolution.py``)
    are themselves inside function bodies — a module-scope-only walker would
    have silently missed them entirely, defeating the whole scan.
    """
    tree = ast.parse(source)
    lines: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "relative_to"
        ):
            lines.append(node.lineno)
    return lines


def _scanned_source_files() -> list[Path]:
    """Every agent-package source file this test holds to the convention:
    all of ``src/grackle``, excluding generated code and ``paths.py`` itself
    (the sanctioned module — its own internal ``relative_to`` call is the
    point of the module, not a violation of the rule it enforces)."""
    return [
        p
        for p in sorted(_SRC_DIR.rglob("*.py"))
        if "_generated" not in p.parts and p != _PATHS_MODULE
    ]


def test_relative_to_calls_confined_to_allow_listed_sites() -> None:
    offenders: dict[str, list[int]] = {}
    for path in _scanned_source_files():
        hits = _find_relative_to_calls(path.read_text(encoding="utf-8"))
        if hits:
            offenders[path.relative_to(_SRC_DIR).as_posix()] = hits

    found = set(offenders)

    unexpected = found - _ALLOWED_RELATIVE_TO_FILES
    assert not unexpected, (
        "Unauthorized relative_to() call(s) found outside the sanctioned "
        f"grackle.paths module: {[(f, offenders[f]) for f in sorted(unexpected)]}. "
        "Route path-to-POSIX conversion through grackle.paths.to_posix instead. "
        "If this call site is a genuinely legitimate containment-only "
        "predicate (discards the relative-path result, relies only on the "
        "ValueError side effect to check 'is X inside Y', never constructs a "
        "POSIX-relative string that crosses the wire), add it to "
        "_ALLOWED_RELATIVE_TO_FILES in this test with justification."
    )

    stale = _ALLOWED_RELATIVE_TO_FILES - found
    assert not stale, (
        f"Allow-listed file(s) no longer call relative_to(): {sorted(stale)}. "
        "Narrow _ALLOWED_RELATIVE_TO_FILES in this test to match reality — "
        "leaving a stale entry would let the allow-list silently drift "
        "over-permissive (e.g. masking a later unrelated violation that "
        "happens to land in a file already on the list)."
    )

    # Exact-set equality (not subset): both directions above must hold.
    assert found == _ALLOWED_RELATIVE_TO_FILES


def test_direct_call_at_module_scope_is_caught() -> None:
    assert _find_relative_to_calls("x.relative_to(y)\n") == [1]


def test_call_nested_in_function_body_is_caught() -> None:
    # This is exactly the case a module-scope-only walker (like the one in
    # test_ml_bridge_import_hygiene.py, which deliberately stops at def/
    # class boundaries) would have MISSED — proving ast.walk was the right
    # choice for this scanner, since both real allow-listed call sites
    # (cli.py, node_resolution.py) are themselves inside function bodies.
    source = "def f() -> None:\n    x.relative_to(y)\n"
    assert _find_relative_to_calls(source) == [2]


def test_chained_call_is_caught() -> None:
    # Matches the real cli.py / node_resolution.py shape: script.resolve()
    # .relative_to(root.resolve()) / abs_path.relative_to(self._root).
    source = "def f() -> None:\n    script.resolve().relative_to(root.resolve())\n"
    assert _find_relative_to_calls(source) == [2]


def test_similarly_named_call_is_not_a_false_positive() -> None:
    assert _find_relative_to_calls("x.relative_path(y)\n") == []


def test_bare_attribute_reference_without_a_call_is_not_flagged() -> None:
    assert _find_relative_to_calls("f = x.relative_to\n") == []


def test_agent_source_files_actually_scanned_non_vacuous() -> None:
    # Guards the scan above against silently walking zero/wrong files (a bad
    # glob or moved directory), which would make the main test pass vacuously.
    scanned = _scanned_source_files()
    assert len(scanned) > 20
    assert (_SRC_DIR / "cli.py") in scanned


def test_paths_module_itself_is_excluded_from_the_scan() -> None:
    assert _PATHS_MODULE not in _scanned_source_files()
