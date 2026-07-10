from pathlib import Path

import numpy as np

from grackle_nn._types import Array
from grackle_nn.layers import Layer


class Sequential:
    def __init__(self, *layers: Layer) -> None:
        if len({id(layer) for layer in layers}) != len(layers):
            raise ValueError(
                "each layer instance may appear only once (layers cache forward state)"
            )
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
            # allow_pickle only became a real savez keyword in numpy 2.2; pyproject
            # allows numpy>=2,<3, so passing it explicitly would silently write a
            # stray "allow_pickle" array into the checkpoint on numpy 2.0/2.1.
            np.savez(fh, **params)  # type: ignore[arg-type]
        tmp.replace(path)

    def load(self, path: Path) -> None:
        params = self.parameters()
        keys = [f"p{i}" for i in range(len(params))]
        with np.load(path) as npz:
            for key in keys:
                if key not in npz:
                    raise ValueError(f"checkpoint missing key {key!r}")
            loaded = [(p, npz[key]) for key, p in zip(keys, params, strict=True)]
        for key, (p, arr) in zip(keys, loaded, strict=True):
            if arr.shape != p.shape:
                raise ValueError(f"shape mismatch for {key!r}: expected {p.shape}, got {arr.shape}")
        for p, arr in loaded:
            p[...] = arr
