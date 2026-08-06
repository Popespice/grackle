"""Import-hygiene enforcement for grackle_nn.ml (Phase 12.1, block M8).

ADR-0028's rule ("nn -> grackle is dev-only") gets teeth here: the ML pipeline
must be importable and usable with zero runtime dependency on the agent
package. Two independent checks catch disjoint failure modes -- a fresh
subprocess catches anything actually executed at import time, and a static
AST scan catches ``TYPE_CHECKING``-gated imports the subprocess can never see
(they never execute). The AST scan (not a regex) so it can't be evaded by a
comma-separated ``import os, grackle`` or an aliased ``import grackle as g``.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import grackle  # noqa: F401

# ^ proves grackle is genuinely importable in this dev env, so a passing
# subprocess check below means grackle_nn.ml never imports it, not that
# grackle itself is unavailable.

_ML_DIR = Path(__file__).parents[2] / "src" / "grackle_nn" / "ml"


def _is_grackle(module: str) -> bool:
    return module == "grackle" or module.startswith("grackle.")


def _find_grackle_imports(source: str) -> list[str]:
    tree = ast.parse(source)
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            offenders += [
                f"line {node.lineno}: import {a.name}" for a in node.names if _is_grackle(a.name)
            ]
        elif isinstance(node, ast.ImportFrom) and node.module and _is_grackle(node.module):
            offenders.append(f"line {node.lineno}: from {node.module} import ...")
    return offenders


_ML_MODULES = (
    "grackle_nn.ml",
    "grackle_nn.ml.dataset",
    "grackle_nn.ml.features",
    "grackle_nn.ml.heat_model",
    "grackle_nn.ml.labels",
    "grackle_nn.ml.metrics_rank",
)


def test_fresh_subprocess_importing_ml_never_pulls_in_grackle() -> None:
    script = "\n".join(
        [
            "import sys",
            *(f"import {module}" for module in _ML_MODULES),
            "leaked = [m for m in sys.modules if m == 'grackle' or m.startswith('grackle.')]",
            "assert not leaked, f'grackle_nn.ml pulled in: {leaked}'",
        ]
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parents[2],
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_ml_source_never_contains_a_grackle_import_statement() -> None:
    offenders = []
    for path in sorted(_ML_DIR.glob("*.py")):
        for hit in _find_grackle_imports(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.name}: {hit}")
    assert not offenders, offenders


def test_find_grackle_imports_catches_comma_and_alias_forms() -> None:
    # The regex this replaced only matched "grackle" as the first name after
    # import/from -- a comma-separated or aliased import evaded it silently.
    assert _find_grackle_imports("import os, grackle\n")
    assert _find_grackle_imports("import grackle as g\n")
    assert _find_grackle_imports("from grackle.foo import bar\n")
    assert _find_grackle_imports("if True:\n    import grackle\n")  # nested, not top-level
    assert not _find_grackle_imports("import grackle_nn.ml as m\n")
    assert not _find_grackle_imports("from grackle_nn.ml import features\n")


def test_ml_source_files_actually_scanned_non_vacuous() -> None:
    # Guards the grep test above against silently scanning zero files (e.g. a
    # bad glob or moved directory), which would make it pass vacuously.
    assert sorted(p.name for p in _ML_DIR.glob("*.py"))
