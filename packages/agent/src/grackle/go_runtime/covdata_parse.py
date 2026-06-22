"""Pure parser for ``go tool covdata textfmt`` output (ADR-0023).

No I/O, no subprocess — takes the textfmt string and returns a list of
:class:`GoCoverBlock` records. This makes the parser fixture-testable without
a Go toolchain installed.

``go tool covdata textfmt`` emits the standard Go coverage profile format with
import-path-prefixed file paths:

    mode: count
    example.com/tinyapp/main.go:10.13,14.2 3 1
    example.com/tinyapp/models/user.go:15.13,17.2 1 1
    example.com/tinyapp/models/user.go:19.1,21.2 1 0

Grammar (per non-header line):
    <import-prefixed-path>:<sLine>.<sCol>,<eLine>.<eCol> <numStmts> <count>

Note the path is the **module-import-prefixed** path, NOT a filesystem path.
:class:`~grackle.go_runtime.resolution.GoResolver` strips the module prefix
during resolution.
"""

from __future__ import annotations

import re
from typing import TypedDict


class GoCoverBlock(TypedDict):
    import_path: str  # e.g. "example.com/tinyapp/models/user.go"
    start_line: int  # statement line (NOT the func-keyword decl line), 1-based
    count: int  # exact block execution count (0 = never executed)


# Matches: <path>:<sLine>.<sCol>,<eLine>.<eCol> <numStmts> <count>
_LINE_RE = re.compile(r"^(?P<path>.+):(?P<sl>\d+)\.\d+,\d+\.\d+ \d+ (?P<count>\d+)$")


def parse_textfmt(text: str) -> list[GoCoverBlock]:
    """Parse ``go tool covdata textfmt`` output into a list of coverage blocks.

    Skips blank lines, the ``mode:`` header, and any malformed lines
    (defensive tolerance, mirrors ``coverage_poll``'s approach). Returns one
    :class:`GoCoverBlock` per parsed line — all blocks, including count-0 ones.
    """
    blocks: list[GoCoverBlock] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("mode:"):
            continue
        m = _LINE_RE.match(line)
        if m is None:
            continue
        blocks.append(
            GoCoverBlock(
                import_path=m.group("path"),
                start_line=int(m.group("sl")),
                count=int(m.group("count")),
            )
        )
    return blocks
