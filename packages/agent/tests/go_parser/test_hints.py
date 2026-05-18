"""Tests for go_parser.hints — HTTP and subprocess extraction."""

from __future__ import annotations

from grackle.go_parser.hints import extract_hints

FILE_ID = "go/api.go"


def test_http_get_client() -> None:
    hints = extract_hints('http.Get("/api/users")', FILE_ID)
    assert any(h["kind"] == "http_client" and h["payload"]["path"] == "/api/users" for h in hints)


def test_http_post_client() -> None:
    hints = extract_hints('http.Post("/api/items")', FILE_ID)
    assert any(h["kind"] == "http_client" and h["payload"]["path"] == "/api/items" for h in hints)


def test_http_new_request() -> None:
    hints = extract_hints('http.NewRequest("/api/data")', FILE_ID)
    assert any(h["kind"] == "http_client" and h["payload"]["path"] == "/api/data" for h in hints)


def test_handle_func_server() -> None:
    hints = extract_hints('http.HandleFunc("/api/users", handler)', FILE_ID)
    assert any(h["kind"] == "http_server" and h["payload"]["path"] == "/api/users" for h in hints)


def test_mux_handle_func() -> None:
    hints = extract_hints('mux.HandleFunc("/api/users", handler)', FILE_ID)
    assert any(h["kind"] == "http_server" and h["payload"]["path"] == "/api/users" for h in hints)


def test_http_handle() -> None:
    hints = extract_hints('http.Handle("/api/users", handler)', FILE_ID)
    assert any(h["kind"] == "http_server" and h["payload"]["path"] == "/api/users" for h in hints)


def test_exec_command() -> None:
    hints = extract_hints('exec.Command("make")', FILE_ID)
    assert any(h["kind"] == "subprocess" and h["payload"]["command"] == "make" for h in hints)


def test_language_field() -> None:
    hints = extract_hints('http.Get("/api/users")', FILE_ID)
    assert all(h["language"] == "go" for h in hints)


def test_node_id_field() -> None:
    hints = extract_hints('http.Get("/api/users")', FILE_ID)
    assert all(h["node_id"] == FILE_ID for h in hints)


def test_empty_source_returns_empty() -> None:
    assert extract_hints("", FILE_ID) == []
