"""Shared test configuration for packages/nn.

Two jobs, in one file because a second ``conftest.py`` in ``tests/ml/`` would
collide with this one as mypy's module ``conftest`` (neither directory is a
package):

1. Make ``tests/ml/synth.py`` importable as ``import synth`` regardless of
   pytest's ``--import-mode`` (campaign T2-3). The default ``prepend`` mode
   inserts each test file's own directory onto ``sys.path`` while collecting
   it, which is the only reason ``test_synthetic_acceptance.py``'s bare
   ``from synth import make_synthetic_pair`` resolves; ``--import-mode=
   importlib`` does no such insertion. conftest.py files are always executed by
   pytest's own discovery (never gated by ``--import-mode``), and this one runs
   before collection reaches ``tests/ml/``, so inserting that directory here
   makes the import mode-independent.
2. Hypothesis profiles (campaign C5) — the same two as the agent's
   ``tests/conftest.py``: "ci" is the default (derandomized, no example
   database, so the gate is reproducible and writes nothing), "nightly" is
   selected by the campaign workflow via ``HYPOTHESIS_PROFILE=nightly``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from hypothesis import settings

# Appended only if absent. Under the default ``prepend`` import mode pytest has
# already put tests/ml at sys.path[0] — ahead of the standard library — by the
# time a test there is collected, so this is a no-op; it only matters under
# ``--import-mode=importlib``, where appending (rather than prepending) avoids
# *adding* a precedence the directory would not otherwise have. It does NOT make
# tests/ml safe to shadow stdlib names under the default mode: don't give
# helpers there stdlib module names.
_ML_TESTS = str(Path(__file__).parent / "ml")
if _ML_TESTS not in sys.path:
    sys.path.append(_ML_TESTS)

settings.register_profile("ci", max_examples=100, deadline=None, derandomize=True, database=None)
settings.register_profile("nightly", max_examples=5000, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))
