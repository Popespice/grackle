import numpy as np

from grackle_nn._types import Array, IntArray
from grackle_nn.losses import ClassificationLoss
from grackle_nn.metrics import accuracy, record_epoch, record_layer_stats
from grackle_nn.model import Sequential
from grackle_nn.optim import Optimizer

type EpochStats = tuple[int, float, float]


def train_step(
    model: Sequential,
    loss_fn: ClassificationLoss,
    optimizer: Optimizer,
    xb: Array,
    yb: IntArray,
) -> float:
    logits = model.forward(xb)
    loss = loss_fn.forward(logits, yb)
    grad = loss_fn.backward()
    model.backward(grad)
    optimizer.step()
    model.zero_grad()
    return loss


def evaluate(
    model: Sequential, loss_fn: ClassificationLoss, x: Array, y: IntArray
) -> tuple[float, float]:
    logits = model.forward(x)
    loss = loss_fn.forward(logits, y)
    acc = accuracy(logits, y)
    return loss, acc


def fit(
    model: Sequential,
    x: Array,
    y: IntArray,
    *,
    loss_fn: ClassificationLoss,
    optimizer: Optimizer,
    epochs: int,
    batch_size: int,
    rng: np.random.Generator,
) -> list[EpochStats]:
    history: list[EpochStats] = []
    n = x.shape[0]
    # Snapshot each param-carrying layer's weight matrix (copies, never live
    # refs — the arrays mutate in place during training) so the per-epoch
    # weight-change RMS can be measured against the previous epoch. Iterate
    # model.layers inline, never model.parameters(): a parameters() call is a
    # traced project function (+2 events) that would shift the one-time event
    # constant C the sizing formula and golden trace depend on.
    prev_weights = [layer.params[0].copy() for layer in model.layers if layer.params]
    for epoch in range(epochs):
        perm = rng.permutation(n)
        x_shuffled = x[perm]
        y_shuffled = y[perm]
        for start in range(0, n, batch_size):
            end = start + batch_size
            train_step(model, loss_fn, optimizer, x_shuffled[start:end], y_shuffled[start:end])
        loss, acc = evaluate(model, loss_fn, x, y)
        epoch_stats = record_epoch(epoch, loss, acc)
        # Per-layer weight magnitude and per-epoch weight change, computed inline
        # with numpy only. The numpy frames are DISABLE'd by the tracer (they are
        # not project code), and float()/f-string formatting are C-level, so this
        # whole block adds no trace events — only record_layer_stats itself emits
        # (+2 per epoch). Rounding to 3 significant figures on the caller keeps
        # the beacon's captured return repr compact, stable across tiny BLAS
        # drift, and a flat parse contract; record_layer_stats stays a pure
        # identity passthrough.
        weights = [layer.params[0] for layer in model.layers if layer.params]
        stats: list[float] = []
        for prev_w, w in zip(prev_weights, weights, strict=True):
            delta = w - prev_w
            w_rms = float(np.sqrt(np.mean(w * w)))
            dw_rms = float(np.sqrt(np.mean(delta * delta)))
            stats.append(float(f"{w_rms:.3g}"))
            stats.append(float(f"{dw_rms:.3g}"))
        prev_weights = [w.copy() for w in weights]
        record_layer_stats(epoch, tuple(stats))
        history.append(epoch_stats)
    return history
