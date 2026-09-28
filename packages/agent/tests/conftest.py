import asyncio
import contextlib
import os
import socket
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import pytest
from hypothesis import settings as _hypothesis_settings

# Hypothesis profiles (campaign C5, tier T8). "ci" is the default: derandomized
# and database-free, so a property test is exactly as reproducible as any other
# test in the gate and writes nothing to the tree. The nightly campaign workflow
# selects "nightly" (HYPOTHESIS_PROFILE=nightly) for fresh randomness and a far
# larger example budget. deadline=None in both: per-example timing on shared CI
# runners (Windows especially) is too noisy to be a signal.
_hypothesis_settings.register_profile(
    "ci", max_examples=100, deadline=None, derandomize=True, database=None
)
_hypothesis_settings.register_profile("nightly", max_examples=5000, deadline=None)
_hypothesis_settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))


@pytest.fixture
def free_port() -> int:
    """Return a free ephemeral port on 127.0.0.1, OS-assigned."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return cast("int", s.getsockname()[1])


StartServer = Callable[..., Awaitable[tuple["asyncio.Task[None]", int]]]


@pytest.fixture
async def start_server() -> AsyncIterator[StartServer]:
    """Start ``grackle.server.serve`` and return ``(task, port)`` once the
    socket is listening. Keyword arguments pass through to ``serve()``.

    Use this, never ``create_task(serve(...))`` followed by a sleep
    (campaign T5-2, ``docs/test-campaigns/phase-12.md``): nothing awaitable
    sits between task creation and the bind, so a fixed sleep is a race a
    slow runner loses — the ``[WinError 1225]`` connect-refused flake. This
    waits on ``serve()``'s readiness future instead, and binds port 0 so the
    OS assigns the port at bind time; ``free_port`` probes a number and
    releases it before ``serve()`` rebinds it, and another process can take
    it in between.

    Stop a server early with ``task.cancel()`` and await it; any still
    running are stopped at teardown. A fixture rather than an importable
    helper for the reason given at :func:`bump_mtime_forward`.
    """
    from grackle.server import serve

    tasks: list[asyncio.Task[None]] = []

    async def _start(**kwargs: Any) -> tuple[asyncio.Task[None], int]:
        ready: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        task = asyncio.create_task(serve("127.0.0.1", 0, ready=ready, **kwargs))
        tasks.append(task)
        either: set[asyncio.Future[Any]] = {ready, task}
        await asyncio.wait(either, timeout=30.0, return_when=asyncio.FIRST_COMPLETED)
        if not ready.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task  # re-raises serve()'s own error if it died before the bind
            raise TimeoutError("serve() was not listening within 30s")
        return task, ready.result()

    yield _start
    for task in tasks:
        task.cancel()
    # return_exceptions: a serve() that died before its bind already raised
    # out of _start(); re-raising it here would report the one failure twice.
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.fixture
def agent_source_files() -> list[Path]:
    """Every hand-written ``.py`` file in the agent package's source tree.

    Shared by the two AST-scanning suites — ``test_ml_bridge_import_hygiene``
    and ``test_path_discipline`` — which walk the same file set but apply
    genuinely different traversals to it (module-scope-only vs whole-tree),
    so only the enumeration is common. Keeping the ``_generated`` exclusion in
    one place means adding or renaming a generated directory cannot leave one
    scanner walking files it should skip.

    A fixture rather than an importable helper for the same reason as
    :func:`bump_mtime_forward` below: ``from conftest import ...`` only
    resolves under pytest's default ``prepend`` import mode.
    """
    return _agent_source_files()


def _agent_source_files() -> list[Path]:
    src_dir = Path(__file__).parents[1] / "src" / "grackle"
    return [p for p in sorted(src_dir.rglob("*.py")) if "_generated" not in p.parts]


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
