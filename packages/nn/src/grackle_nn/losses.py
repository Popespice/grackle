from typing import Protocol

import numpy as np

from ._types import Array, IntArray


class ClassificationLoss(Protocol):
    def forward(self, logits: Array, labels: IntArray) -> float: ...
    def backward(self) -> Array: ...


class SoftmaxCrossEntropy:
    def __init__(self) -> None:
        self._probs: Array | None = None
        self._labels: IntArray | None = None

    def forward(self, logits: Array, labels: IntArray) -> float:
        B = logits.shape[0]
        # Subtract the row max before exponentiating: the largest entry per row
        # becomes exactly 0, so exp(shifted) is always in (0, 1] and never
        # overflows, even at |logits| ~ 1e4.
        shifted = logits - logits.max(axis=1, keepdims=True)
        log_probs = shifted - np.log(np.exp(shifted).sum(axis=1, keepdims=True))
        self._probs = np.exp(log_probs)
        self._labels = labels
        return float((-log_probs[np.arange(B), labels]).mean())

    def backward(self) -> Array:
        assert self._probs is not None
        assert self._labels is not None
        B: int = self._probs.shape[0]
        # .copy(): mutating grad below must not corrupt the cached probs, which
        # could conceptually still be read again.
        grad = self._probs.copy()
        grad[np.arange(B), self._labels] -= 1.0
        return grad / B


class MSE:
    def __init__(self) -> None:
        self._diff: Array | None = None

    def forward(self, pred: Array, target: Array) -> float:
        self._diff = pred - target
        return float(np.mean(self._diff**2))

    def backward(self) -> Array:
        assert self._diff is not None
        # Normalize by total element count (.size), not batch size: this makes
        # the analytic gradient exactly match the central-difference numerical
        # gradient of the scalar mean returned by forward().
        return 2.0 * self._diff / self._diff.size
