from importlib.metadata import PackageNotFoundError, version

from grackle.adapters import registry as registry

try:
    __version__ = version("grackle")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__all__ = ["registry"]
