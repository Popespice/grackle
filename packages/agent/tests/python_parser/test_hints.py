"""Tests for python_parser.hints — HTTP and subprocess extraction."""

from __future__ import annotations

from grackle.python_parser.hints import extract_hints

FILE_ID = "python/client.py"


def test_requests_get() -> None:
    hints = extract_hints("requests.get('/api/users')", FILE_ID)
    assert any(h["kind"] == "http_client" and h["payload"]["path"] == "/api/users" for h in hints)


def test_requests_post() -> None:
    hints = extract_hints("requests.post('/api/items')", FILE_ID)
    assert any(h["kind"] == "http_client" and h["payload"]["path"] == "/api/items" for h in hints)


def test_httpx_get() -> None:
    hints = extract_hints("httpx.get('/health/check')", FILE_ID)
    assert any(
        h["kind"] == "http_client" and h["payload"]["path"] == "/health/check" for h in hints
    )


def test_urllib_urlopen() -> None:
    hints = extract_hints("urllib.request.urlopen('/api/data')", FILE_ID)
    assert any(h["kind"] == "http_client" and h["payload"]["path"] == "/api/data" for h in hints)


def test_flask_route() -> None:
    src = "@app.route('/api/users')\ndef users(): pass"
    hints = extract_hints(src, FILE_ID)
    assert any(h["kind"] == "http_server" and h["payload"]["path"] == "/api/users" for h in hints)


def test_fastapi_decorator() -> None:
    src = "@router.get('/api/users')\nasync def list_users(): pass"
    hints = extract_hints(src, FILE_ID)
    assert any(h["kind"] == "http_server" and h["payload"]["path"] == "/api/users" for h in hints)


def test_django_path() -> None:
    src = "urlpatterns = [path('/api/users', views.users)]"
    hints = extract_hints(src, FILE_ID)
    assert any(h["kind"] == "http_server" and h["payload"]["path"] == "/api/users" for h in hints)


def test_subprocess_run_list() -> None:
    hints = extract_hints("subprocess.run(['./scripts/build.ts'])", FILE_ID)
    assert any(
        h["kind"] == "subprocess" and h["payload"]["command"] == "./scripts/build.ts" for h in hints
    )


def test_subprocess_popen() -> None:
    hints = extract_hints("subprocess.Popen(['node', 'app.js'])", FILE_ID)
    assert any(h["kind"] == "subprocess" and h["payload"]["command"] == "node" for h in hints)


def test_os_system() -> None:
    hints = extract_hints("os.system('make build')", FILE_ID)
    assert any(h["kind"] == "subprocess" and h["payload"]["command"] == "make build" for h in hints)


def test_language_field() -> None:
    hints = extract_hints("requests.get('/api/users')", FILE_ID)
    assert all(h["language"] == "python" for h in hints)


def test_node_id_field() -> None:
    hints = extract_hints("requests.get('/api/users')", FILE_ID)
    assert all(h["node_id"] == FILE_ID for h in hints)


def test_empty_source_returns_empty() -> None:
    assert extract_hints("", FILE_ID) == []


def test_no_patterns_returns_empty() -> None:
    assert extract_hints("x = 1 + 2", FILE_ID) == []
