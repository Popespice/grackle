"""tiny-python-app — grackle Phase 6 runtime-tracer fixture.

Exercises the sys.monitoring tracer with:
  - Mutual recursion (is_even / is_odd) → many call/return events.
  - Exception handling (negative input to is_even) → exception events.
  - Multiple call sites from main() → cross-function coverage.

Run standalone: python main.py
"""

from __future__ import annotations


def is_even(n: int) -> bool:
    """Return True if *n* is even using mutual recursion with is_odd."""
    if n < 0:
        raise ValueError(f"expected non-negative integer, got {n}")
    if n == 0:
        return True
    return is_odd(n - 1)


def is_odd(n: int) -> bool:
    """Return True if *n* is odd using mutual recursion with is_even."""
    if n == 0:
        return False
    return is_even(n - 1)


def classify(n: int) -> str:
    """Return 'even', 'odd', or 'invalid' for *n*."""
    try:
        return "even" if is_even(n) else "odd"
    except ValueError:
        return "invalid"


def main() -> None:
    """Entry point — classifies integers 0‥4 and then the invalid case -1."""
    results = [f"{n}={classify(n)}" for n in range(5)]
    print(", ".join(results))
    # negative input exercises the exception path
    print(f"-1={classify(-1)}")


if __name__ == "__main__":
    main()
