import numpy as np

from grackle_nn._types import Array, IntArray
from grackle_nn.losses import ClassificationLoss
from grackle_nn.metrics import accuracy, record_epoch
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
    for epoch in range(epochs):
        perm = rng.permutation(n)
        x_shuffled = x[perm]
        y_shuffled = y[perm]
        for start in range(0, n, batch_size):
            end = start + batch_size
            train_step(model, loss_fn, optimizer, x_shuffled[start:end], y_shuffled[start:end])
        loss, acc = evaluate(model, loss_fn, x, y)
        history.append(record_epoch(epoch, loss, acc))
    return history
