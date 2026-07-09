from grackle_nn._types import Array, IntArray


def accuracy(logits: Array, labels: IntArray) -> float:
    return float((logits.argmax(axis=1) == labels).mean())


def record_epoch(epoch: int, loss: float, accuracy: float) -> tuple[int, float, float]:
    """Identity passthrough — do not inline or remove.

    This is a deliberate identity function, not dead code. It exists solely as a
    per-epoch trace beacon: grackle's code tracer captures this function's call
    arguments and return value on every invocation during training, and the
    time-travel debugging UI (and, later, a loss-curve data source for an ML
    feature) scrubs through a training run by reading exactly those captured
    values. Inlining this at the call site would silently break that
    traceability contract even though the runtime behavior looks identical.
    """
    return (epoch, loss, accuracy)
