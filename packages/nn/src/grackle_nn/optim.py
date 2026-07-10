from typing import Protocol

import numpy as np

from grackle_nn._types import Array


class Optimizer(Protocol):
    def step(self) -> None: ...


class SGD:
    def __init__(
        self,
        params: list[Array],
        grads: list[Array],
        *,
        lr: float,
        momentum: float = 0.0,
    ) -> None:
        self.params = params
        self.grads = grads
        self.lr = lr
        self.momentum = momentum
        self.velocities: list[Array] = [np.zeros_like(p) for p in params]

    def step(self) -> None:
        for p, g, v in zip(self.params, self.grads, self.velocities, strict=True):
            v *= self.momentum
            v += g
            p -= self.lr * v


class Adam:
    def __init__(
        self,
        params: list[Array],
        grads: list[Array],
        *,
        lr: float = 1e-3,
        beta1: float = 0.9,
        beta2: float = 0.999,
        eps: float = 1e-8,
    ) -> None:
        self.params = params
        self.grads = grads
        self.lr = lr
        self.beta1 = beta1
        self.beta2 = beta2
        self.eps = eps
        self.t = 0
        self.m: list[Array] = [np.zeros_like(p) for p in params]
        self.v: list[Array] = [np.zeros_like(p) for p in params]

    def step(self) -> None:
        self.t += 1
        for p, g, m, v in zip(self.params, self.grads, self.m, self.v, strict=True):
            m *= self.beta1
            m += (1.0 - self.beta1) * g
            v *= self.beta2
            v += (1.0 - self.beta2) * g**2
            m_hat = m / (1.0 - self.beta1**self.t)
            v_hat = v / (1.0 - self.beta2**self.t)
            p -= self.lr * m_hat / (np.sqrt(v_hat) + self.eps)
