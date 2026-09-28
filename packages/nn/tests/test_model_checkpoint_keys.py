"""The exact key set of a ``Sequential.save`` checkpoint (campaign T9-6).

This is the writer the ``allow_pickle`` comment in ``grackle_nn/model.py``
is about: on numpy 2.0/2.1, which ``numpy>=2,<3`` admits, passing
``allow_pickle=`` to ``np.savez`` stored it as a stray bool array in every
checkpoint. ``Sequential.load`` checks only that ``p0..pN`` are present and
ignores anything else, so the round-trip tests in ``test_model.py`` would stay
green with a stray key in every file. Only the exact set catches it. The heat
model's checkpoint has the same pin in ``tests/ml/test_checkpoint_pins.py``.
"""

from __future__ import annotations

import zipfile
from typing import TYPE_CHECKING

import numpy as np

from grackle_nn.layers import Linear, ReLU, Tanh
from grackle_nn.model import Sequential

if TYPE_CHECKING:
    from pathlib import Path


def test_sequential_checkpoint_holds_exactly_one_array_per_parameter(tmp_path: Path) -> None:
    rng = np.random.default_rng(0)
    model = Sequential(
        Linear(2, 4, rng=rng), ReLU(), Linear(4, 4, rng=rng), Tanh(), Linear(4, 3, rng=rng)
    )
    path = tmp_path / "model.npz"
    model.save(path)

    expected = [f"p{i}" for i in range(6)]  # W, b for each of the three Linear layers
    with zipfile.ZipFile(path) as zf:
        assert sorted(zf.namelist()) == sorted(f"{k}.npy" for k in expected)
    with np.load(path, allow_pickle=False) as npz:
        assert sorted(npz.files) == sorted(expected)
        for key, param in zip(expected, model.parameters(), strict=True):
            assert npz[key].dtype == np.float64
            assert npz[key].shape == param.shape
