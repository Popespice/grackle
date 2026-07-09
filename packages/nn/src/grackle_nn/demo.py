import os

import numpy as np

from grackle_nn.data import make_spirals
from grackle_nn.layers import Linear, ReLU
from grackle_nn.losses import SoftmaxCrossEntropy
from grackle_nn.model import Sequential
from grackle_nn.optim import SGD
from grackle_nn.train import EpochStats, fit


def main() -> list[EpochStats]:
    lr = float(os.environ.get("NN_DEMO_LR", "0.3"))
    epochs = int(os.environ.get("NN_DEMO_EPOCHS", "60"))
    seed = int(os.environ.get("NN_DEMO_SEED", "0"))

    rng = np.random.default_rng(seed)
    x, y = make_spirals(rng=rng)
    model = Sequential(
        Linear(2, 32, rng=rng),
        ReLU(),
        Linear(32, 32, rng=rng),
        ReLU(),
        Linear(32, 3, rng=rng),
    )
    loss_fn = SoftmaxCrossEntropy()
    # momentum=0.9 at the default lr=0.3 diverges after ~epoch 40 (verified empirically:
    # final accuracy ~0.6, not the >=0.95 this demo targets) -- effective step size
    # lr/(1-momentum) gets too large for this depth/width once the loss is already low.
    optimizer = SGD(model.parameters(), model.gradients(), lr=lr, momentum=0.5)

    history = fit(
        model,
        x,
        y,
        loss_fn=loss_fn,
        optimizer=optimizer,
        epochs=epochs,
        batch_size=32,
        rng=rng,
    )

    for epoch, loss, acc in history:
        print(f"epoch {epoch:3d}  loss={loss:.4f}  acc={acc:.4f}")

    return history


if __name__ == "__main__":
    main()
