import os
import socket
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest


@pytest.fixture
def free_port() -> int:
    """Return a free ephemeral port on 127.0.0.1, OS-assigned."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return cast("int", s.getsockname()[1])


@pytest.fixture
def bump_mtime_forward() -> Callable[..., None]:
    """The :func:`_bump_mtime_forward` helper, as a fixture.

    Exposed as a fixture rather than imported (``from conftest import ...``)
    because that import only resolves under pytest's default ``prepend``
    import mode — which this project does not pin — and breaks outright under
    ``--import-mode=importlib``. It is also shadow-prone: adding a
    ``conftest.py`` to ``tests/python_runtime/`` or ``tests/node_runtime/``
    (the two subdirectories without ``__init__.py``) would rebind
    ``sys.modules["conftest"]`` and break the import from a file nobody
    touched. Fixture resolution goes through pytest's own conftest discovery
    and has neither problem.
    """
    return _bump_mtime_forward


def _bump_mtime_forward(path: Path, seconds: float = 5.0) -> None:
    """Force ``path``'s mtime forward by ``seconds``, guaranteeing it differs
    from whatever it was before this call — even on a filesystem/CI runner
    whose mtime resolution is too coarse to distinguish two back-to-back
    writes (observed in CI: a same-byte-length edit written immediately
    after priming a snapshot can land in the same mtime bucket on at least
    one Windows runner, which would otherwise make a "detect this edit"
    test flaky for an environment reason unrelated to the code under test —
    exactly the coarse-mtime gap ADR-0027 documents as an accepted
    limitation for real users, but not one this test suite should trip over
    by accident).

    Lives here, not in any one test module, because two suites need the same
    hard-won workaround: ``test_watcher.py`` (snapshot/diff detection) and
    ``test_server_predicted_heat.py`` (the model-mtime cache key). Keeping
    one copy means a future refinement — a larger offset, a platform carve-out
    — cannot land in one suite and silently leave the other flaky.
    """
    current_ns = path.stat().st_mtime_ns
    new_ns = current_ns + int(seconds * 1_000_000_000)
    os.utime(path, ns=(new_ns, new_ns))
