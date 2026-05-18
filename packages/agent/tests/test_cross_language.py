"""Unit tests for cross_language.py — path normalisation and edge construction."""

from __future__ import annotations

from grackle.cross_language import normalize_http_path, resolve_cross_language_edges

# ---------------------------------------------------------------------------
# normalize_http_path
# ---------------------------------------------------------------------------


def test_normalize_strips_whitespace() -> None:
    assert normalize_http_path("  /api/users  ") == "/api/users"


def test_normalize_lowercases() -> None:
    assert normalize_http_path("/API/USERS") == "/api/users"


def test_normalize_strips_trailing_slash() -> None:
    assert normalize_http_path("/api/users/") == "/api/users"
    assert normalize_http_path("/") == ""


def test_normalize_curly_param() -> None:
    assert normalize_http_path("/users/{id}") == "/users/{param}"


def test_normalize_colon_param() -> None:
    assert normalize_http_path("/users/:id") == "/users/{param}"


def test_normalize_angle_param() -> None:
    assert normalize_http_path("/users/<id>") == "/users/{param}"


def test_normalize_multiple_params() -> None:
    assert normalize_http_path("/users/{id}/posts/:slug") == "/users/{param}/posts/{param}"


def test_normalize_no_params_unchanged() -> None:
    assert normalize_http_path("/api/users") == "/api/users"


# ---------------------------------------------------------------------------
# resolve_cross_language_edges — HTTP client↔server matching
# ---------------------------------------------------------------------------


def _file_node(node_id: str) -> dict[str, str]:
    return {"id": node_id, "kind": "file", "name": node_id, "path": node_id}


def test_http_match_emits_cross_language_call() -> None:
    hints = [
        {
            "kind": "http_client",
            "node_id": "python/client.py",
            "language": "python",
            "payload": {"path": "/api/users"},
        },
        {
            "kind": "http_server",
            "node_id": "typescript/server.ts",
            "language": "typescript",
            "payload": {"path": "/api/users"},
        },
    ]
    nodes = [_file_node("python/client.py"), _file_node("typescript/server.ts")]
    edges = resolve_cross_language_edges(hints, nodes)
    assert len(edges) == 1
    assert edges[0]["kind"] == "cross_language_call"
    assert edges[0]["source"] == "python/client.py"
    assert edges[0]["target"] == "typescript/server.ts"


def test_http_match_normalizes_paths() -> None:
    hints = [
        {
            "kind": "http_client",
            "node_id": "a.py",
            "language": "python",
            "payload": {"path": "/API/Users/"},
        },
        {
            "kind": "http_server",
            "node_id": "b.ts",
            "language": "typescript",
            "payload": {"path": "/api/users"},
        },
    ]
    nodes = [_file_node("a.py"), _file_node("b.ts")]
    edges = resolve_cross_language_edges(hints, nodes)
    assert len(edges) == 1


def test_http_param_styles_match_each_other() -> None:
    hints = [
        {
            "kind": "http_client",
            "node_id": "a.py",
            "language": "python",
            "payload": {"path": "/users/{id}"},
        },
        {
            "kind": "http_server",
            "node_id": "b.ts",
            "language": "typescript",
            "payload": {"path": "/users/:id"},
        },
    ]
    nodes = [_file_node("a.py"), _file_node("b.ts")]
    edges = resolve_cross_language_edges(hints, nodes)
    assert len(edges) == 1


def test_short_path_suppressed() -> None:
    hints = [
        {
            "kind": "http_client",
            "node_id": "a.py",
            "language": "python",
            "payload": {"path": "/health"},
        },
        {
            "kind": "http_server",
            "node_id": "b.ts",
            "language": "typescript",
            "payload": {"path": "/health"},
        },
    ]
    nodes = [_file_node("a.py"), _file_node("b.ts")]
    edges = resolve_cross_language_edges(hints, nodes)
    assert edges == []


def test_no_self_loop() -> None:
    hints = [
        {
            "kind": "http_client",
            "node_id": "same.ts",
            "language": "typescript",
            "payload": {"path": "/api/users"},
        },
        {
            "kind": "http_server",
            "node_id": "same.ts",
            "language": "typescript",
            "payload": {"path": "/api/users"},
        },
    ]
    nodes = [_file_node("same.ts")]
    edges = resolve_cross_language_edges(hints, nodes)
    assert edges == []


def test_no_match_returns_empty() -> None:
    hints = [
        {
            "kind": "http_client",
            "node_id": "a.py",
            "language": "python",
            "payload": {"path": "/api/orders"},
        },
        {
            "kind": "http_server",
            "node_id": "b.ts",
            "language": "typescript",
            "payload": {"path": "/api/users"},
        },
    ]
    nodes = [_file_node("a.py"), _file_node("b.ts")]
    edges = resolve_cross_language_edges(hints, nodes)
    assert edges == []


def test_empty_hints_returns_empty() -> None:
    assert resolve_cross_language_edges([], []) == []


# ---------------------------------------------------------------------------
# resolve_cross_language_edges — subprocess matching
# ---------------------------------------------------------------------------


def test_subprocess_match_emits_cross_language_spawn() -> None:
    hints = [
        {
            "kind": "subprocess",
            "node_id": "python/client.py",
            "language": "python",
            "payload": {"command": "./scripts/build.ts"},
        },
    ]
    nodes = [_file_node("python/client.py"), _file_node("scripts/build.ts")]
    edges = resolve_cross_language_edges(hints, nodes)
    assert len(edges) == 1
    assert edges[0]["kind"] == "cross_language_spawn"
    assert edges[0]["source"] == "python/client.py"
    assert edges[0]["target"] == "scripts/build.ts"


def test_subprocess_strips_leading_dot_slash() -> None:
    hints = [
        {
            "kind": "subprocess",
            "node_id": "runner.py",
            "language": "python",
            "payload": {"command": "./scripts/deploy.sh"},
        },
    ]
    nodes = [_file_node("runner.py"), _file_node("scripts/deploy.sh")]
    edges = resolve_cross_language_edges(hints, nodes)
    assert len(edges) == 1


def test_subprocess_suffix_match() -> None:
    hints = [
        {
            "kind": "subprocess",
            "node_id": "runner.py",
            "language": "python",
            "payload": {"command": "tools/gen.py"},
        },
    ]
    nodes = [_file_node("runner.py"), _file_node("src/tools/gen.py")]
    edges = resolve_cross_language_edges(hints, nodes)
    assert len(edges) == 1
    assert edges[0]["target"] == "src/tools/gen.py"


def test_subprocess_no_match_returns_empty() -> None:
    hints = [
        {
            "kind": "subprocess",
            "node_id": "a.py",
            "language": "python",
            "payload": {"command": "unknown-bin"},
        },
    ]
    nodes = [_file_node("a.py"), _file_node("scripts/other.ts")]
    edges = resolve_cross_language_edges(hints, nodes)
    assert edges == []


def test_subprocess_no_self_loop() -> None:
    hints = [
        {
            "kind": "subprocess",
            "node_id": "scripts/build.ts",
            "language": "typescript",
            "payload": {"command": "scripts/build.ts"},
        },
    ]
    nodes = [_file_node("scripts/build.ts")]
    edges = resolve_cross_language_edges(hints, nodes)
    assert edges == []
