import numpy as np

from grackle_nn._types import Array, IntArray


def make_spirals(
    n_per_class: int = 128, *, classes: int = 3, noise: float = 0.15, rng: np.random.Generator
) -> tuple[Array, IntArray]:
    t = np.linspace(0.0, 1.0, n_per_class, dtype=np.float64)
    xs = []
    ys = []
    for k in range(classes):
        r = t
        theta = (2.0 * np.pi * k) / classes + 4.0 * t + rng.standard_normal(n_per_class) * noise
        xs.append(np.stack((r * np.sin(theta), r * np.cos(theta)), axis=1))
        ys.append(np.full(n_per_class, k, dtype=np.int64))
    return np.concatenate(xs), np.concatenate(ys)
