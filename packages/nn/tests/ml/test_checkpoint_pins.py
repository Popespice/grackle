"""Checkpoint pins for ``heat-model.npz``: the key set (T9-6) and a reload golden (T10-2).

Campaign ``docs/test-campaigns/phase-12.md``.

**T9-6, the key set.** ``np.savez`` stores every keyword argument as an array.
``allow_pickle`` only became a real ``savez`` keyword in numpy 2.2, so on
numpy 2.0/2.1 (which ``numpy>=2,<3`` still admits) passing it wrote a stray
bool array into every checkpoint (see the comment in ``grackle_nn/model.py``).
``HeatModel.load`` and the agent's reader (``grackle.ml_bridge.predict_scores``,
which calls it) look up the keys they need and ignore everything else, so a
round-trip test cannot see a stray key. Only asserting the exact set can. The
set is checked twice: through numpy (``NpzFile.files``) and through the ZIP
directory itself, which does not depend on numpy's reader.

**T10-2, reload equivalence across platforms.** A ZIP embeds mtimes, so the
checkpoint's bytes can never be pinned. What can be pinned is behavior: a
fixed-seed model, trained, saved, reloaded and asked to predict a fixed input,
must produce the committed golden vector on every OS in the CI matrix. The
golden was generated on macOS/arm64 (numpy 2.5, Accelerate BLAS); Linux and
Windows run OpenBLAS and their own libm, so they reach it through different
summation orders and differently-rounded ``log1p``/``pow``.

The tolerance, ``atol=1e-10``, was chosen from measurements on this exact
configuration (macOS; scripted perturbations, 40 seeds each):

- A different BLAS summation order, emulated by moving every ``Linear``
  matmul output and gradient by up to ``sqrt(K) * eps * sum(|terms|)``, moved
  the golden rows by at most 7e-15 (6e-14 at ten times that noise). Measured
  before the T9-8 fix, which does not touch any summation order.
- A different libm, emulated by shifting each distinct feature and target
  value by up to 2 ulps, moves them by up to 4.4e-15 (5.9e-15 at 8 ulps).
  Before T9-8's fix this was 4.3e-8 (1.6e-7 at 8 ulps) and set the band at
  1e-6: nearly all of it came through a column that is constant in training
  (``log1p_path_depth = log1p(2)`` on every row), whose mean is one ulp off
  its value, so a computed ``std`` of 2.2e-16 was floored to 1e-8 and that
  residual reached the network as an input of about 2e-8. Such a column now
  gets scale 1.0 (``test_standardization_envelope.py``), and the residual stays
  at 1e-16.
- Real changes to the training configuration moved the golden rows by 0.043
  (one epoch fewer) to 0.53 (seed + 1): ``epochs`` +/- 1, ``batch_size``
  64 -> 32, ``lr`` 1e-3 -> 1.1e-3. The companion test below re-proves this on
  every run. Adam's ``eps`` 1e-8 -> 1e-7 moved them by 1.5e-6 under the old
  band, which sat at its edge; it is now caught (15000x the band).

So the band is 1600x the worst BLAS drift at ten times the emulated noise,
and 17000x the worst libm drift at 8 ulps, and more than eight orders of
magnitude below the smallest real change. The wire rounds ``predicted_heat``
to 4 decimals, so 1e-10 is far finer than anything a user can see. The repo's
one known cross-platform surprise in this pipeline was a single-ulp ``log1p``
disagreement (``test_labels.py``,
``test_make_targets_hottest_node_is_exactly_one``). Verified on macOS only;
the Ubuntu and Windows legs are the first real cross-OS check.

When a deliberate change to training moves the golden, regenerate it (print
``_reloaded_predictions(tmp_path)[1][list(_GOLDEN_ROWS)]`` with ``repr``) and
keep the companion test green; it is what shows the band still discriminates.
"""

from __future__ import annotations

import zipfile
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest
from numpy.testing import assert_allclose, assert_array_equal

from grackle_nn.ml.dataset import build_example
from grackle_nn.ml.features import FEATURE_NAMES, FEATURE_VERSION
from grackle_nn.ml.heat_model import HeatModel, train_heat_model

if TYPE_CHECKING:
    from pathlib import Path

    from grackle_nn._types import Array
    from grackle_nn.ml.dataset import Example

_WIDTH = len(FEATURE_NAMES)

# The checkpoint's complete contents, name -> (dtype, shape). Adding or
# removing a key is a format change: update this table, and FEATURE_VERSION
# or the load-side validation, deliberately.
_HEAT_MODEL_LAYOUT: dict[str, tuple[str, tuple[int, ...]]] = {
    "feature_version": ("int64", ()),
    "hidden": ("int64", (2,)),
    "norm_mean": ("float64", (_WIDTH,)),
    "norm_std": ("float64", (_WIDTH,)),
    "w0": ("float64", (_WIDTH, 64)),
    "b0": ("float64", (64,)),
    "w1": ("float64", (64, 32)),
    "b1": ("float64", (32,)),
    "w2": ("float64", (32, 1)),
    "b2": ("float64", (1,)),
}


# --- A tiny, rng-free fixture ---------------------------------------------------
#
# Built by hand rather than from tests/ml/synth.py, so the golden depends only
# on production code: a change to the acceptance corpus cannot move it.


def _fixture_graph(n: int, offset: int) -> tuple[dict[str, Any], dict[str, int]]:
    """A deterministic call/import graph of *n* functions, and a heat map for it."""
    files = [f"pkg/mod{i}.py" for i in range(3)]
    nodes: list[dict[str, Any]] = [
        {"id": f, "kind": "file", "name": f.rsplit("/", 1)[1], "path": f} for f in files
    ]
    ids = []
    for i in range(n):
        path = files[i % 3]
        name = f"{'test_' if i % 7 == 3 else ''}{'_' if i % 11 == 5 else ''}fn{i}"
        kind = "method" if i % 3 == 1 else "function"
        nodes.append(
            {"id": f"{path}:{name}", "kind": kind, "name": name, "path": path, "line": 1 + 4 * i}
        )
        ids.append(f"{path}:{name}")

    edges: list[dict[str, Any]] = []
    heat = dict.fromkeys(ids, 0)
    for i in range(1, n):
        target = (i * 37 + offset) % i
        edges.append({"source": ids[i], "target": ids[target], "kind": "call"})
        heat[ids[target]] += 1 + i % 5
        if i % 4 == 0:
            edges.append({"source": ids[i], "target": ids[(i * 11 + offset) % i], "kind": "import"})
    for a, b in ((5, 9), (9, 13), (13, 5)):  # one planted cycle
        edges.append({"source": ids[a], "target": ids[b], "kind": "call"})
        heat[ids[b]] += 7
    for i in range(0, n, 5):
        edges.append({"source": files[i % 3], "target": ids[i], "kind": "import"})
    graph = {"version": 1, "language": "python", "nodes": nodes, "edges": edges}
    return graph, {k: v for k, v in heat.items() if v > 0}


def _fixture_examples() -> tuple[list[Example], Example]:
    train = [
        build_example("a", *_fixture_graph(40, 3)),
        build_example("b", *_fixture_graph(48, 5)),
    ]
    probe = build_example("probe", *_fixture_graph(32, 7))
    return train, probe


_GOLDEN_SEED = 5
_GOLDEN_EPOCHS = 30
# Probe rows whose predictions sit strictly inside (0, 1): a clipped 0.0 or 1.0
# is insensitive to drift and would make the comparison weaker than it looks.
_GOLDEN_ROWS = (3, 4, 6, 8, 10, 14, 16, 17, 20, 26, 29, 32)
# Generated on macOS 26 / arm64, Python 3.12, numpy 2.5.1 (Accelerate BLAS).
_GOLDEN = (
    0.23125615829716398,
    0.4224271055616656,
    0.48111331814810376,
    0.5382077865990761,
    0.2608372228887015,
    0.10134943600367255,
    0.07222974224544632,
    0.0928207318921623,
    0.10310149477003952,
    0.09750452207866181,
    0.096672601699679,
    0.09393729102474108,
)
_GOLDEN_ATOL = 1e-10


def _train_golden_model(**overrides: Any) -> tuple[HeatModel, Array]:
    """Train the golden configuration (``grackle learn``'s defaults: lr, batch size)."""
    train, probe = _fixture_examples()
    kwargs: dict[str, Any] = {"epochs": _GOLDEN_EPOCHS, "seed": _GOLDEN_SEED}
    kwargs.update(overrides)
    model, _ = train_heat_model(train, **kwargs)
    return model, probe.x


def _reloaded_predictions(tmp_path: Path, **overrides: Any) -> tuple[Array, Array]:
    model, probe_x = _train_golden_model(**overrides)
    in_memory = model.predict(probe_x)
    path = tmp_path / "heat-model.npz"
    model.save(path)
    return in_memory, HeatModel.load(path).predict(probe_x)


# --- T9-6: the key set ------------------------------------------------------------


def _assert_heat_model_layout(path: Path) -> None:
    with zipfile.ZipFile(path) as zf:
        assert sorted(zf.namelist()) == sorted(f"{k}.npy" for k in _HEAT_MODEL_LAYOUT)
    # allow_pickle=False is np.load's default, and the agent's reader relies on
    # it: an object array anywhere in the file would fail to load there.
    with np.load(path, allow_pickle=False) as npz:
        assert sorted(npz.files) == sorted(_HEAT_MODEL_LAYOUT)
        for key, (dtype, shape) in _HEAT_MODEL_LAYOUT.items():
            arr = npz[key]
            assert (arr.dtype.name, arr.shape) == (dtype, shape), key
        assert int(npz["feature_version"]) == FEATURE_VERSION
        assert_array_equal(npz["hidden"], [64, 32])


def test_heat_model_checkpoint_has_exactly_the_documented_keys(tmp_path: Path) -> None:
    model, _ = _train_golden_model(epochs=1)
    path = tmp_path / "heat-model.npz"
    model.save(path)
    _assert_heat_model_layout(path)


def test_grackle_learn_writer_path_has_exactly_the_documented_keys(tmp_path: Path) -> None:
    """The production path end to end: ``grackle learn``'s ``train_and_save``
    writes the checkpoint, and ``serve``'s ``predict_scores`` reads it back.

    ``grackle`` is importable here only as a dev dependency (ADR-0029: the
    ``grackle_nn`` package itself never imports it).
    """
    from grackle.ml_bridge import predict_scores, train_and_save

    graph, heat = _fixture_graph(40, 3)
    out = tmp_path / "models" / "heat-model.npz"
    train_and_save(graph, heat, epochs=2, seed=0, out=out)  # type: ignore[arg-type]
    _assert_heat_model_layout(out)
    payload = predict_scores(graph, out)  # type: ignore[arg-type]
    assert payload["model_version"] == FEATURE_VERSION
    assert len(payload["scores"]) == len(graph["nodes"])


# --- T10-2: reload equivalence --------------------------------------------------


def test_reload_is_bit_identical_on_this_platform(tmp_path: Path) -> None:
    # The in-process half needs no tolerance: save/load moves float64 bytes.
    in_memory, reloaded = _reloaded_predictions(tmp_path)
    assert_array_equal(reloaded, in_memory)


def test_reloaded_predictions_match_the_cross_platform_golden(tmp_path: Path) -> None:
    _, reloaded = _reloaded_predictions(tmp_path)
    got = reloaded[list(_GOLDEN_ROWS)]
    assert_allclose(got, _GOLDEN, rtol=0, atol=_GOLDEN_ATOL)


def test_golden_is_well_formed() -> None:
    # A golden made of clipped values, or of too few rows, would pass under
    # almost any regression. Guard the fixture itself.
    assert len(_GOLDEN_ROWS) == len(_GOLDEN) >= 8
    assert all(0.05 < g < 0.95 for g in _GOLDEN)
    assert len(set(_GOLDEN)) == len(_GOLDEN)


@pytest.mark.parametrize(
    "overrides",
    [
        {"seed": _GOLDEN_SEED + 1},
        {"epochs": _GOLDEN_EPOCHS - 1},
        {"epochs": _GOLDEN_EPOCHS + 1},
        {"batch_size": 32},
        {"lr": 1.1e-3},
    ],
    ids=["seed+1", "epochs-1", "epochs+1", "batch_size=32", "lr*1.1"],
)
def test_golden_tolerance_still_catches_a_small_real_change(
    tmp_path: Path, overrides: dict[str, Any]
) -> None:
    """Discriminating-power companion: each small, real change to the training
    configuration moves the golden rows by far more than the tolerance, so
    the band cannot have been widened into vacuity."""
    _, reloaded = _reloaded_predictions(tmp_path, **overrides)
    drift = float(np.max(np.abs(reloaded[list(_GOLDEN_ROWS)] - np.array(_GOLDEN))))
    assert drift > 1000 * _GOLDEN_ATOL, drift
