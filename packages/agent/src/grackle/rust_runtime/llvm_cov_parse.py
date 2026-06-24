"""Pure parser for ``llvm-cov export --format=json`` output (ADR-0024).

No I/O, no subprocess — takes the JSON string and returns a list of
:class:`RustCoverFunction` records. This makes the parser fixture-testable
without a Rust toolchain installed.

``llvm-cov export --format=json`` emits a JSON document with this shape::

    {
      "version": "2.0.1",
      "type": "llvm.coverage.json.export",
      "data": [
        {
          "functions": [
            {
              "name": "<mangled-name>",
              "count": 3,
              "filenames": ["/absolute/path/to/src/main.rs"],
              "regions": [
                [lineStart, colStart, lineEnd, colEnd, count, fileID, expandedFileID, kind],
                ...
              ],
              ...
            }
          ],
          ...
        }
      ]
    }

Each ``functions[]`` entry may correspond to one monomorphisation of a generic
function. The ``count`` field is the function-level entry count (how many times
the function was entered). The ``start_line`` we report is the minimum of all
region start lines — the first executable line of the function body, used by
:class:`~grackle.rust_runtime.resolution.RustResolver` to bisect to the
enclosing declaration via ``_resolve_by_decl_line``.

Entries with missing/empty ``filenames`` or ``regions``, or with a non-integer
``count``, are silently skipped (defensive tolerance).
"""

from __future__ import annotations

import json
from typing import Any, TypedDict


class RustCoverFunction(TypedDict):
    path: str  # absolute filesystem path to the source file
    start_line: int  # minimum region start line (1-based); used for decl-line bisect
    count: int  # function entry count (>= 0)


def parse_export(json_text: str) -> list[RustCoverFunction]:
    """Parse ``llvm-cov export --format=json`` output into coverage function records.

    Returns one :class:`RustCoverFunction` per ``functions[]`` entry (including
    count-0 entries and monomorphisation duplicates — the adapter folds by node ID).
    Skips entries with missing or unusable data.
    """
    try:
        doc: Any = json.loads(json_text)
    except (json.JSONDecodeError, ValueError):
        return []

    results: list[RustCoverFunction] = []
    data = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(data, list):
        return []

    for section in data:
        if not isinstance(section, dict):
            continue
        functions = section.get("functions")
        if not isinstance(functions, list):
            continue
        for fn in functions:
            entry = _parse_function(fn)
            if entry is not None:
                results.append(entry)

    return results


def _parse_function(fn: Any) -> RustCoverFunction | None:
    """Parse one ``functions[]`` entry; return ``None`` to skip."""
    if not isinstance(fn, dict):
        return None

    # ``count`` must be a non-negative integer (never bool).
    raw_count = fn.get("count")
    if isinstance(raw_count, bool) or not isinstance(raw_count, int):
        return None
    count = raw_count

    # ``filenames[0]`` is the primary source file (absolute path).
    filenames = fn.get("filenames")
    if not isinstance(filenames, list) or not filenames:
        return None
    path = filenames[0]
    if not isinstance(path, str) or not path:
        return None

    # ``regions`` must be non-empty to determine the start line.
    regions = fn.get("regions")
    if not isinstance(regions, list) or not regions:
        return None

    start_line = _min_region_line(regions)
    if start_line is None:
        return None

    return RustCoverFunction(path=path, start_line=start_line, count=count)


def _min_region_line(regions: list[Any]) -> int | None:
    """Return the minimum line-start across all regions, or ``None`` if none parse."""
    min_line: int | None = None
    for r in regions:
        if not isinstance(r, (list, tuple)) or not r:
            continue
        line = r[0]
        if not isinstance(line, int) or isinstance(line, bool):
            continue
        if min_line is None or line < min_line:
            min_line = line
    return min_line
