"""Tests for typescript_parser.hints — HTTP and subprocess extraction."""

from __future__ import annotations

from grackle.typescript_parser.hints import extract_hints

FILE_ID = "typescript/server.ts"


def test_fetch_http_client() -> None:
    hints = extract_hints("fetch('/api/users')", FILE_ID)
    assert any(h["kind"] == "http_client" and h["payload"]["path"] == "/api/users" for h in hints)


def test_axios_get() -> None:
    hints = extract_hints("axios.get('/api/users')", FILE_ID)
    assert any(h["kind"] == "http_client" and h["payload"]["path"] == "/api/users" for h in hints)


def test_axios_post() -> None:
    hints = extract_hints("axios.post('/api/items')", FILE_ID)
    assert any(h["kind"] == "http_client" and h["payload"]["path"] == "/api/items" for h in hints)


def test_express_app_get() -> None:
    hints = extract_hints("app.get('/api/users', handler)", FILE_ID)
    assert any(h["kind"] == "http_server" and h["payload"]["path"] == "/api/users" for h in hints)


def test_express_router_post() -> None:
    hints = extract_hints("router.post('/api/items', handler)", FILE_ID)
    assert any(h["kind"] == "http_server" and h["payload"]["path"] == "/api/items" for h in hints)


def test_express_app_delete() -> None:
    hints = extract_hints("app.delete('/api/users/:id', handler)", FILE_ID)
    assert any(h["kind"] == "http_server" for h in hints)


def test_child_process_exec() -> None:
    hints = extract_hints("exec('make build')", FILE_ID)
    assert any(h["kind"] == "subprocess" and h["payload"]["command"] == "make build" for h in hints)


def test_child_process_spawn() -> None:
    hints = extract_hints("spawn('node')", FILE_ID)
    assert any(h["kind"] == "subprocess" and h["payload"]["command"] == "node" for h in hints)


def test_execa() -> None:
    hints = extract_hints("execa('pnpm')", FILE_ID)
    assert any(h["kind"] == "subprocess" and h["payload"]["command"] == "pnpm" for h in hints)


def test_language_field() -> None:
    hints = extract_hints("fetch('/api/users')", FILE_ID)
    assert all(h["language"] == "typescript" for h in hints)


def test_node_id_field() -> None:
    hints = extract_hints("fetch('/api/users')", FILE_ID)
    assert all(h["node_id"] == FILE_ID for h in hints)


def test_empty_source_returns_empty() -> None:
    assert extract_hints("", FILE_ID) == []
