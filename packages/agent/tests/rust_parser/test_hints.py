"""Tests for rust_parser.hints — HTTP and subprocess extraction."""

from __future__ import annotations

from grackle.rust_parser.hints import extract_hints

FILE_ID = "rust/api.rs"


def test_reqwest_get_fn() -> None:
    hints = extract_hints('reqwest::get("/api/users")', FILE_ID)
    assert any(h["kind"] == "http_client" and h["payload"]["path"] == "/api/users" for h in hints)


def test_reqwest_client_get() -> None:
    hints = extract_hints('.get("/api/users")', FILE_ID)
    assert any(h["kind"] == "http_client" and h["payload"]["path"] == "/api/users" for h in hints)


def test_reqwest_client_post() -> None:
    hints = extract_hints('.post("/api/items")', FILE_ID)
    assert any(h["kind"] == "http_client" and h["payload"]["path"] == "/api/items" for h in hints)


def test_axum_route() -> None:
    hints = extract_hints('.route("/api/users", get(handler))', FILE_ID)
    assert any(h["kind"] == "http_server" and h["payload"]["path"] == "/api/users" for h in hints)


def test_command_new() -> None:
    hints = extract_hints('Command::new("cargo")', FILE_ID)
    assert any(h["kind"] == "subprocess" and h["payload"]["command"] == "cargo" for h in hints)


def test_language_field() -> None:
    hints = extract_hints('reqwest::get("/api/users")', FILE_ID)
    assert all(h["language"] == "rust" for h in hints)


def test_node_id_field() -> None:
    hints = extract_hints('reqwest::get("/api/users")', FILE_ID)
    assert all(h["node_id"] == FILE_ID for h in hints)


def test_empty_source_returns_empty() -> None:
    assert extract_hints("", FILE_ID) == []
