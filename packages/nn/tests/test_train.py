import numpy as np

from grackle_nn.data import make_spirals
from grackle_nn.layers import Linear, ReLU
from grackle_nn.losses import SoftmaxCrossEntropy
from grackle_nn.model import Sequential
from grackle_nn.optim import SGD
from grackle_nn.train import fit


def _make_model(seed: int) -> Sequential:
    rng = np.random.default_rng(seed)
    return Sequential(
        Linear(2, 16, rng=rng),
        ReLU(),
        Linear(16, 16, rng=rng),
        ReLU(),
        Linear(16, 3, rng=rng),
    )


def test_fit_history_length_and_types() -> None:
    x, y = make_spirals(n_per_class=16, rng=np.random.default_rng(0))
    model = _make_model(1)
    optimizer = SGD(model.parameters(), model.gradients(), lr=0.1, momentum=0.9)

    history = fit(
        model,
        x,
        y,
        loss_fn=SoftmaxCrossEntropy(),
        optimizer=optimizer,
        epochs=3,
        batch_size=16,
        rng=np.random.default_rng(2),
    )

    assert len(history) == 3
    for i, entry in enumerate(history):
        assert entry[0] == i
        assert type(entry[0]) is int
        assert type(entry[1]) is float
        assert type(entry[2]) is float


def test_fit_loss_decreases_over_15_epochs() -> None:
    x, y = make_spirals(n_per_class=32, rng=np.random.default_rng(10))
    model = _make_model(11)
    optimizer = SGD(model.parameters(), model.gradients(), lr=0.5, momentum=0.9)

    history = fit(
        model,
        x,
        y,
        loss_fn=SoftmaxCrossEntropy(),
        optimizer=optimizer,
        epochs=15,
        batch_size=32,
        rng=np.random.default_rng(12),
    )

    first_loss = history[0][1]
    last_loss = history[-1][1]
    assert last_loss < first_loss


def test_fit_is_deterministic_given_same_seeds() -> None:
    x, y = make_spirals(n_per_class=16, rng=np.random.default_rng(20))

    def run() -> list[tuple[int, float, float]]:
        model = _make_model(21)
        optimizer = SGD(model.parameters(), model.gradients(), lr=0.2, momentum=0.9)
        return fit(
            model,
            x,
            y,
            loss_fn=SoftmaxCrossEntropy(),
            optimizer=optimizer,
            epochs=5,
            batch_size=16,
            rng=np.random.default_rng(22),
        )

    history_a = run()
    history_b = run()
    assert history_a == history_b
