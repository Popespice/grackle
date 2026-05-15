"""Golden-graph integration test for fixtures/tiny-app/.

Asserts exact node count, edge count, known node IDs, and key edge endpoints.
Serves as the regression net for the full adapter→walker→resolver pipeline.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from grackle.adapters.base import ParseOptions
from grackle.python_parser.adapter import PythonStaticParser

if TYPE_CHECKING:
    from grackle.adapters.base import GraphEdge, StaticGraph

TINY_APP = Path(__file__).parent.parent.parent.parent / "fixtures" / "tiny-app"


def _parse() -> StaticGraph:
    return PythonStaticParser().parse(TINY_APP, ParseOptions())


def _node_ids(graph: StaticGraph, kind: str) -> set[str]:
    return {n["id"] for n in graph["nodes"] if n["kind"] == kind}


def _edges(graph: StaticGraph, kind: str) -> list[GraphEdge]:
    return [e for e in graph["edges"] if e["kind"] == kind]


# ---------------------------------------------------------------------------
# Exact counts (golden)
# ---------------------------------------------------------------------------


def test_exact_node_count() -> None:
    assert len(_parse()["nodes"]) == 25


def test_exact_edge_count() -> None:
    assert len(_parse()["edges"]) == 42


def test_file_node_count() -> None:
    assert len(_node_ids(_parse(), "file")) == 6


def test_class_node_count() -> None:
    assert len(_node_ids(_parse(), "class")) == 4


def test_method_node_count() -> None:
    assert len(_node_ids(_parse(), "method")) == 8


def test_import_edge_count() -> None:
    assert len(_edges(_parse(), "import")) == 17


def test_inherit_edge_count() -> None:
    assert len(_edges(_parse(), "inherit")) == 1


def test_call_edge_count() -> None:
    assert len(_edges(_parse(), "call")) == 24


# ---------------------------------------------------------------------------
# Known node IDs
# ---------------------------------------------------------------------------


def test_file_nodes() -> None:
    ids = _node_ids(_parse(), "file")
    assert ids == {
        "main.py",
        "models.py",
        "utils.py",
        "services/__init__.py",
        "services/auth.py",
        "services/db.py",
    }


def test_class_nodes() -> None:
    ids = _node_ids(_parse(), "class")
    assert ids == {
        "models.py:User",
        "models.py:Admin",
        "services/auth.py:AuthService",
        "services/db.py:Database",
    }


def test_method_nodes() -> None:
    ids = _node_ids(_parse(), "method")
    assert "models.py:User.display" in ids
    assert "models.py:Admin.__init__" in ids
    assert "models.py:Admin.is_superadmin" in ids
    assert "services/auth.py:AuthService.login" in ids
    assert "services/auth.py:AuthService.create_token" in ids
    assert "services/db.py:Database.__init__" in ids
    assert "services/db.py:Database.connection" in ids


def test_function_nodes() -> None:
    ids = _node_ids(_parse(), "function")
    assert "utils.py:hash_password" in ids
    assert "utils.py:normalize_email" in ids
    assert "utils.py:send_welcome" in ids
    assert "services/db.py:query" in ids
    assert "main.py:run" in ids
    assert "main.py:make_admin" in ids


def test_closure_node_exists() -> None:
    func_ids = _node_ids(_parse(), "function")
    closures = [i for i in func_ids if "_parse_row" in i]
    assert len(closures) == 1
    assert closures[0].startswith("services/db.py:query._parse_row.")


# ---------------------------------------------------------------------------
# Known edge endpoints
# ---------------------------------------------------------------------------


def test_inherit_admin_from_user_resolved() -> None:
    inherits = _edges(_parse(), "inherit")
    assert len(inherits) == 1
    e = inherits[0]
    assert e["source"] == "models.py:Admin"
    assert e["target"] == "models.py:User"
    assert e.get("metadata", {}).get("resolved") is not False


def test_type_checking_import_tagged() -> None:
    imports = _edges(_parse(), "import")
    # models.py imports services.auth under TYPE_CHECKING
    tc = [e for e in imports if e["source"] == "models.py" and e["target"] == "services.auth"]
    assert len(tc) == 1
    assert tc[0]["metadata"].get("type_checking") is True


def test_call_cross_file_resolved() -> None:
    # auth.login calls hash_password which is imported from utils
    calls = _edges(_parse(), "call")
    resolved = [
        e
        for e in calls
        if e["source"] == "services/auth.py:AuthService.login"
        and e["target"] == "utils.py:hash_password"
    ]
    assert len(resolved) == 1


def test_call_local_closure_resolved() -> None:
    calls = _edges(_parse(), "call")
    closure_calls = [
        e for e in calls if e["source"] == "services/db.py:query" and "_parse_row" in e["target"]
    ]
    assert len(closure_calls) == 1
