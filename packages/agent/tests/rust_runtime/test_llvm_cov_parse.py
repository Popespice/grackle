"""Tests for rust_runtime.llvm_cov_parse — no Rust toolchain required."""

from __future__ import annotations

import json
from typing import Any

from grackle.rust_runtime.llvm_cov_parse import parse_export

# ---------------------------------------------------------------------------
# Minimal golden JSON blob representative of real llvm-cov export output.
# Keeps the test self-contained (no real Rust build required).
# ---------------------------------------------------------------------------

_SAMPLE_DOC: dict[str, Any] = {
    "version": "2.0.1",
    "type": "llvm.coverage.json.export",
    "data": [
        {
            "files": [],
            "functions": [
                {
                    # Typical free function
                    "name": "_ZN8tiny_bin4mainE",
                    "count": 1,
                    "filenames": ["/repo/src/main.rs"],
                    "regions": [[5, 1, 10, 2, 1, 0, 0, 0]],
                    "branches": [],
                },
                {
                    # Function called multiple times
                    "name": "_ZN8tiny_bin4calc3addE",
                    "count": 3,
                    "filenames": ["/repo/src/calc.rs"],
                    "regions": [[1, 1, 3, 2, 3, 0, 0, 0]],
                    "branches": [],
                },
                {
                    # Never-executed function (count=0)
                    "name": "_ZN8tiny_bin4calc3subE",
                    "count": 0,
                    "filenames": ["/repo/src/calc.rs"],
                    "regions": [[5, 1, 7, 2, 0, 0, 0, 0]],
                    "branches": [],
                },
                {
                    # Monomorphisation of a generic function — same file+start_line, different name
                    "name": "_ZN8tiny_bin7generic3fooIiEE",
                    "count": 2,
                    "filenames": ["/repo/src/main.rs"],
                    "regions": [[12, 1, 15, 2, 2, 0, 0, 0]],
                    "branches": [],
                },
                {
                    # Second monomorphisation at same location (different type param)
                    "name": "_ZN8tiny_bin7generic3fooIsEE",
                    "count": 1,
                    "filenames": ["/repo/src/main.rs"],
                    "regions": [[12, 1, 15, 2, 1, 0, 0, 0]],
                    "branches": [],
                },
                {
                    # Multiple regions — start_line should be the minimum
                    "name": "_ZN8tiny_bin5multiE",
                    "count": 1,
                    "filenames": ["/repo/src/main.rs"],
                    "regions": [
                        [20, 1, 22, 2, 1, 0, 0, 0],
                        [17, 1, 19, 2, 1, 0, 0, 0],  # earlier line
                        [25, 1, 27, 2, 0, 0, 0, 0],
                    ],
                    "branches": [],
                },
            ],
            "totals": {},
        }
    ],
}

_SAMPLE_JSON = json.dumps(_SAMPLE_DOC)


def test_parses_all_entries() -> None:
    results = parse_export(_SAMPLE_JSON)
    assert len(results) == 6


def test_path_extracted() -> None:
    results = parse_export(_SAMPLE_JSON)
    paths = {r["path"] for r in results}
    assert "/repo/src/main.rs" in paths
    assert "/repo/src/calc.rs" in paths


def test_count_extracted() -> None:
    results = parse_export(_SAMPLE_JSON)
    by_name: dict[str, int] = {}
    fns: list[dict[str, Any]] = _SAMPLE_DOC["data"][0]["functions"]
    for fn, r in zip(fns, results, strict=True):
        by_name[fn["name"]] = r["count"]
    assert by_name["_ZN8tiny_bin4calc3addE"] == 3
    assert by_name["_ZN8tiny_bin4calc3subE"] == 0  # cold entries are kept


def test_start_line_is_min_region() -> None:
    results = parse_export(_SAMPLE_JSON)
    # The "multi" entry has regions starting at 17, 20, 25 — min is 17.
    multi = [r for r in results if r["path"] == "/repo/src/main.rs" and r["start_line"] == 17]
    assert len(multi) == 1


def test_monomorphisations_preserved_separately() -> None:
    # Both generic foo entries should come through as separate records.
    results = parse_export(_SAMPLE_JSON)
    foo_entries = [r for r in results if r["start_line"] == 12]
    assert len(foo_entries) == 2
    counts = sorted(r["count"] for r in foo_entries)
    assert counts == [1, 2]


_R = [[1, 1, 2, 2, 1, 0, 0, 0]]  # minimal region used by several inline tests


def test_count_present_assertion() -> None:
    # Guard: an entry without 'count' is skipped, not silently zero.
    doc = {"data": [{"functions": [{"filenames": ["/a.rs"], "regions": _R}]}]}
    results = parse_export(json.dumps(doc))
    assert results == []


def test_bool_count_skipped() -> None:
    # bool is an int subclass in Python — must not slip through as count=1 or count=0.
    doc = {"data": [{"functions": [{"count": True, "filenames": ["/a.rs"], "regions": _R}]}]}
    results = parse_export(json.dumps(doc))
    assert results == []


def test_empty_filenames_skipped() -> None:
    doc = {"data": [{"functions": [{"count": 1, "filenames": [], "regions": _R}]}]}
    results = parse_export(json.dumps(doc))
    assert results == []


def test_empty_regions_skipped() -> None:
    doc = {"data": [{"functions": [{"count": 1, "filenames": ["/a.rs"], "regions": []}]}]}
    results = parse_export(json.dumps(doc))
    assert results == []


def test_malformed_json_returns_empty() -> None:
    assert parse_export("not json at all") == []


def test_missing_data_key_returns_empty() -> None:
    assert parse_export(json.dumps({"version": "2.0.1"})) == []


def test_multiple_data_sections() -> None:
    doc = {
        "data": [
            {"functions": [{"name": "a", "count": 1, "filenames": ["/a.rs"], "regions": _R}]},
            {"functions": [{"name": "b", "count": 2, "filenames": ["/b.rs"], "regions": _R}]},
        ]
    }
    results = parse_export(json.dumps(doc))
    assert len(results) == 2


def test_macro_expansion_regions_ignored_for_start_line() -> None:
    # A region with fileID==1 (macro-expansion, indexes a different file in filenames)
    # must not lower start_line below the primary-file body. Without the fileID filter
    # the expanded region at line 2 would win over the primary-file region at line 10,
    # causing decl-bisect to attribute this function to the wrong node.
    doc = {
        "data": [
            {
                "functions": [
                    {
                        "name": "_ZN4foo3barE",
                        "count": 1,
                        "filenames": ["/src/main.rs", "/src/macros.rs"],
                        "regions": [
                            [10, 1, 15, 2, 1, 0, 0, 0],  # primary file, line 10
                            [2, 5, 2, 30, 1, 1, 0, 0],  # macro expansion in macros.rs
                        ],
                        "branches": [],
                    }
                ]
            }
        ]
    }
    results = parse_export(json.dumps(doc))
    assert len(results) == 1
    assert results[0]["start_line"] == 10  # macro region at line 2 must be ignored


def test_fully_macro_generated_function_uses_fallback() -> None:
    # When ALL regions have fileID > 0 (fully macro-generated, no primary-file region),
    # the fallback path uses all regions so the function is not silently dropped.
    doc = {
        "data": [
            {
                "functions": [
                    {
                        "name": "_ZN4foo6DeriveE",
                        "count": 1,
                        "filenames": ["/src/main.rs", "/src/macros.rs"],
                        "regions": [
                            [5, 1, 8, 2, 1, 1, 0, 0],  # macro expansion only
                            [12, 1, 14, 2, 1, 1, 0, 0],  # another macro expansion
                        ],
                        "branches": [],
                    }
                ]
            }
        ]
    }
    results = parse_export(json.dumps(doc))
    assert len(results) == 1
    assert results[0]["start_line"] == 5  # fallback to min across all regions
