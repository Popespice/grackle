import json
from collections.abc import Generator

import pytest
import structlog

from grackle.logging import configure_logging


@pytest.fixture(autouse=True)
def _reset_structlog() -> Generator[None, None, None]:
    yield
    structlog.reset_defaults()


def test_json_format_emits_valid_json(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(format="json")
    structlog.get_logger().info("hello world", extra="data")
    out = capsys.readouterr().out.strip()
    assert out, "expected log output"
    parsed = json.loads(out)
    assert parsed["event"] == "hello world"
    assert parsed["extra"] == "data"


def test_json_format_includes_level_and_timestamp(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(format="json")
    structlog.get_logger().info("check fields")
    out = capsys.readouterr().out.strip()
    parsed = json.loads(out)
    assert parsed.get("level") == "info"
    assert "timestamp" in parsed


def test_pretty_format_contains_event(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging(format="pretty")
    structlog.get_logger().info("hello world")
    out = capsys.readouterr().out
    assert "hello world" in out
