from importlib.metadata import PackageNotFoundError, version

import grackle.go_parser  # noqa: F401  — triggers GoStaticParser registration
import grackle.python_parser  # noqa: F401  — triggers PythonStaticParser registration
import grackle.typescript_parser  # noqa: F401  — triggers TypeScriptStaticParser registration
from grackle.adapters import registry as registry

try:
    __version__ = version("grackle")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__all__ = ["registry"]
