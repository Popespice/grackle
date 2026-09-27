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

What counts as a use: any reference to a ``.relative_to`` attribute (a direct
call, but also a bound-method alias like ``rel = p.relative_to`` or an unbound
``Path.relative_to`` passed to ``map``), ``getattr(x, "relative_to")``, and the
same three forms for ``os.path.relpath`` — which returns backslash-separated
paths on Windows and is not flagged by ruff's PTH ruleset, so without this
scan it would be an unguarded way around the rule. The allow-list pins a
per-file *count*, so an extra use added to an already-allow-listed file fails
just like a use in a new file.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TypeGuard

_SRC_DIR = Path(__file__).parents[1] / "src" / "grackle"
_PATHS_MODULE = _SRC_DIR / "paths.py"

# The two known, investigated, legitimate exceptions (see module docstring),
# pinned by per-file use COUNT, not just file name: cli.py is 1,100+ lines, and
# a file-name-only allow-list would let a second, wire-bound use added there
# pass unseen. If this stops matching reality in EITHER direction — a new or
# extra use, or an allow-listed use removed — the main test fails and says so.
_ALLOWED_RELATIVE_PATH_USES: dict[str, int] = {
    "cli.py": 1,
    "python_runtime/node_resolution.py": 1,
}

_FORBIDDEN_NAMES = frozenset({"relative_to", "relpath"})


def _find_relative_path_uses(source: str) -> list[int]:
    """Return the line number of every use of ``.relative_to`` or
    ``os.path.relpath`` in *source* (see the module docstring for what counts
    as a use), anywhere in the AST — module scope, inside a function or class
    body, nested in a comprehension, wherever.

    Uses ``ast.walk``, which recurses everywhere, rather than the custom
    module-scope-only walker in ``test_ml_bridge_import_hygiene.py``. That
    difference is deliberate and load-bearing: both call sites this scan must
    allow-list (``cli.py``, ``node_resolution.py``) are themselves inside
    function bodies, so a module-scope-only walker would miss them entirely
    and the scan would silently find nothing.
    """
    return sorted(node.lineno for node in ast.walk(ast.parse(source)) if _is_use(node))


def _is_use(node: ast.AST) -> TypeGuard[ast.expr | ast.stmt]:
    # Any attribute reference, not only a direct call: `rel = p.relative_to` and
    # `map(Path.relative_to, ...)` are uses too. A direct call is still counted
    # once — its func is exactly one such Attribute node.
    if isinstance(node, ast.Attribute):
        return node.attr in _FORBIDDEN_NAMES
    if isinstance(node, ast.Call):
        return (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in _FORBIDDEN_NAMES
        )
    if isinstance(node, ast.ImportFrom):
        return any(alias.name == "relpath" for alias in node.names)
    return False


def _scannable(agent_source_files: list[Path]) -> list[Path]:
    """The files this test holds to the convention: everything the shared
    ``agent_source_files`` fixture enumerates, minus ``paths.py`` itself (the
    sanctioned module — its own internal ``relative_to`` call is the point of
    the module, not a violation of the rule it enforces)."""
    return [p for p in agent_source_files if p != _PATHS_MODULE]


def test_relative_path_uses_confined_to_allow_listed_sites(
    agent_source_files: list[Path],
) -> None:
    offenders: dict[str, list[int]] = {}
    for path in _scannable(agent_source_files):
        hits = _find_relative_path_uses(path.read_text(encoding="utf-8"))
        if hits:
            offenders[path.relative_to(_SRC_DIR).as_posix()] = hits

    unexpected = {
        f: lines
        for f, lines in offenders.items()
        if len(lines) > _ALLOWED_RELATIVE_PATH_USES.get(f, 0)
    }
    assert not unexpected, (
        "Unauthorized relative_to()/os.path.relpath use(s) outside the sanctioned "
        f"grackle.paths module (file -> lines): {dict(sorted(unexpected.items()))}. "
        "Route path-to-POSIX conversion through grackle.paths.to_posix instead. "
        "If this call site is a genuinely legitimate containment-only "
        "predicate (discards the relative-path result, relies only on the "
        "ValueError side effect to check 'is X inside Y', never constructs a "
        "POSIX-relative string that crosses the wire), add it to "
        "_ALLOWED_RELATIVE_PATH_USES in this test with justification."
    )

    stale = {
        f: (len(offenders.get(f, [])), allowed)
        for f, allowed in _ALLOWED_RELATIVE_PATH_USES.items()
        if len(offenders.get(f, [])) < allowed
    }
    assert not stale, (
        f"Allow-listed use(s) no longer present (file -> (found, allowed)): {stale}. "
        "Lower the count in _ALLOWED_RELATIVE_PATH_USES to match reality — "
        "leaving a stale entry would let the allow-list silently drift "
        "over-permissive (e.g. masking a later unrelated violation that "
        "happens to land in a file already on the list)."
    )

    # The two assertions above are jointly exact equality of per-file counts:
    # nothing beyond the allow-list, and nothing on it missing. A third bare
    # equality assert would add no coverage and report a worse message.


def test_direct_call_at_module_scope_is_caught() -> None:
    assert _find_relative_path_uses("x.relative_to(y)\n") == [1]


def test_call_nested_in_function_body_is_caught() -> None:
    # This is exactly the case a module-scope-only walker (like the one in
    # test_ml_bridge_import_hygiene.py, which deliberately stops at def/
    # class boundaries) would have MISSED — proving ast.walk was the right
    # choice for this scanner, since both real allow-listed call sites
    # (cli.py, node_resolution.py) are themselves inside function bodies.
    source = "def f() -> None:\n    x.relative_to(y)\n"
    assert _find_relative_path_uses(source) == [2]


def test_chained_call_is_caught() -> None:
    # Matches the real cli.py / node_resolution.py shape: script.resolve()
    # .relative_to(root.resolve()) / abs_path.relative_to(self._root).
    source = "def f() -> None:\n    script.resolve().relative_to(root.resolve())\n"
    assert _find_relative_path_uses(source) == [2]


def test_similarly_named_call_is_not_a_false_positive() -> None:
    assert _find_relative_path_uses("x.relative_path(y)\n") == []


def test_bound_method_alias_is_caught() -> None:
    # `rel = p.relative_to` is almost always followed by `rel(root)`, which the
    # Call-only matcher this replaced could not see.
    assert _find_relative_path_uses("rel = p.relative_to\nrel(root)\n") == [1]


def test_unbound_reference_and_getattr_forms_are_caught() -> None:
    assert _find_relative_path_uses("map(Path.relative_to, ps, rs)\n") == [1]
    assert _find_relative_path_uses('getattr(p, "relative_to")(root)\n') == [1]


def test_os_path_relpath_forms_are_caught() -> None:
    # ruff's PTH ruleset does not flag os.path.relpath, and it returns
    # backslash-separated paths on Windows.
    assert _find_relative_path_uses("os.path.relpath(a, b)\n") == [1]
    assert _find_relative_path_uses("from os.path import relpath\n") == [1]


def test_each_use_in_a_file_is_counted() -> None:
    # The per-file count is what stops a second use in an allow-listed file
    # from hiding behind the first.
    source = "a.relative_to(b)\ndef f() -> None:\n    c.relative_to(d)\n"
    assert _find_relative_path_uses(source) == [1, 3]


def test_agent_source_files_actually_scanned_non_vacuous(
    agent_source_files: list[Path],
) -> None:
    # Guards the scan above against silently walking zero/wrong files (a bad
    # glob or moved directory), which would make the main test pass vacuously.
    scanned = _scannable(agent_source_files)
    assert len(scanned) > 20
    assert (_SRC_DIR / "cli.py") in scanned


def test_paths_module_itself_is_excluded_from_the_scan(
    agent_source_files: list[Path],
) -> None:
    # paths.py must be in the shared enumeration but out of this scan.
    assert _PATHS_MODULE in agent_source_files
    assert _PATHS_MODULE not in _scannable(agent_source_files)
