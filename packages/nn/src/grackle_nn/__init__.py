from importlib.metadata import PackageNotFoundError, version

from grackle_nn.data import make_spirals
from grackle_nn.layers import Layer, Linear, ReLU, Tanh
from grackle_nn.losses import MSE, ClassificationLoss, SoftmaxCrossEntropy
from grackle_nn.metrics import (
    accuracy,
    record_architecture,
    record_epoch,
    record_layer_stats,
)
from grackle_nn.model import Sequential
from grackle_nn.optim import SGD, Adam, Optimizer
from grackle_nn.train import EpochStats, evaluate, fit, train_step

try:
    __version__ = version("grackle-nn")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__all__ = [
    "MSE",
    "SGD",
    "Adam",
    "ClassificationLoss",
    "EpochStats",
    "Layer",
    "Linear",
    "Optimizer",
    "ReLU",
    "Sequential",
    "SoftmaxCrossEntropy",
    "Tanh",
    "accuracy",
    "evaluate",
    "fit",
    "make_spirals",
    "record_architecture",
    "record_epoch",
    "record_layer_stats",
    "train_step",
]
