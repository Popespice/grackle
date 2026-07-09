from pathlib import Path

import numpy as np

from grackle_nn._types import Array
from grackle_nn.layers import Layer


class Sequential:
    def __init__(self, *layers: Layer) -> None:
        self.layers: list[Layer] = list(layers)

    def forward(self, x: Array) -> Array:
        for layer in self.layers:
            x = layer.forward(x)
        return x

    def backward(self, grad: Array) -> Array:
        for layer in reversed(self.layers):
            grad = layer.backward(grad)
        return grad

    def parameters(self) -> list[Array]:
        return [p for layer in self.layers for p in layer.params]

    def gradients(self) -> list[Array]:
        return [g for layer in self.layers for g in layer.grads]

    def zero_grad(self) -> None:
        for layer in self.layers:
            for g in layer.grads:
                g[...] = 0.0

    def save(self, path: Path) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        params = {f"p{i}": p for i, p in enumerate(self.parameters())}
        with tmp.open("wb") as fh:
            # allow_pickle is savez's own default; pinned explicitly so mypy resolves
            # the **params splat against **kwds: ArrayLike instead of the allow_pickle:
            # bool keyword it would otherwise (incorrectly) also check the splat against.
            np.savez(fh, allow_pickle=True, **params)
        tmp.replace(path)

    def load(self, path: Path) -> None:
        params = self.parameters()
        keys = [f"p{i}" for i in range(len(params))]
        with np.load(path) as npz:
            for key in keys:
                if key not in npz:
                    raise ValueError(f"checkpoint missing key {key!r}")
            for key, p in zip(keys, params, strict=True):
                loaded: Array = npz[key]
                if loaded.shape != p.shape:
                    raise ValueError(
                        f"shape mismatch for {key!r}: expected {p.shape}, got {loaded.shape}"
                    )
            for key, p in zip(keys, params, strict=True):
                arr: Array = npz[key]
                p[...] = arr
