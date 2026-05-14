import socket
from typing import cast

import pytest


@pytest.fixture
def free_port() -> int:
    """Return a free ephemeral port on 127.0.0.1, OS-assigned."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return cast("int", s.getsockname()[1])
