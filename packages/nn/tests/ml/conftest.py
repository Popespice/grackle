"""Make ``tests/ml/synth.py`` importable as ``import synth`` regardless of
pytest's ``--import-mode``.

Pytest's default (``prepend``) import mode inserts each test file's own
directory onto ``sys.path`` as a side effect of collecting it, which is the
only reason ``test_synthetic_acceptance.py``'s bare ``from synth import
make_synthetic_pair`` resolves today. ``--import-mode=importlib`` does not do
that insertion, so the same import raises ``ModuleNotFoundError`` under that
mode, and this project does not pin an import mode in ``pyproject.toml``.
Inserting this directory onto ``sys.path`` explicitly, here, makes the import
mode-independent: conftest.py files are always executed by pytest directly
(never as a package import gated by ``--import-mode``), so this insertion
runs before collection reaches ``synth.py``'s importers no matter which mode
is active.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Appended only if absent. Under the default ``prepend`` import mode pytest has
# already put this directory at sys.path[0] — ahead of the standard library —
# by the time this runs, so this line is a no-op there. It only matters under
# ``--import-mode=importlib``, where appending (rather than prepending) avoids
# *adding* a precedence the directory would not otherwise have.
#
# It does NOT make this directory safe to shadow stdlib names: under the
# default mode, which CI uses, a helper here named e.g. types.py or json.py
# still shadows the stdlib module for the whole session. Don't give helpers in
# this directory stdlib module names.
_HERE = str(Path(__file__).parent)
if _HERE not in sys.path:
    sys.path.append(_HERE)
