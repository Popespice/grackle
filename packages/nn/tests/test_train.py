import numpy as np
import pytest

from grackle_nn.data import make_spirals
from grackle_nn.demo import main
from grackle_nn.layers import Linear, ReLU
from grackle_nn.losses import SoftmaxCrossEntropy
from grackle_nn.model import Sequential
from grackle_nn.optim import SGD
from grackle_nn.train import fit

_ENV_VARS = ("NN_DEMO_LR", "NN_DEMO_EPOCHS", "NN_DEMO_SEED")


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


def test_demo_main_defaults_reach_target_accuracy(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    history = main()

    assert len(history) == 60
    assert history[-1][2] >= 0.95


def test_demo_main_env_overrides_are_honored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NN_DEMO_EPOCHS", "3")
    monkeypatch.setenv("NN_DEMO_LR", "0.05")
    monkeypatch.setenv("NN_DEMO_SEED", "1")
    history_seed1 = main()
    assert len(history_seed1) == 3

    monkeypatch.setenv("NN_DEMO_SEED", "0")
    history_seed0 = main()
    assert len(history_seed0) == 3

    assert history_seed1 != history_seed0
