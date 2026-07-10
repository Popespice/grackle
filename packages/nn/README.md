# grackle-nn

A from-scratch, layer-granularity numpy MLP — designed to be legible to grackle's own
tracer, time-travel debugger, heat-map, and diff tooling. The point isn't the model (a
small MLP on synthetic spirals); it's that grackle can watch it learn using the exact
same instruments it uses to analyze any other codebase, with zero changes to grackle
itself.

Layers, losses, optimizers, and the training loop are all implemented here without a
deep-learning framework: `Linear` / `ReLU` / `Tanh` layers, `SoftmaxCrossEntropy` / `MSE`
losses, `SGD` / `Adam` optimizers, and a `Sequential` model container.

The full watch-it-learn walkthrough — tracing the training demo with
`--capture-values`, then browsing the run live in grackle's frontend (heat map, flame
graph, time-travel value inspector) — lands in Phase 11.2.
