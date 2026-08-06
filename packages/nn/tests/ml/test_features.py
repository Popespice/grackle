"""Tests for grackle_nn.ml.features (Phase 12.1, block M1).

The hand-built 6-node graph below is the fixture for every structural test in
this file. Literal expected values were derived by running the graph through
``extract_features`` and hand-verifying each nonzero column against the
edges/metadata that produced it (see the module docstring's column layout);
this file pins those verified values so a future regression is caught.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from grackle_nn.ml.features import FEATURE_NAMES, FEATURE_VERSION, extract_features

L2 = math.log(2.0)  # log1p(1)
L4 = math.log(4.0)  # log1p(3)


def _six_node_graph() -> dict[str, Any]:
    return {
        "version": 1,
        "language": "python",
        "nodes": [
            {"id": "main.py", "kind": "file", "name": "main.py", "path": "main.py"},
            {
                "id": "main.py:foo",
                "kind": "function",
                "name": "foo",
                "path": "main.py",
                "line": 5,
            },
            {
                "id": "pkg/tests/util.py",
                "kind": "file",
                "name": "util.py",
                "path": "pkg/tests/util.py",
            },
            {
                "id": "pkg/tests/util.py:test_bar",
                "kind": "function",
                "name": "test_bar",
                "path": "pkg/tests/util.py",
                "line": 3,
            },
            {
                "id": "pkg/tests/util.py:Cls",
                "kind": "class",
                "name": "Cls",
                "path": "pkg/tests/util.py",
                "line": 10,
            },
            {
                "id": "pkg/tests/util.py:Cls.method",
                "kind": "method",
                "name": "__init__",
                "path": "pkg/tests/util.py",
                "line": 11,
                "metadata": {"is_async": True, "decorators": ["staticmethod"]},
            },
        ],
        "edges": [
            {"source": "main.py", "target": "main.py:foo", "kind": "import"},
            {
                "source": "main.py:foo",
                "target": "pkg/tests/util.py:test_bar",
                "kind": "call",
            },
            {
                "source": "pkg/tests/util.py:test_bar",
                "target": "pkg/tests/util.py:Cls",
                "kind": "call",
            },
            {
                "source": "pkg/tests/util.py:Cls",
                "target": "pkg/tests/util.py:Cls.method",
                "kind": "inherit",
            },
            {
                "source": "pkg/tests/util.py:Cls.method",
                "target": "pkg/tests/util.py:test_bar",
                "kind": "cross_language_http_route",
            },
        ],
    }


# Hand-verified full 35-column rows (index order matches FEATURE_NAMES).
_EXPECTED_FOO = [
    L2,
    L2,
    L2,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,  # in_degree, out_degree, in buckets
    0.0,
    L2,
    0.0,
    0.0,
    0.0,
    0.0,  # out buckets
    1.0,
    0.0,
    0.0,
    0.0,
    0.0,  # kind one-hot: function
    0.0,
    L2,  # in_cycle=0, scc_size=1 (trivial component)
    0.6,
    0.6,  # in/out percentile (2 of 5 non-file nodes tie at rank; see below)
    0.0,
    L2,  # file fan in/out
    L2,
    1.0,  # bfs_depth=1, reachable
    L4,
    0.0,
    0.0,
    0.0,
    0.0,
    0.0,  # name_len=3 -> log1p(3), no flags
    L2,
    math.log1p(5),  # path_depth=1, line=5
]

_EXPECTED_CLS_METHOD = [
    L2,
    L2,
    0.0,
    0.0,
    L2,
    0.0,
    0.0,
    0.0,  # in_degree=1(inherit), out_degree=1(cross_language)
    0.0,
    0.0,
    0.0,
    0.0,
    L2,
    0.0,
    0.0,
    1.0,
    0.0,
    0.0,
    0.0,  # kind one-hot: method
    1.0,
    L4,  # in_cycle=1 (3-cycle), scc_size=3
    0.6,
    0.6,
    L2,
    0.0,  # file fan in=1, out=0
    # bfs_depth=0: the only inbound edge (Cls -> Cls.method) is "inherit", not
    # call/import, so Cls.method has zero call/import in-degree -> an entry itself.
    0.0,
    1.0,
    math.log1p(8),
    1.0,
    0.0,
    1.0,
    1.0,
    1.0,  # name_len=8 (__init__): dunder, test, async, decorated
    L4,
    math.log1p(11),  # path_depth=3 (pkg/tests/util.py), line=11
]


def test_full_row_hand_computed_foo() -> None:
    ids, x = extract_features(_six_node_graph())
    row = x[ids.index("main.py:foo")]
    assert row.tolist() == pytest.approx(_EXPECTED_FOO, abs=1e-12)


def test_full_row_hand_computed_cls_method() -> None:
    ids, x = extract_features(_six_node_graph())
    row = x[ids.index("pkg/tests/util.py:Cls.method")]
    assert row.tolist() == pytest.approx(_EXPECTED_CLS_METHOD, abs=1e-12)


def test_feature_names_length_and_version() -> None:
    assert len(FEATURE_NAMES) == 35
    assert len(set(FEATURE_NAMES)) == 35
    assert FEATURE_VERSION == 1


def test_bucket_collapse_cross_language_http_route() -> None:
    ids, x = extract_features(_six_node_graph())
    i_method = ids.index("pkg/tests/util.py:Cls.method")
    i_bar = ids.index("pkg/tests/util.py:test_bar")
    out_cross = FEATURE_NAMES.index("log1p_out_cross_language")
    in_cross = FEATURE_NAMES.index("log1p_in_cross_language")
    assert x[i_method, out_cross] > 0.0
    assert x[i_bar, in_cross] > 0.0


def test_self_loop_marks_in_cycle_with_trivial_scc_size() -> None:
    graph = {
        "version": 1,
        "language": "python",
        "nodes": [{"id": "a.py:f", "kind": "function", "name": "f", "path": "a.py"}],
        "edges": [{"source": "a.py:f", "target": "a.py:f", "kind": "call"}],
    }
    ids, x = extract_features(graph)
    i = ids.index("a.py:f")
    in_cycle = FEATURE_NAMES.index("in_cycle")
    scc = FEATURE_NAMES.index("log1p_scc_size")
    assert x[i, in_cycle] == 1.0
    assert x[i, scc] == pytest.approx(math.log1p(1))  # scc_size=1: self-loops don't inflate size


def test_three_node_cycle_scc_size_three() -> None:
    ids, x = extract_features(_six_node_graph())
    # test_bar -> Cls -> Cls.method -> test_bar via call/inherit/cross_language edges.
    cycle_ids = (
        "pkg/tests/util.py:test_bar",
        "pkg/tests/util.py:Cls",
        "pkg/tests/util.py:Cls.method",
    )
    for nid in cycle_ids:
        i = ids.index(nid)
        assert x[i, FEATURE_NAMES.index("in_cycle")] == 1.0
        assert x[i, FEATURE_NAMES.index("log1p_scc_size")] == pytest.approx(math.log1p(3))


def test_unreachable_is_zero_zero_at_bfs_and_reachable_columns() -> None:
    # Two nodes that only call each other, no file node and no zero-in-degree
    # entry point in the graph -> unreachable from the entry set.
    graph = {
        "version": 1,
        "language": "python",
        "nodes": [
            {"id": "a.py:b", "kind": "function", "name": "b", "path": "a.py"},
            {"id": "a.py:c", "kind": "function", "name": "c", "path": "a.py"},
        ],
        "edges": [
            {"source": "a.py:b", "target": "a.py:c", "kind": "call"},
            {"source": "a.py:c", "target": "a.py:b", "kind": "call"},
        ],
    }
    ids, x = extract_features(graph)
    bfs = FEATURE_NAMES.index("log1p_bfs_depth")
    reach = FEATURE_NAMES.index("reachable")
    for nid in ids:
        i = ids.index(nid)
        assert x[i, bfs] == 0.0
        assert x[i, reach] == 0.0


def test_percentile_ties_share_average_rank() -> None:
    # n0, n1 tie for out_degree=1 (both call t0); n2 has out_degree=1 too
    # (calls t1) -- all three tie, so all three share the average rank.
    n_nodes = [
        {"id": f"n{i}", "kind": "function", "name": f"n{i}", "path": "a.py"} for i in range(3)
    ]
    graph = {
        "version": 1,
        "language": "python",
        "nodes": n_nodes
        + [
            {"id": "t0", "kind": "function", "name": "t0", "path": "a.py"},
            {"id": "t1", "kind": "function", "name": "t1", "path": "a.py"},
        ],
        "edges": [
            {"source": "n0", "target": "t0", "kind": "call"},
            {"source": "n1", "target": "t0", "kind": "call"},
            {"source": "n2", "target": "t1", "kind": "call"},
        ],
    }
    ids, x = extract_features(graph)
    out_pct = FEATURE_NAMES.index("out_degree_percentile")
    values = {nid: x[ids.index(nid), out_pct] for nid in ("n0", "n1", "n2")}
    assert values["n0"] == values["n1"] == values["n2"]
    # Not just equal -- the correct AVERAGED rank: t0/t1 (out_degree=0) tie at
    # 0-indexed ranks 0,1 (avg 0.5); n0/n1/n2 (out_degree=1) tie at ranks
    # 2,3,4 (avg 3.0). Percentile = avg_rank / (n-1) = 3.0/4 = 0.75. A
    # min-rank or max-rank scheme (or an arbitrary-but-consistent one) would
    # also pass the equality check above but not this exact value.
    assert values["n0"] == pytest.approx(0.75)


def test_percentile_single_node_graph_is_zero() -> None:
    graph = {
        "version": 1,
        "language": "python",
        "nodes": [{"id": "a.py", "kind": "file", "name": "a.py", "path": "a.py"}],
        "edges": [],
    }
    ids, x = extract_features(graph)
    in_pct = FEATURE_NAMES.index("in_degree_percentile")
    out_pct = FEATURE_NAMES.index("out_degree_percentile")
    assert x[0, in_pct] == 0.0
    assert x[0, out_pct] == 0.0


def test_flag_rules_dunder_private_test_async_decorators() -> None:
    graph = {
        "version": 1,
        "language": "python",
        "nodes": [
            {"id": "a.py:__init__", "kind": "method", "name": "__init__", "path": "a.py"},
            {"id": "a.py:_helper", "kind": "function", "name": "_helper", "path": "a.py"},
            {"id": "a.py:test_thing", "kind": "function", "name": "test_thing", "path": "a.py"},
            {
                "id": "pkg/tests/util.py:plain",
                "kind": "function",
                "name": "plain",
                "path": "pkg/tests/util.py",
            },
            {
                "id": "a.py:test_helper.py:test_x",
                "kind": "function",
                "name": "test_x",
                "path": "a.py/test_helper.py",
            },
            {
                "id": "a.py:async_fn",
                "kind": "function",
                "name": "async_fn",
                "path": "a.py",
                "metadata": {"is_async": True},
            },
            {
                "id": "a.py:decorated",
                "kind": "function",
                "name": "decorated",
                "path": "a.py",
                "metadata": {"decorators": ["cached"]},
            },
        ],
        "edges": [],
    }
    ids, x = extract_features(graph)
    dunder = FEATURE_NAMES.index("is_dunder")
    private = FEATURE_NAMES.index("is_private")
    is_test = FEATURE_NAMES.index("is_test")
    is_async = FEATURE_NAMES.index("is_async")
    has_dec = FEATURE_NAMES.index("has_decorators")

    def row(nid: str) -> Any:
        return x[ids.index(nid)]

    assert row("a.py:__init__")[dunder] == 1.0
    assert row("a.py:__init__")[private] == 0.0  # dunder wins, not double-counted as private
    assert row("a.py:_helper")[private] == 1.0
    assert row("a.py:_helper")[dunder] == 0.0
    assert row("a.py:test_thing")[is_test] == 1.0  # name starts with test_
    assert row("pkg/tests/util.py:plain")[is_test] == 1.0  # path part "tests"
    assert row("a.py:test_helper.py:test_x")[is_test] == 1.0  # filename starts with test_
    assert row("a.py:async_fn")[is_async] == 1.0
    assert row("a.py:decorated")[has_dec] == 1.0


def test_empty_graph_returns_zero_by_thirty_five() -> None:
    ids, x = extract_features({"version": 1, "language": "python", "nodes": [], "edges": []})
    assert ids == []
    assert x.shape == (0, 35)


def test_dangling_edge_endpoints_are_dropped() -> None:
    graph = {
        "version": 1,
        "language": "python",
        "nodes": [{"id": "a.py:f", "kind": "function", "name": "f", "path": "a.py"}],
        "edges": [
            {"source": "a.py:f", "target": "SomeBuiltin", "kind": "call"},
            {"source": "Missing", "target": "a.py:f", "kind": "call"},
        ],
    }
    ids, x = extract_features(graph)
    assert x[0, FEATURE_NAMES.index("log1p_in_degree")] == 0.0
    assert x[0, FEATURE_NAMES.index("log1p_out_degree")] == 0.0


def test_dev_only_cross_check_in_cycle_against_agent_graph_analysis() -> None:
    """Independently verifies the reimplemented Tarjan against the shipped one.

    Imports ``grackle`` (the agent package) — allowed in tests only (dev dep
    since 11.2); ``grackle_nn.ml`` itself never imports it. See ADR-0028/M8.
    """
    from pathlib import Path

    from grackle.adapters import registry
    from grackle.adapters.base import ParseOptions
    from grackle.graph_analysis import enrich_metadata

    root = Path(__file__).parents[4] / "fixtures" / "tiny-python-app"
    static_parser = registry.get_static("python")
    assert static_parser is not None
    graph = static_parser.parse(root, ParseOptions())

    ids, x = extract_features(graph)
    in_cycle = FEATURE_NAMES.index("in_cycle")
    ml_in_cycle = {nid for nid, row in zip(ids, x, strict=True) if row[in_cycle] == 1.0}

    enrich_metadata(graph)
    agent_in_cycle: set[str] = set()
    for cycle in graph["metadata"]["cycles"]:
        agent_in_cycle.update(cycle["nodes"])

    assert ml_in_cycle  # non-vacuous: tiny-python-app has a real is_even/is_odd cycle
    assert ml_in_cycle == agent_in_cycle


def test_self_loop_only_node_is_still_an_entry_point() -> None:
    # A node whose only call/import edge is a self-loop has zero EXTERNAL
    # in-degree -- same as a node with no edges at all. Both must be treated
    # as entries (regression: the self-loop used to wrongly disqualify it,
    # since call_import_in_degree counted it but call_import_adj/the SCC
    # adjacency never did).
    graph = {
        "version": 1,
        "language": "python",
        "nodes": [
            {"id": "a.py:recurse", "kind": "function", "name": "recurse", "path": "a.py"},
            {"id": "a.py:other", "kind": "function", "name": "other", "path": "a.py"},
        ],
        "edges": [{"source": "a.py:recurse", "target": "a.py:recurse", "kind": "call"}],
    }
    ids, x = extract_features(graph)
    reach = FEATURE_NAMES.index("reachable")
    bfs = FEATURE_NAMES.index("log1p_bfs_depth")
    assert x[ids.index("a.py:recurse"), reach] == 1.0
    assert x[ids.index("a.py:recurse"), bfs] == 0.0
    assert x[ids.index("a.py:other"), reach] == 1.0


def test_none_path_defaults_to_empty_string_not_crash() -> None:
    graph = {
        "version": 1,
        "language": "python",
        "nodes": [{"id": "a", "kind": "function", "name": "f", "path": None}],
        "edges": [],
    }
    ids, x = extract_features(graph)
    assert ids == ["a"]
    assert x[0, FEATURE_NAMES.index("log1p_path_depth")] == 0.0


def test_non_dict_metadata_is_ignored_not_crash() -> None:
    graph = {
        "version": 1,
        "language": "python",
        "nodes": [
            {
                "id": "a.py:f",
                "kind": "function",
                "name": "f",
                "path": "a.py",
                "metadata": ["oops", "not", "a", "dict"],
            }
        ],
        "edges": [],
    }
    ids, x = extract_features(graph)
    assert x[0, FEATURE_NAMES.index("is_async")] == 0.0
    assert x[0, FEATURE_NAMES.index("has_decorators")] == 0.0


def test_is_test_bare_filename_prefix_without_tests_dir_or_prefixed_name() -> None:
    # Exercises _is_test's third OR-branch specifically: the node's own name
    # doesn't start with "test_" and no path segment is literally "tests",
    # only the bare filename itself starts with "test_".
    graph = {
        "version": 1,
        "language": "python",
        "nodes": [
            {
                "id": "test_util.py:helper",
                "kind": "function",
                "name": "helper",
                "path": "test_util.py",
            }
        ],
        "edges": [],
    }
    ids, x = extract_features(graph)
    assert x[0, FEATURE_NAMES.index("is_test")] == 1.0


def test_is_dunder_excludes_bare_underscore_runs() -> None:
    graph = {
        "version": 1,
        "language": "python",
        "nodes": [{"id": "a.py:____", "kind": "function", "name": "____", "path": "a.py"}],
        "edges": [],
    }
    ids, x = extract_features(graph)
    assert x[0, FEATURE_NAMES.index("is_dunder")] == 0.0
    assert x[0, FEATURE_NAMES.index("is_private")] == 1.0


def test_edge_kind_other_fallback_bucket() -> None:
    graph = {
        "version": 1,
        "language": "python",
        "nodes": [
            {"id": "a.py:x", "kind": "function", "name": "x", "path": "a.py"},
            {"id": "a.py:y", "kind": "function", "name": "y", "path": "a.py"},
        ],
        "edges": [{"source": "a.py:x", "target": "a.py:y", "kind": "some_unrecognized_kind"}],
    }
    ids, x = extract_features(graph)
    assert x[ids.index("a.py:x"), FEATURE_NAMES.index("log1p_out_other")] > 0.0
    assert x[ids.index("a.py:y"), FEATURE_NAMES.index("log1p_in_other")] > 0.0


def test_node_kind_other_fallback_bucket() -> None:
    graph = {
        "version": 1,
        "language": "python",
        "nodes": [{"id": "a.ts:X", "kind": "interface", "name": "X", "path": "a.ts"}],
        "edges": [],
    }
    ids, x = extract_features(graph)
    assert x[0, FEATURE_NAMES.index("kind_other")] == 1.0
