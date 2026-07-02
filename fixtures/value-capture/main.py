"""fixtures/value-capture — Phase 10.2 value-capture tracer fixture.

Exercises every function-signature shape the value-capture acceptance grid
covers: positional, defaults, keyword-only, *args, **kwargs, mixed,
instance method / staticmethod / classmethod, generator, a generator-
expression site, async def, recursion, and sensitive-named parameters
(redaction).

Run standalone: python main.py
"""

from __future__ import annotations

import asyncio


def add(a: int, b: int) -> int:
    """Positional args."""
    return a + b


def greet(name: str, greeting: str = "hello") -> str:
    """A default-valued arg."""
    return f"{greeting}, {name}!"


def scale(value: float, *, factor: float = 2.0) -> float:
    """Keyword-only arg."""
    return value * factor


def total(*numbers: int) -> int:
    """*args."""
    return sum(numbers)


def describe(**fields: object) -> str:
    """**kwargs."""
    return ", ".join(f"{k}={v}" for k, v in fields.items())


def mixed(a: int, b: int = 1, *rest: int, tag: str = "x", **extra: object) -> str:
    """Every kind of parameter at once: positional, default, *args, kwonly, **kwargs."""
    return f"{a}-{b}-{rest}-{tag}-{extra}"


def factorial(n: int) -> int:
    """Recursion."""
    if n <= 1:
        return 1
    return n * factorial(n - 1)


def login(username: str, password: str) -> str:
    """A 'password'-named parameter — must redact."""
    return f"login as {username}"


def call_api(endpoint: str, api_key: str) -> str:
    """An 'api_key'-named parameter — must redact."""
    return f"GET {endpoint}"


class Widget:
    def __init__(self, name: str) -> None:
        self.name = name

    def rename(self, new_name: str) -> str:
        """Instance method — 'self' is a declared parameter too."""
        self.name = new_name
        return self.name

    @staticmethod
    def describe_kind() -> str:
        """Staticmethod — no implicit first parameter."""
        return "widget"

    @classmethod
    def from_default(cls) -> Widget:
        """Classmethod — 'cls' is a declared parameter."""
        return cls("default")


def squares(n: int):
    """A generator function."""
    for i in range(n):
        yield i * i


def sum_of_squares(n: int) -> int:
    """Consumes a generator and a generator-expression call site."""
    total_ = sum(x * x for x in range(n))
    for v in squares(n):
        total_ += v
    return total_


async def fetch_value(x: int) -> int:
    """async def."""
    return x * 2


def main() -> None:
    add(1, 2)
    greet("Ada")
    greet("Grace", greeting="hi")
    scale(3.0)
    scale(3.0, factor=1.5)
    total(1, 2, 3)
    describe(role="admin", active=True)
    mixed(1, 2, 3, 4, tag="y", extra_field="z")
    factorial(5)
    login("ada", password="s3cret")
    call_api("/v1/widgets", api_key="tok_abc123")
    widget = Widget("gadget")
    widget.rename("gizmo")
    Widget.describe_kind()
    Widget.from_default()
    sum_of_squares(5)
    asyncio.run(fetch_value(21))


if __name__ == "__main__":
    main()
