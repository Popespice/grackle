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

sys.path.insert(0, str(Path(__file__).parent))
