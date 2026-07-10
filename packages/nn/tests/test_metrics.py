import ast

import numpy as np

from grackle_nn.metrics import accuracy, record_epoch


def test_accuracy_known_value() -> None:
    logits = np.array([[2.0, 1.0], [0.0, 3.0], [5.0, 0.0], [1.0, 2.0]])
    labels = np.array([0, 1, 1, 1])

    acc = accuracy(logits, labels)

    assert acc == 0.75
    assert type(acc) is float


def test_record_epoch_is_identity_and_builtin_typed() -> None:
    result = record_epoch(12, 0.4321098765, 0.8697916667)

    assert result == (12, 0.4321098765, 0.8697916667)
    assert type(result[0]) is int
    assert type(result[1]) is float
    assert type(result[2]) is float
    assert ast.literal_eval(repr(result)) == result
