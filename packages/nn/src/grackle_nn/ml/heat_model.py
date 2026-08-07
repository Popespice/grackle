"""The hotspot-prediction model: architecture, training loop, and artifact I/O.

D12.3: the Phase-11 MLP verbatim (``Linear(35,64), ReLU, Linear(64,32), ReLU,
Linear(32,1)``), trained here (not ``grackle_nn.train.fit``, which is
classification-shaped) with a single MSE regression head. The training loop
calls the 11.x ``record_epoch`` beacon each epoch so a traced ``grackle
learn`` run lights up the LossCurvePanel. This module never imports
``grackle`` at runtime, not even under ``TYPE_CHECKING``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from grackle_nn.layers import Linear, ReLU
from grackle_nn.losses import MSE
from grackle_nn.metrics import record_epoch
from grackle_nn.ml.dataset import stack
from grackle_nn.ml.features import FEATURE_NAMES, FEATURE_VERSION
from grackle_nn.model import Sequential
from grackle_nn.optim import Adam

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from grackle_nn._types import Array
    from grackle_nn.ml.dataset import Example
    from grackle_nn.train import EpochStats

_FEATURE_WIDTH = len(FEATURE_NAMES)
_HIDDEN = (64, 32)
_REQUIRED_KEYS = (
    "feature_version",
    "hidden",
    "norm_mean",
    "norm_std",
    "w0",
    "b0",
    "w1",
    "b1",
    "w2",
    "b2",
)
_EXPECTED_PARAM_SHAPES = {
    "w0": (_FEATURE_WIDTH, _HIDDEN[0]),
    "b0": (_HIDDEN[0],),
    "w1": (_HIDDEN[0], _HIDDEN[1]),
    "b1": (_HIDDEN[1],),
    "w2": (_HIDDEN[1], 1),
    "b2": (1,),
}


class ModelFormatError(Exception):
    """A ``heat-model.npz`` artifact is missing keys, has wrong shapes, or was
    saved under a different ``FEATURE_VERSION``."""

    def __init__(self, message: str, *, expected_version: int, found_version: int) -> None:
        super().__init__(message)
        self.expected_version = expected_version
        self.found_version = found_version


def _build_architecture(rng: np.random.Generator) -> Sequential:
    return Sequential(
        Linear(_FEATURE_WIDTH, _HIDDEN[0], rng=rng),
        ReLU(),
        Linear(_HIDDEN[0], _HIDDEN[1], rng=rng),
        ReLU(),
        Linear(_HIDDEN[1], 1, rng=rng),
    )


class HeatModel:
    """A trained hotspot-prediction model: the MLP plus its standardization stats."""

    def __init__(self, model: Sequential, norm_mean: Array, norm_std: Array) -> None:
        self.model = model
        self.norm_mean = norm_mean
        self.norm_std = norm_std

    def predict(self, x: Array) -> Array:
        """Standardize -> forward -> squeeze -> clip to ``[0, 1]``."""
        if x.shape[-1] != _FEATURE_WIDTH:
            raise ValueError(f"expected {_FEATURE_WIDTH} feature columns, got {x.shape[-1]}")
        standardized = (x - self.norm_mean) / self.norm_std
        logits = self.model.forward(standardized)
        squeezed = logits.squeeze(axis=1)
        result: Array = np.clip(squeezed, 0.0, 1.0)
        return result

    def save(self, path: Path) -> None:
        """Atomic save (D12.6): write to a sibling ``.tmp``, then ``Path.replace``."""
        tmp = path.with_suffix(path.suffix + ".tmp")
        w0, b0, w1, b1, w2, b2 = self.model.parameters()
        with tmp.open("wb") as fh:
            np.savez(
                fh,
                feature_version=FEATURE_VERSION,
                hidden=np.array(_HIDDEN, dtype=np.int64),
                norm_mean=self.norm_mean,
                norm_std=self.norm_std,
                w0=w0,
                b0=b0,
                w1=w1,
                b1=b1,
                w2=w2,
                b2=b2,
            )
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path) -> HeatModel:
        """Two-pass load: validate every key/version/shape, only then construct."""
        with np.load(path) as npz:
            # feature_version is read before the missing-key check (if present)
            # so a ModelFormatError raised for some OTHER missing key still
            # reports the checkpoint's real found_version, not an unknown -1.
            found_version = int(npz["feature_version"]) if "feature_version" in npz else -1

            missing = [key for key in _REQUIRED_KEYS if key not in npz]
            if missing:
                raise ModelFormatError(
                    f"checkpoint {path} missing key(s): {missing}",
                    expected_version=FEATURE_VERSION,
                    found_version=found_version,
                )
            if found_version != FEATURE_VERSION:
                raise ModelFormatError(
                    f"checkpoint {path} was saved with feature_version={found_version}, "
                    f"expected {FEATURE_VERSION}",
                    expected_version=FEATURE_VERSION,
                    found_version=found_version,
                )
            hidden = tuple(int(v) for v in np.asarray(npz["hidden"]).tolist())
            if hidden != _HIDDEN:
                raise ModelFormatError(
                    f"checkpoint {path} hidden={hidden}, expected {_HIDDEN} "
                    "(the architecture is fixed, D12.3)",
                    expected_version=FEATURE_VERSION,
                    found_version=found_version,
                )
            norm_mean = np.array(npz["norm_mean"], dtype=np.float64)
            norm_std = np.array(npz["norm_std"], dtype=np.float64)
            if norm_mean.shape != (_FEATURE_WIDTH,) or norm_std.shape != (_FEATURE_WIDTH,):
                raise ModelFormatError(
                    f"checkpoint {path} norm_mean/norm_std must have shape "
                    f"({_FEATURE_WIDTH},), got {norm_mean.shape}/{norm_std.shape}",
                    expected_version=FEATURE_VERSION,
                    found_version=found_version,
                )
            params: dict[str, Array] = {}
            for key, expected_shape in _EXPECTED_PARAM_SHAPES.items():
                arr = np.array(npz[key], dtype=np.float64)
                if arr.shape != expected_shape:
                    raise ModelFormatError(
                        f"checkpoint {path} key {key!r} has shape {arr.shape}, "
                        f"expected {expected_shape}",
                        expected_version=FEATURE_VERSION,
                        found_version=found_version,
                    )
                params[key] = arr

        model = _build_architecture(np.random.default_rng(0))
        for p, key in zip(model.parameters(), _EXPECTED_PARAM_SHAPES, strict=True):
            p[...] = params[key]
        return cls(model, norm_mean, norm_std)


def train_heat_model(
    train: Sequence[Example],
    *,
    epochs: int = 200,
    batch_size: int = 64,
    lr: float = 1e-3,
    seed: int = 0,
    val: Sequence[Example] | None = None,
) -> tuple[HeatModel, list[EpochStats]]:
    """Train a :class:`HeatModel` on pooled examples (D12.3).

    A single ``default_rng(seed)`` is threaded into all three ``Linear``
    layers (init order) and every epoch's minibatch shuffle, so a run is
    fully reproducible from ``seed`` alone (the D10 discipline). Calls
    ``record_epoch(epoch, train_loss, val_loss_or_train_loss)`` once per
    epoch as a trace beacon.
    """
    x, y = stack(train)
    norm_mean: Array = x.mean(axis=0)
    norm_std: Array = np.maximum(x.std(axis=0), 1e-8)
    x_std = (x - norm_mean) / norm_std
    y_col = y.reshape(-1, 1)

    if val is not None:
        val_x, val_y = stack(val)
        val_x_std = (val_x - norm_mean) / norm_std
        val_y_col = val_y.reshape(-1, 1)
    else:
        val_x_std = None
        val_y_col = None

    rng = np.random.default_rng(seed)
    model = _build_architecture(rng)
    loss_fn = MSE()
    optimizer = Adam(model.parameters(), model.gradients(), lr=lr)

    n = x_std.shape[0]
    history: list[EpochStats] = []
    for epoch in range(epochs):
        perm = rng.permutation(n)
        x_shuffled = x_std[perm]
        y_shuffled = y_col[perm]
        for start in range(0, n, batch_size):
            end = start + batch_size
            pred = model.forward(x_shuffled[start:end])
            loss_fn.forward(pred, y_shuffled[start:end])
            grad = loss_fn.backward()
            model.backward(grad)
            optimizer.step()
            model.zero_grad()

        train_pred = model.forward(x_std)
        train_loss = loss_fn.forward(train_pred, y_col)
        if val_x_std is not None and val_y_col is not None:
            val_pred = model.forward(val_x_std)
            val_loss = loss_fn.forward(val_pred, val_y_col)
        else:
            val_loss = train_loss
        epoch_stats = record_epoch(epoch, float(train_loss), float(val_loss))
        history.append(epoch_stats)

    return HeatModel(model, norm_mean, norm_std), history
