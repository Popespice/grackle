from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from grackle_nn._types import Array, IntArray
    from grackle_nn.model import Sequential


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


def record_architecture(model: Sequential) -> str:
    """Traced beacon — do not inline or remove.

    Returns a space-separated token string describing the model's layer stack,
    one token per layer in forward order: ``linear:<in>:<out>`` for a
    param-carrying layer (dims read from ``params[0].shape``), else the layer
    type name lowercased (e.g. ``relu``). The demo net yields
    ``"linear:2:32 relu linear:32:32 relu linear:32:3"``.

    grackle's tracer captures this return value once per run; a frontend
    reconstruction (Phase 12.4's NetworkViewPanel) parses the captured repr to
    render the network's structure. The token grammar is therefore a versioned
    parse contract — changing it changes what the frontend can render. Token
    order equals the order layers fire inside ``Sequential.forward``, which is
    what disambiguates the three ``Linear`` instances that share one trace
    node_id. Attribute access only, no nested traced calls, and a list
    comprehension (never a generator expression — a genexp frame would leak
    events into the trace), so the beacon's own event shape stays trivial.
    """
    tokens = [
        f"linear:{layer.params[0].shape[0]}:{layer.params[0].shape[1]}"
        if layer.params
        else type(layer).__name__.lower()
        for layer in model.layers
    ]
    return " ".join(tokens)


def record_layer_stats(epoch: int, stats: tuple[float, ...]) -> tuple[int | float, ...]:
    """Identity passthrough — do not inline or remove.

    A per-epoch trace beacon, sibling to :func:`record_epoch`. ``stats`` is a
    flat tuple of pre-rounded floats ``(w0_rms, dw0_rms, w1_rms, dw1_rms, …)``
    in model order — the weight RMS and per-epoch weight-change RMS of each
    param-carrying layer — computed and rounded by the caller (``train.fit``)
    so this function does no numpy work and adds no nested traced calls of its
    own. Returns ``(epoch, *stats)``, a flat ``1 + 2L`` tuple whose captured
    repr the frontend parses (Phase 12.4). Kept flat (depth 1) and short so the
    repr stays untruncated under the default capture limits
    (``max_value_len=120``, ``max_value_items=10``; the ``1 + 2L`` item bound
    holds through L=4). The return repr is a versioned frontend parse contract.
    """
    return (epoch, *stats)
