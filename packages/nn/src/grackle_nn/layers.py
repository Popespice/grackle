import math
from typing import Literal, Protocol

import numpy as np
import numpy.typing as npt

from grackle_nn._types import Array

# forward()/backward() bodies below call only numpy operators/functions, never a
# project-local function or method: grackle's tracer emits one call/return event per
# Python-level invocation, so any helper call here would leak extra events into every
# golden trace.


class Layer(Protocol):
    params: list[Array]
    grads: list[Array]

    def forward(self, x: Array) -> Array: ...
    def backward(self, grad: Array) -> Array: ...


class Linear:
    def __init__(
        self,
        in_features: int,
        out_features: int,
        *,
        rng: np.random.Generator,
        init: Literal["he", "xavier"] = "he",
    ) -> None:
        if init == "he":
            W = rng.standard_normal((in_features, out_features)) * math.sqrt(2.0 / in_features)
        else:
            a = math.sqrt(6.0 / (in_features + out_features))
            W = rng.uniform(-a, a, (in_features, out_features))
        b = np.zeros(out_features)
        self.params: list[Array] = [W, b]
        self.grads: list[Array] = [np.zeros_like(W), np.zeros_like(b)]
        self._x: Array | None = None

    def forward(self, x: Array) -> Array:
        self._x = x
        W, b = self.params
        return x @ W + b

    def backward(self, grad: Array) -> Array:
        if self._x is None:
            raise RuntimeError("backward called before forward")
        W, _ = self.params
        self.grads[0] += self._x.T @ grad
        self.grads[1] += grad.sum(axis=0)
        return grad @ W.T


class ReLU:
    def __init__(self) -> None:
        self.params: list[Array] = []
        self.grads: list[Array] = []
        self._mask: npt.NDArray[np.bool_] | None = None

    def forward(self, x: Array) -> Array:
        self._mask = x > 0.0
        return x * self._mask

    def backward(self, grad: Array) -> Array:
        if self._mask is None:
            raise RuntimeError("backward called before forward")
        return grad * self._mask


class Tanh:
    def __init__(self) -> None:
        self.params: list[Array] = []
        self.grads: list[Array] = []
        self._t: Array | None = None

    def forward(self, x: Array) -> Array:
        self._t = np.tanh(x)
        return self._t

    def backward(self, grad: Array) -> Array:
        if self._t is None:
            raise RuntimeError("backward called before forward")
        return grad * (1 - self._t**2)
