"""Import-hygiene enforcement for the agent -> grackle_nn boundary (Phase 12.2).

ADR-0028 states the direction: ``grackle_nn`` never imports ``grackle`` at
runtime (enforced on the nn side by ``packages/nn/tests/ml/test_import_hygiene.py``).
The permitted direction — the agent importing ``grackle_nn`` — must still stay
confined to function bodies inside ``grackle.ml_bridge``, never at module
scope anywhere in the agent package. If it leaked to module scope, importing
``grackle.cli`` or ``grackle.server`` (CLI startup) would eagerly import numpy
even for users who never touch `learn`/`predicted_heat`, defeating the whole
point of the capability gate (mirrors the go/rust/node toolchain gates).

Two independent checks catch disjoint failure modes, mirroring the nn-side
suite: a fresh subprocess catches anything actually executed at import time,
and a static AST scan (module-scope statements only — function-local imports
are expected and fine) catches an import that exists but hasn't been
exercised by any test path yet.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

_SRC_DIR = Path(__file__).parents[1] / "src" / "grackle"


def _is_grackle_nn(module: str) -> bool:
    return module == "grackle_nn" or module.startswith("grackle_nn.")


def _find_module_level_grackle_nn_imports(source: str) -> list[str]:
    """Only ``tree.body`` (top-level statements) — function-local imports,
    which ``ml_bridge.py`` uses deliberately, are out of scope for this scan.
    """
    tree = ast.parse(source)
    offenders = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            offenders += [
                f"line {node.lineno}: import {a.name}" for a in node.names if _is_grackle_nn(a.name)
            ]
        elif isinstance(node, ast.ImportFrom) and node.module and _is_grackle_nn(node.module):
            offenders.append(f"line {node.lineno}: from {node.module} import ...")
    return offenders


def test_fresh_subprocess_importing_cli_and_server_never_pulls_in_grackle_nn_or_numpy() -> None:
    script = "\n".join(
        [
            "import sys",
            "import grackle.cli",
            "import grackle.server",
            "leaked = [",
            "    m for m in sys.modules",
            "    if m in ('numpy',) or m == 'grackle_nn' or m.startswith('grackle_nn.')",
            "]",
            "assert not leaked, f'importing grackle.cli/server pulled in: {leaked}'",
        ]
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parents[1],
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_no_module_scope_grackle_nn_import_anywhere_in_agent_source() -> None:
    offenders = []
    for path in sorted(_SRC_DIR.rglob("*.py")):
        if "_generated" in path.parts:
            continue
        for hit in _find_module_level_grackle_nn_imports(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(_SRC_DIR)}: {hit}")
    assert not offenders, offenders


def test_find_module_level_imports_catches_comma_and_alias_forms() -> None:
    # Mirrors the discriminating-power lesson from the nn-side suite: a naive
    # regex only matching "grackle_nn" as the first name after import/from
    # would miss a comma-separated or aliased import.
    assert _find_module_level_grackle_nn_imports("import os, grackle_nn\n")
    assert _find_module_level_grackle_nn_imports("import grackle_nn as nn\n")
    assert _find_module_level_grackle_nn_imports("from grackle_nn.ml import HeatModel\n")
    assert not _find_module_level_grackle_nn_imports("import numpy as np\n")


def test_function_local_imports_are_not_flagged() -> None:
    # ml_bridge.py's actual pattern — imports inside function bodies — must
    # NOT trip the module-scope scan (that's the whole point of the gate).
    source = (
        "def learn_available() -> bool:\n"
        "    try:\n"
        "        import grackle_nn.ml  # noqa: F401\n"
        "    except ImportError:\n"
        "        return False\n"
        "    return True\n"
    )
    assert _find_module_level_grackle_nn_imports(source) == []


def test_ml_bridge_itself_has_zero_module_scope_grackle_nn_imports() -> None:
    ml_bridge_source = (_SRC_DIR / "ml_bridge.py").read_text(encoding="utf-8")
    assert _find_module_level_grackle_nn_imports(ml_bridge_source) == []


def test_agent_source_files_actually_scanned_non_vacuous() -> None:
    # Guards the scan above against silently walking zero files (a bad glob
    # or moved directory), which would make it pass vacuously.
    scanned = [p for p in _SRC_DIR.rglob("*.py") if "_generated" not in p.parts]
    assert len(scanned) > 20
    assert (_SRC_DIR / "ml_bridge.py") in scanned
