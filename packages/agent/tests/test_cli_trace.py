"""Tests for the ``grackle trace`` CLI subcommand.

Covers:
- happy path (writes JSONL output)
- ``--max-events`` rejects non-positive values (I3)
- SCRIPT outside ``--root`` is rejected with a clear error (I5)
- ``--max-events`` cap is propagated to the tracer
- Phase 8.1: ``--stream + --output`` tee mode
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import subprocess
import sys
import time
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from grackle.cli import main

if TYPE_CHECKING:
    from pathlib import Path


def _write_simple_script(root: Path) -> Path:
    """Write a minimal traceable script to ``root/script.py`` and return its path."""
    root.mkdir(parents=True, exist_ok=True)
    script = root / "script.py"
    script.write_text(
        "def add(a, b):\n"
        "    return a + b\n"
        "\n"
        "def main() -> None:\n"
        "    add(1, 2)\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    main()\n",
        encoding="utf-8",
    )
    return script


def test_trace_writes_output_file(tmp_path: Path) -> None:
    script = _write_simple_script(tmp_path)
    out = tmp_path / "trace.jsonl"
    result = CliRunner().invoke(
        main,
        ["trace", str(script), "--root", str(tmp_path), "--output", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) > 0
    # Every line must be a valid JSON event with the required fields
    for raw in lines:
        e = json.loads(raw)
        assert "event" in e
        assert "node_id" in e


def test_trace_capture_values_flag_in_help() -> None:
    """``trace --help`` must document --capture-values (ADR-0025, chunk 10.2)."""
    result = CliRunner().invoke(main, ["trace", "--help"])
    assert result.exit_code == 0
    assert "--capture-values" in result.output
    assert "--no-redact" in result.output
    assert "--capture-first-n" in result.output


def test_trace_capture_values_emits_values_field(tmp_path: Path) -> None:
    script = _write_simple_script(tmp_path)
    out = tmp_path / "trace.jsonl"
    result = CliRunner().invoke(
        main,
        [
            "trace",
            str(script),
            "--root",
            str(tmp_path),
            "--output",
            str(out),
            "--capture-values",
        ],
    )
    assert result.exit_code == 0, result.output
    lines = [json.loads(raw) for raw in out.read_text(encoding="utf-8").splitlines()]
    assert any("values" in e for e in lines)


def test_trace_no_redact_flag_bypasses_redaction(tmp_path: Path) -> None:
    """``--no-redact`` must actually flow through to the tracer, not just parse."""
    root = tmp_path
    root.mkdir(parents=True, exist_ok=True)
    script = root / "script.py"
    script.write_text(
        "def login(username, password):\n    return username\n\nlogin('ada', password='s3cret')\n",
        encoding="utf-8",
    )
    out = tmp_path / "trace.jsonl"
    result = CliRunner().invoke(
        main,
        [
            "trace",
            str(script),
            "--root",
            str(root),
            "--output",
            str(out),
            "--capture-values",
            "--no-redact",
        ],
    )
    assert result.exit_code == 0, result.output
    lines = [json.loads(raw) for raw in out.read_text(encoding="utf-8").splitlines()]
    call = next(e for e in lines if e["event"] == "call" and e["node_id"].endswith(":login"))
    args_by_name = {a["name"]: a for a in call["values"]["args"]}
    assert args_by_name["password"]["repr"] == "'s3cret'"
    assert "redacted" not in args_by_name["password"]


def test_trace_password_redacted_by_default_via_cli(tmp_path: Path) -> None:
    """Without ``--no-redact``, a sensitive-named arg is redacted end-to-end through the CLI."""
    root = tmp_path
    root.mkdir(parents=True, exist_ok=True)
    script = root / "script.py"
    script.write_text(
        "def login(username, password):\n    return username\n\nlogin('ada', password='s3cret')\n",
        encoding="utf-8",
    )
    out = tmp_path / "trace.jsonl"
    result = CliRunner().invoke(
        main,
        [
            "trace",
            str(script),
            "--root",
            str(root),
            "--output",
            str(out),
            "--capture-values",
        ],
    )
    assert result.exit_code == 0, result.output
    lines = [json.loads(raw) for raw in out.read_text(encoding="utf-8").splitlines()]
    call = next(e for e in lines if e["event"] == "call" and e["node_id"].endswith(":login"))
    args_by_name = {a["name"]: a for a in call["values"]["args"]}
    assert args_by_name["password"]["repr"] == "<redacted>"
    assert args_by_name["password"]["redacted"] is True


def test_trace_max_value_len_flag_truncates_via_cli(tmp_path: Path) -> None:
    """``--max-value-len`` must actually bound the captured repr length."""
    root = tmp_path
    root.mkdir(parents=True, exist_ok=True)
    script = root / "script.py"
    script.write_text(
        "def take(s):\n    return s\n\ntake('x' * 500)\n",
        encoding="utf-8",
    )
    out = tmp_path / "trace.jsonl"
    result = CliRunner().invoke(
        main,
        [
            "trace",
            str(script),
            "--root",
            str(root),
            "--output",
            str(out),
            "--capture-values",
            "--max-value-len",
            "40",
        ],
    )
    assert result.exit_code == 0, result.output
    lines = [json.loads(raw) for raw in out.read_text(encoding="utf-8").splitlines()]
    call = next(e for e in lines if e["event"] == "call" and e["node_id"].endswith(":take"))
    arg = next(a for a in call["values"]["args"] if a["name"] == "s")
    assert arg["truncated"] is True
    assert len(arg["repr"]) <= 40


def test_trace_max_value_items_flag_is_honored_via_cli(tmp_path: Path) -> None:
    """``--max-value-items`` must actually reach the tracer, not just parse.

    Uses a 15-item list with ``--max-value-items 20`` (larger than the
    default of 10). The default alone would truncate a 15-item list
    (15 > 10); a value of 20 would not (15 <= 20). Asserting NOT truncated
    is the discriminating check — a regression that silently drops the flag
    and falls back to the default would still truncate, and this would fail.
    """
    root = tmp_path
    root.mkdir(parents=True, exist_ok=True)
    script = root / "script.py"
    script.write_text(
        "def take(items):\n    return items\n\ntake(list(range(15)))\n",
        encoding="utf-8",
    )
    out = tmp_path / "trace.jsonl"
    result = CliRunner().invoke(
        main,
        [
            "trace",
            str(script),
            "--root",
            str(root),
            "--output",
            str(out),
            "--capture-values",
            "--max-value-items",
            "20",
        ],
    )
    assert result.exit_code == 0, result.output
    lines = [json.loads(raw) for raw in out.read_text(encoding="utf-8").splitlines()]
    call = next(e for e in lines if e["event"] == "call" and e["node_id"].endswith(":take"))
    arg = next(a for a in call["values"]["args"] if a["name"] == "items")
    assert "truncated" not in arg
    assert arg["repr"] == "[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]"


def test_trace_max_value_depth_flag_is_honored_via_cli(tmp_path: Path) -> None:
    """``--max-value-depth`` must actually reach the tracer, not just parse.

    Uses ``[[1]]`` (2 levels of list nesting) with ``--max-value-depth 1``.
    The default of 3 would NOT truncate this value; depth 1 does. Asserting
    truncated is the discriminating check — a regression that silently
    drops the flag and falls back to the default would not truncate, and
    this would fail.
    """
    root = tmp_path
    root.mkdir(parents=True, exist_ok=True)
    script = root / "script.py"
    script.write_text(
        "def take(x):\n    return x\n\ntake([[1]])\n",
        encoding="utf-8",
    )
    out = tmp_path / "trace.jsonl"
    result = CliRunner().invoke(
        main,
        [
            "trace",
            str(script),
            "--root",
            str(root),
            "--output",
            str(out),
            "--capture-values",
            "--max-value-depth",
            "1",
        ],
    )
    assert result.exit_code == 0, result.output
    lines = [json.loads(raw) for raw in out.read_text(encoding="utf-8").splitlines()]
    call = next(e for e in lines if e["event"] == "call" and e["node_id"].endswith(":take"))
    arg = next(a for a in call["values"]["args"] if a["name"] == "x")
    assert arg["truncated"] is True
    assert arg["repr"] == "[[...]]"


def test_trace_capture_first_n_flag_bounds_capture_via_cli(tmp_path: Path) -> None:
    """``--capture-first-n`` must actually bound how many events capture values,
    while every call/return event is still emitted (never dropped)."""
    root = tmp_path
    root.mkdir(parents=True, exist_ok=True)
    script = root / "script.py"
    script.write_text(
        "def hot(i):\n    return i\n\nfor _n in range(60):\n    hot(_n)\n",
        encoding="utf-8",
    )
    out = tmp_path / "trace.jsonl"
    result = CliRunner().invoke(
        main,
        [
            "trace",
            str(script),
            "--root",
            str(root),
            "--output",
            str(out),
            "--capture-values",
            "--capture-first-n",
            "10",
        ],
    )
    assert result.exit_code == 0, result.output
    lines = [json.loads(raw) for raw in out.read_text(encoding="utf-8").splitlines()]
    calls = [e for e in lines if e["event"] == "call" and e["node_id"].endswith(":hot")]
    returns = [e for e in lines if e["event"] == "return" and e["node_id"].endswith(":hot")]
    assert len(calls) == 60
    assert len(returns) == 60
    total_captured = sum("values" in e for e in calls) + sum("values" in e for e in returns)
    assert total_captured == 10


def test_trace_default_omits_values_field(tmp_path: Path) -> None:
    """Without --capture-values, no event carries a 'values' key (byte-identical default)."""
    script = _write_simple_script(tmp_path)
    out = tmp_path / "trace.jsonl"
    result = CliRunner().invoke(
        main,
        ["trace", str(script), "--root", str(tmp_path), "--output", str(out)],
    )
    assert result.exit_code == 0, result.output
    lines = [json.loads(raw) for raw in out.read_text(encoding="utf-8").splitlines()]
    assert all("values" not in e for e in lines)


def test_trace_stdout(tmp_path: Path) -> None:
    script = _write_simple_script(tmp_path)
    result = CliRunner().invoke(main, ["trace", str(script), "--root", str(tmp_path)])
    assert result.exit_code == 0, result.output
    # First non-empty stdout line must be a JSON object
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert lines, "expected at least one event on stdout"
    json.loads(lines[0])


def test_trace_max_events_zero_rejected(tmp_path: Path) -> None:
    """``--max-events 0`` must fail with a usage error (I3 regression)."""
    script = _write_simple_script(tmp_path)
    result = CliRunner().invoke(
        main,
        ["trace", str(script), "--root", str(tmp_path), "--max-events", "0"],
    )
    assert result.exit_code != 0
    assert "0" in result.output


def test_trace_max_events_negative_rejected(tmp_path: Path) -> None:
    """``--max-events -1`` must fail with a usage error (I3 regression)."""
    script = _write_simple_script(tmp_path)
    result = CliRunner().invoke(
        main,
        ["trace", str(script), "--root", str(tmp_path), "--max-events", "-1"],
    )
    assert result.exit_code != 0


def test_trace_max_events_cap_propagates(tmp_path: Path) -> None:
    """Tracer must surface ``TraceCapExceeded`` as a click error."""
    script = _write_simple_script(tmp_path)
    result = CliRunner().invoke(
        main,
        ["trace", str(script), "--root", str(tmp_path), "--max-events", "1"],
    )
    # Cap of 1 is reached almost immediately on any non-trivial script
    assert result.exit_code != 0
    assert "cap" in result.output.lower()


def test_trace_script_outside_root_rejected(tmp_path: Path) -> None:
    """SCRIPT not under ROOT must be rejected with a clear UsageError (I5)."""
    # Two unrelated dirs
    root_dir = tmp_path / "project"
    outside_dir = tmp_path / "elsewhere"
    root_dir.mkdir()
    outside_dir.mkdir()
    # Script lives in outside_dir, not under root_dir
    script = _write_simple_script(outside_dir)

    result = CliRunner().invoke(main, ["trace", str(script), "--root", str(root_dir)])
    assert result.exit_code != 0, result.output
    assert "not inside" in result.output.lower() or "<unresolved>" in result.output


def test_trace_help_mentions_runpy_caveat() -> None:
    """The ``trace --help`` output should warn about sys.argv/cwd (I4)."""
    result = CliRunner().invoke(main, ["trace", "--help"])
    assert result.exit_code == 0
    # The note about runpy + sys.argv is part of the docstring
    assert "sys.argv" in result.output or "cwd" in result.output


def test_trace_help_mentions_connect_option() -> None:
    """``trace --help`` must document the --connect option."""
    result = CliRunner().invoke(main, ["trace", "--help"])
    assert result.exit_code == 0
    assert "--connect" in result.output


def test_serve_help_mentions_trace_source() -> None:
    """``serve --help`` must document the --trace-source option."""
    result = CliRunner().invoke(main, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--trace-source" in result.output


def test_serve_help_mentions_no_pace() -> None:
    """``serve --help`` must document the --no-pace flag."""
    result = CliRunner().invoke(main, ["serve", "--help"])
    assert result.exit_code == 0
    assert "--no-pace" in result.output


# ---------------------------------------------------------------------------
# Phase 7.2 — --stream flag
# ---------------------------------------------------------------------------


def test_trace_stream_flag_in_help() -> None:
    """``trace --help`` must document the --stream option."""
    result = CliRunner().invoke(main, ["trace", "--help"])
    assert result.exit_code == 0
    assert "--stream" in result.output


def test_trace_stream_without_connect_rejected(tmp_path: Path) -> None:
    """``--stream`` without ``--connect`` must fail with a UsageError."""
    script = _write_simple_script(tmp_path)
    result = CliRunner().invoke(
        main,
        ["trace", str(script), "--root", str(tmp_path), "--stream"],
    )
    assert result.exit_code != 0
    assert "--connect" in result.output or "connect" in result.output.lower()


def test_trace_no_pace_does_not_error_with_stream(tmp_path: Path) -> None:
    """``--stream --no-pace`` must not cause a usage error (--no-pace is a no-op)."""
    # We don't connect for real; just verify validation passes (will fail on connect).
    script = _write_simple_script(tmp_path)
    result = CliRunner().invoke(
        main,
        [
            "trace",
            str(script),
            "--root",
            str(tmp_path),
            "--stream",
            "--no-pace",
            "--connect",
            "ws://127.0.0.1:1",  # guaranteed unreachable
        ],
    )
    # Should fail on connection, not on argument validation.
    assert result.exit_code != 0
    assert "--output" not in result.output  # no usage error about --output


def test_trace_stream_connect_failure_surfaces_clean_error(tmp_path: Path) -> None:
    """Connection failure must produce a clean ClickException, not a traceback."""
    script = _write_simple_script(tmp_path)
    result = CliRunner().invoke(
        main,
        [
            "trace",
            str(script),
            "--root",
            str(tmp_path),
            "--stream",
            "--connect",
            "ws://127.0.0.1:1",  # port 1 — nothing listening
        ],
    )
    assert result.exit_code != 0
    # Must not be an unhandled exception (no traceback in output).
    assert "Traceback" not in result.output
    assert "Error" in result.output


# ---------------------------------------------------------------------------
# Phase 8.1 — --stream + --output tee mode
# ---------------------------------------------------------------------------


def test_trace_stream_with_output_accepted(tmp_path: Path) -> None:
    """``--stream + --output`` is now valid; previously rejected, now a tee.

    Uses an unreachable server so the test validates argument acceptance,
    not a live connection.  The failure must come from the connection
    attempt, not from a UsageError about --output.
    """
    script = _write_simple_script(tmp_path)
    out = tmp_path / "trace.jsonl"
    result = CliRunner().invoke(
        main,
        [
            "trace",
            str(script),
            "--root",
            str(tmp_path),
            "--stream",
            "--connect",
            "ws://127.0.0.1:1",
            "--output",
            str(out),
        ],
    )
    # Must fail on connection, not on --output argument validation.
    assert result.exit_code != 0
    assert "incompatible" not in result.output.lower()
    assert "Traceback" not in result.output


async def test_trace_stream_tee_writes_file(free_port: int, tmp_path: Path) -> None:
    """``--stream + --output`` writes a JSONL file and streams to server simultaneously.

    Verifies:
    - exit code 0
    - output file exists with valid JSONL events
    - server received ``trace_session_start`` and ``trace_session_end``
    - file event count equals events actually streamed
    """
    import json as _json

    from websockets.asyncio.client import connect as _ws_connect

    from grackle.server import serve as _serve

    root = tmp_path / "proj"
    root.mkdir()
    script = _write_simple_script(root)
    out = tmp_path / "tee.jsonl"
    url = f"ws://127.0.0.1:{free_port}"

    # Start server in live-attach mode.
    server_task = asyncio.create_task(_serve("127.0.0.1", free_port, root=root))
    await asyncio.sleep(0.05)

    # Consumer collects all trace messages until session_end.
    received: list[dict[str, object]] = []
    consumer_done = asyncio.Event()

    async def _consume() -> None:
        async with _ws_connect(url) as ws:
            await ws.send(_json.dumps({"id": "ping0", "type": "ping", "payload": {}}))
            async for raw in ws:
                msg = _json.loads(raw)
                received.append(msg)
                if msg["type"] == "trace_session_end":
                    consumer_done.set()
                    break

    consumer_task = asyncio.create_task(_consume())
    try:
        await asyncio.sleep(0.05)  # let consumer connect before CLI starts

        # Run CLI in a thread (CliRunner.invoke is synchronous).
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: CliRunner().invoke(
                main,
                [
                    "trace",
                    str(script),
                    "--root",
                    str(root),
                    "--stream",
                    "--connect",
                    url,
                    "--output",
                    str(out),
                ],
            ),
        )

        assert result.exit_code == 0, result.output
        assert "wrote" in result.output
        assert "streamed" in result.output

        # File must exist with valid events.
        assert out.exists()
        file_lines = out.read_text(encoding="utf-8").splitlines()
        assert len(file_lines) > 0
        for raw in file_lines:
            e = _json.loads(raw)
            assert "event" in e
            assert "node_id" in e

        # Wait for consumer to receive session_end (or time out).
        await asyncio.wait_for(consumer_done.wait(), timeout=5.0)

        types = [m["type"] for m in received]
        assert "trace_session_start" in types
        assert "trace_session_end" in types

        # File is lossless: captures all events including any the WS sender drops
        # under backpressure, so file count >= server-received count.
        streamed_count = sum(1 for m in received if m["type"] == "trace_event")
        assert len(file_lines) >= streamed_count
    finally:
        server_task.cancel()
        consumer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await server_task
        with contextlib.suppress(asyncio.CancelledError):
            await consumer_task


# ---------------------------------------------------------------------------
# Phase 12.0 — tee path through JsonlPartWriter (--stream + --output)
# ---------------------------------------------------------------------------


async def test_trace_stream_tee_write_failure_does_not_disrupt_stream(
    monkeypatch: pytest.MonkeyPatch, free_port: int, tmp_path: Path
) -> None:
    """A write failure mid-tee must not kill the live stream — the stream
    completes normally (write-then-send never lets a recording failure
    propagate into the hot path), but the CLI still exits non-zero to
    report the file problem after the fact."""
    import json as _json

    from websockets.asyncio.client import connect as _ws_connect

    from grackle.python_runtime.writer import JsonlPartWriter
    from grackle.server import serve as _serve

    call_count = {"n": 0}
    real_write = JsonlPartWriter.write

    def _flaky_write(self: JsonlPartWriter, event: object) -> None:
        call_count["n"] += 1
        if call_count["n"] == 2:
            self.broken = True
            raise OSError("simulated disk failure")
        real_write(self, event)  # type: ignore[arg-type]

    monkeypatch.setattr(JsonlPartWriter, "write", _flaky_write)

    root = tmp_path / "proj"
    root.mkdir()
    script = _write_simple_script(root)
    out = tmp_path / "tee.jsonl"
    url = f"ws://127.0.0.1:{free_port}"

    server_task = asyncio.create_task(_serve("127.0.0.1", free_port, root=root))
    await asyncio.sleep(0.05)

    received: list[dict[str, object]] = []
    consumer_done = asyncio.Event()

    async def _consume() -> None:
        async with _ws_connect(url) as ws:
            await ws.send(_json.dumps({"id": "ping0", "type": "ping", "payload": {}}))
            async for raw in ws:
                msg = _json.loads(raw)
                received.append(msg)
                if msg["type"] == "trace_session_end":
                    consumer_done.set()
                    break

    consumer_task = asyncio.create_task(_consume())
    try:
        await asyncio.sleep(0.05)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: CliRunner().invoke(
                main,
                [
                    "trace",
                    str(script),
                    "--root",
                    str(root),
                    "--stream",
                    "--connect",
                    url,
                    "--output",
                    str(out),
                ],
            ),
        )

        # The live stream itself completed normally...
        await asyncio.wait_for(consumer_done.wait(), timeout=5.0)
        types = [m["type"] for m in received]
        assert "trace_session_end" in types

        # ...but the CLI reports the write failure with a non-zero exit,
        # never a bare traceback.
        assert result.exit_code != 0
        assert "Traceback" not in result.output
    finally:
        server_task.cancel()
        consumer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await server_task
        with contextlib.suppress(asyncio.CancelledError):
            await consumer_task


async def test_trace_stream_tee_cap_and_writer_failure_both_reported(
    monkeypatch: pytest.MonkeyPatch, free_port: int, tmp_path: Path
) -> None:
    """When BOTH the adapter (cap exceeded) and the writer (finalize
    failure) fail in the same tee session, the combined error must mention
    both — neither is silently dropped in favor of the other."""
    import json as _json

    from websockets.asyncio.client import connect as _ws_connect

    from grackle.python_runtime.writer import JsonlPartWriter
    from grackle.server import serve as _serve

    def _flaky_finalize(self: JsonlPartWriter) -> None:
        raise OSError("simulated disk full at finalize")

    monkeypatch.setattr(JsonlPartWriter, "finalize", _flaky_finalize)

    root = tmp_path / "proj"
    root.mkdir()
    script = root / "script.py"
    script.write_text(
        "def hot(i):\n    return i\n\nfor _n in range(50):\n    hot(_n)\n",
        encoding="utf-8",
    )
    out = tmp_path / "tee.jsonl"
    url = f"ws://127.0.0.1:{free_port}"

    server_task = asyncio.create_task(_serve("127.0.0.1", free_port, root=root))
    await asyncio.sleep(0.05)

    # The consumer just needs to keep the connection alive and draining —
    # this test's assertions are entirely CLI-side (exit code + combined
    # error message), so it does not wait for any particular server message.
    async def _consume() -> None:
        async with _ws_connect(url) as ws:
            await ws.send(_json.dumps({"id": "ping0", "type": "ping", "payload": {}}))
            async for _raw in ws:
                pass

    consumer_task = asyncio.create_task(_consume())
    try:
        await asyncio.sleep(0.05)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: CliRunner().invoke(
                main,
                [
                    "trace",
                    str(script),
                    "--root",
                    str(root),
                    "--stream",
                    "--connect",
                    url,
                    "--output",
                    str(out),
                    "--max-events",
                    "3",
                ],
            ),
        )

        assert result.exit_code != 0
        output_lower = result.output.lower()
        assert "cap" in output_lower  # the held adapter-side error
        assert "disk full" in output_lower  # the writer-side error — neither dropped
    finally:
        server_task.cancel()
        consumer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await server_task
        with contextlib.suppress(asyncio.CancelledError):
            await consumer_task


async def test_trace_stream_tee_lossless_under_real_backpressure(
    monkeypatch: pytest.MonkeyPatch, free_port: int, tmp_path: Path
) -> None:
    """Tee losslessness holds under REAL WS backpressure drops (mirrors the
    precedent in test_stream_sender.py's test_sender_backpressure_bounds_memory):
    the file has every event (write precedes send), while the WS stream may
    have fewer — the file is a strict superset, not just >=."""
    monkeypatch.setenv("GRACKLE_STREAM_MAX_INFLIGHT", "10")

    import json as _json

    from websockets.asyncio.client import connect as _ws_connect

    from grackle.server import serve as _serve

    root = tmp_path / "proj"
    root.mkdir()
    script = root / "script.py"
    script.write_text(
        "def hot(i):\n    return i\n\nfor _n in range(600):\n    hot(_n)\n",
        encoding="utf-8",
    )
    out = tmp_path / "tee.jsonl"
    url = f"ws://127.0.0.1:{free_port}"

    server_task = asyncio.create_task(_serve("127.0.0.1", free_port, root=root))
    await asyncio.sleep(0.05)

    received: list[dict[str, object]] = []
    consumer_done = asyncio.Event()

    async def _consume() -> None:
        async with _ws_connect(url) as ws:
            await ws.send(_json.dumps({"id": "ping0", "type": "ping", "payload": {}}))
            async for raw in ws:
                msg = _json.loads(raw)
                received.append(msg)
                if msg["type"] == "trace_session_end":
                    consumer_done.set()
                    break

    consumer_task = asyncio.create_task(_consume())
    try:
        await asyncio.sleep(0.05)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: CliRunner().invoke(
                main,
                [
                    "trace",
                    str(script),
                    "--root",
                    str(root),
                    "--stream",
                    "--connect",
                    url,
                    "--output",
                    str(out),
                ],
            ),
        )

        assert result.exit_code == 0, result.output
        assert out.exists()
        file_lines = out.read_text(encoding="utf-8").splitlines()

        await asyncio.wait_for(consumer_done.wait(), timeout=10.0)

        streamed_count = sum(1 for m in received if m["type"] == "trace_event")
        assert len(file_lines) > 0
        # The file is a strict superset under real drops — the discriminating
        # assertion (>=  would also pass with zero drops, which is not what
        # this test exists to prove).
        assert len(file_lines) > streamed_count
    finally:
        server_task.cancel()
        consumer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await server_task
        with contextlib.suppress(asyncio.CancelledError):
            await consumer_task


def test_trace_stream_tee_refuses_to_clobber_an_existing_part(tmp_path: Path) -> None:
    """The tee path refuses an existing .part too — and refuses it BEFORE the
    sender starts, so no server is needed here and none is contacted.

    Same rule as the plain -o path: a .part holds a prior run's salvaged
    events and is never deleted on the user's behalf. Opening the file first
    also means an unusable -o path can never leave a live sender thread and
    WebSocket connection behind.
    """
    root = tmp_path / "proj"
    root.mkdir()
    script = _write_simple_script(root)
    out = tmp_path / "tee.jsonl"
    existing_part = tmp_path / "tee.jsonl.part"
    salvaged = b'{"event": "call", "node_id": "prior_run.py:f"}\n'
    existing_part.write_bytes(salvaged)

    result = CliRunner().invoke(
        main,
        [
            "trace",
            str(script),
            "--root",
            str(root),
            "--stream",
            "--connect",
            "ws://127.0.0.1:1",  # never reached — the refusal precedes the connect
            "--output",
            str(out),
        ],
    )
    assert result.exit_code != 0
    assert "already exists" in result.output, result.output
    assert "could not connect" not in result.output, result.output
    assert existing_part.read_bytes() == salvaged
    assert not out.exists()


async def test_trace_stream_tee_trivial_session_leaves_no_stray_part(
    free_port: int, tmp_path: Path
) -> None:
    """A --stream --output session over a trivial script (no function calls,
    at most the module-level frame) must finalize cleanly — no stray .part
    left behind — even at the minimum possible event count."""
    import json as _json

    from websockets.asyncio.client import connect as _ws_connect

    from grackle.server import serve as _serve

    root = tmp_path / "proj"
    root.mkdir()
    script = root / "script.py"
    script.write_text("", encoding="utf-8")
    out = tmp_path / "tee.jsonl"
    url = f"ws://127.0.0.1:{free_port}"

    server_task = asyncio.create_task(_serve("127.0.0.1", free_port, root=root))
    await asyncio.sleep(0.05)

    consumer_done = asyncio.Event()

    async def _consume() -> None:
        async with _ws_connect(url) as ws:
            await ws.send(_json.dumps({"id": "ping0", "type": "ping", "payload": {}}))
            async for raw in ws:
                msg = _json.loads(raw)
                if msg["type"] == "trace_session_end":
                    consumer_done.set()
                    break

    consumer_task = asyncio.create_task(_consume())
    try:
        await asyncio.sleep(0.05)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: CliRunner().invoke(
                main,
                [
                    "trace",
                    str(script),
                    "--root",
                    str(root),
                    "--stream",
                    "--connect",
                    url,
                    "--output",
                    str(out),
                ],
            ),
        )

        assert result.exit_code == 0, result.output
        assert out.exists()
        assert not (tmp_path / "tee.jsonl.part").exists()

        await asyncio.wait_for(consumer_done.wait(), timeout=5.0)
    finally:
        server_task.cancel()
        consumer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await server_task
        with contextlib.suppress(asyncio.CancelledError):
            await consumer_task


# ---------------------------------------------------------------------------
# Phase 12.0 — incremental trace persistence (-o for streaming_trace_parity
# adapters, i.e. Python today)
# ---------------------------------------------------------------------------


def test_trace_max_events_cap_with_output_finalizes_file(tmp_path: Path) -> None:
    """--max-events with --output must still write the captured prefix
    (D12.0.6): the incremental path finalizes the file BEFORE re-raising the
    cap error, unlike the old buffered path which lost everything on cap."""
    script = tmp_path / "script.py"
    script.write_text(
        "def hot(i):\n    return i\n\nfor _n in range(50):\n    hot(_n)\n",
        encoding="utf-8",
    )
    out = tmp_path / "trace.jsonl"
    result = CliRunner().invoke(
        main,
        [
            "trace",
            str(script),
            "--root",
            str(tmp_path),
            "--output",
            str(out),
            "--max-events",
            "3",
        ],
    )
    assert result.exit_code != 0
    assert "cap" in result.output.lower()
    assert out.exists()
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    for line in lines:
        json.loads(line)


def test_trace_cap_and_writer_failure_both_reported(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When BOTH the adapter (cap exceeded) and the writer (finalize
    failure) fail in the same run, the combined error must mention both —
    neither is silently dropped in favor of the other."""
    from grackle.python_runtime.writer import JsonlPartWriter

    def _flaky_finalize(self: JsonlPartWriter) -> None:
        raise OSError("simulated disk full at finalize")

    monkeypatch.setattr(JsonlPartWriter, "finalize", _flaky_finalize)

    script = tmp_path / "script.py"
    script.write_text(
        "def hot(i):\n    return i\n\nfor _n in range(50):\n    hot(_n)\n",
        encoding="utf-8",
    )
    out = tmp_path / "trace.jsonl"
    result = CliRunner().invoke(
        main,
        [
            "trace",
            str(script),
            "--root",
            str(tmp_path),
            "--output",
            str(out),
            "--max-events",
            "3",
        ],
    )
    assert result.exit_code != 0
    output_lower = result.output.lower()
    assert "cap" in output_lower  # the held adapter-side error
    assert "disk full" in output_lower  # the writer-side error — neither dropped
    # finalize() failed, so the .part is deliberately KEPT (never renamed) —
    # the CLI's keep-on-failure policy (D12.0.1), not RecordingSink's discard.
    assert (tmp_path / "trace.jsonl.part").exists()


def test_trace_cap_does_not_mask_a_broken_mid_run_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A write that fails MID-RUN must be reported even when --max-events also
    fires and the traced script swallowed the propagated error.

    finalize() succeeds here (it salvages the prefix), so the only signal that
    events were lost is ``writer.broken``. The incremental path originally
    checked it on the tee side only, so this exact combination — swallowed
    write error + cap — printed "wrote N events" and reported the cap alone,
    never telling the user the file was short.
    """
    from grackle.python_runtime.writer import JsonlPartWriter

    class _FailsAfter:
        """Wraps the real file object so JsonlPartWriter.write's own failure
        handling runs — patching write() wholesale would skip the very
        ``broken`` bookkeeping under test."""

        def __init__(self, real: object, fail_at: int) -> None:
            self._real = real
            self._fail_at = fail_at
            self._n = 0

        def write(self, data: bytes) -> int:
            self._n += 1
            if self._n >= self._fail_at:
                raise OSError("simulated disk full mid-run")
            return int(self._real.write(data))  # type: ignore[attr-defined]

        def truncate(self, size: int | None = None) -> int:
            return int(self._real.truncate(size))  # type: ignore[attr-defined]

        def close(self) -> None:
            self._real.close()  # type: ignore[attr-defined]

    real_init = JsonlPartWriter.__init__

    def _flaky_init(self: JsonlPartWriter, final_path: Path) -> None:
        real_init(self, final_path)
        self._f = _FailsAfter(self._f, 2)  # type: ignore[assignment]

    monkeypatch.setattr(JsonlPartWriter, "__init__", _flaky_init)

    script = tmp_path / "script.py"
    # The bare `except Exception: pass` is the point — the traced program
    # absorbs the injected sink error, so the run continues to the cap.
    script.write_text(
        "def hot(i):\n"
        "    try:\n"
        "        return i\n"
        "    except Exception:\n"
        "        pass\n"
        "\n"
        "for _n in range(50):\n"
        "    try:\n"
        "        hot(_n)\n"
        "    except Exception:\n"
        "        pass\n",
        encoding="utf-8",
    )
    out = tmp_path / "trace.jsonl"
    result = CliRunner().invoke(
        main,
        [
            "trace",
            str(script),
            "--root",
            str(tmp_path),
            "--output",
            str(out),
            "--max-events",
            "6",
        ],
    )
    assert result.exit_code != 0
    assert "a write failed mid-stream" in result.output, result.output


def test_trace_write_failure_does_not_perturb_the_traced_program(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A grackle disk error must never surface inside the traced program.

    ``sys.monitoring`` propagates an exception raised by a callback into the
    frame being monitored, so a sink that lets a write error escape (a) aborts
    the program mid-run and (b) fires whatever ``except``/``finally`` is live
    in the user's call stack — rollbacks, retries and alerts really execute,
    because grackle's disk filled up. The sink therefore swallows and reports
    via ``writer.broken`` instead, exactly as the ``--stream`` tee sink does.

    The event order here is deterministic — module call, ``main`` call,
    ``work`` call, ``inner`` call — so failing the 4th write lands the error
    while ``work`` is inside its ``try``, which is what makes the handler
    assertion discriminating. Failing on ``work``'s own call event instead
    would land *before* the ``try`` is entered and prove nothing.
    """
    from grackle.python_runtime.writer import JsonlPartWriter

    class _FailsAfter:
        def __init__(self, real: object, fail_at: int) -> None:
            self._real = real
            self._fail_at = fail_at
            self._n = 0

        def write(self, data: bytes) -> int:
            self._n += 1
            if self._n >= self._fail_at:
                raise OSError("simulated disk full mid-run")
            return int(self._real.write(data))  # type: ignore[attr-defined]

        def truncate(self, size: int | None = None) -> int:
            return int(self._real.truncate(size))  # type: ignore[attr-defined]

        def close(self) -> None:
            self._real.close()  # type: ignore[attr-defined]

    real_init = JsonlPartWriter.__init__

    def _flaky_init(self: JsonlPartWriter, final_path: Path) -> None:
        real_init(self, final_path)
        # 4th event == inner's call, i.e. while work is inside its try.
        self._f = _FailsAfter(self._f, 4)  # type: ignore[assignment]

    monkeypatch.setattr(JsonlPartWriter, "__init__", _flaky_init)

    root = tmp_path / "proj"
    root.mkdir()
    handler_marker = tmp_path / "handler-fired"
    completed_marker = tmp_path / "ran-to-completion"
    script = root / "script.py"
    script.write_text(
        "import pathlib\n"
        "\n"
        "def inner(i):\n"
        "    return i + 1\n"
        "\n"
        "def work(i):\n"
        "    try:\n"
        "        return inner(i) * 2\n"
        "    except BaseException:\n"
        f"        pathlib.Path({str(handler_marker)!r}).write_text('perturbed')\n"
        "        raise\n"
        "\n"
        "def main():\n"
        "    total = 0\n"
        "    for n in range(30):\n"
        "        total += work(n)\n"
        "    return total\n"
        "\n"
        "main()\n"
        f"pathlib.Path({str(completed_marker)!r}).write_text('done')\n",
        encoding="utf-8",
    )
    out = tmp_path / "trace.jsonl"
    result = CliRunner().invoke(
        main,
        ["trace", str(script), "--root", str(root), "--output", str(out)],
    )

    # The traced program ran to completion, untouched...
    assert not handler_marker.exists(), "grackle's write error reached the traced program"
    assert completed_marker.exists(), "grackle's write error aborted the traced program"
    # ...and the failure is still reported, structurally, with the prefix kept.
    assert result.exit_code != 0
    assert "a write failed mid-stream" in result.output, result.output
    assert out.exists()


def test_trace_output_survives_a_traced_script_that_chdirs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A relative -o must still finalize when the traced script calls os.chdir().

    The Python tracer runs the script IN-PROCESS via runpy, so its chdir moves
    the agent's own cwd between the .part being opened and finalize()'s
    rename. Unless the writer pinned both paths at construction, the rename
    resolves against the NEW directory and fails, stranding the events under
    the original cwd with no final file.
    """
    root = tmp_path / "proj"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    script = root / "script.py"
    script.write_text(
        "import os\n"
        "\n"
        "def work(i):\n"
        "    return i * 2\n"
        "\n"
        "for _n in range(3):\n"
        "    work(_n)\n"
        f"os.chdir({str(elsewhere)!r})\n"
        "for _n in range(3):\n"
        "    work(_n)\n",
        encoding="utf-8",
    )

    rundir = tmp_path / "rundir"
    rundir.mkdir()
    monkeypatch.chdir(rundir)

    result = CliRunner().invoke(
        main,
        ["trace", str(script), "--root", str(root), "--output", "out.jsonl"],
    )
    assert result.exit_code == 0, result.output
    final = rundir / "out.jsonl"
    assert final.exists(), "finalize must land in the cwd the user launched from"
    assert not (rundir / "out.jsonl.part").exists()
    assert not (elsewhere / "out.jsonl").exists(), "must not follow the script's chdir"
    lines = final.read_text(encoding="utf-8").splitlines()
    assert len(lines) > 0
    for line in lines:
        json.loads(line)


def test_trace_stream_tee_unwritable_output_fails_cleanly(tmp_path: Path) -> None:
    """An unopenable --output must be a clean CLI error, not a traceback.

    click.Path(writable=True) does not validate a nonexistent parent, so the
    exclusive-create open is the first thing to fail. It is deliberately
    attempted BEFORE the sender starts, so this path also cannot leave a live
    sender thread and WebSocket connection behind (there is no server here —
    if the open were attempted after sender.start(), the connect error would
    mask the real problem).
    """
    root = tmp_path / "proj"
    root.mkdir()
    script = _write_simple_script(root)
    missing_dir = tmp_path / "nope" / "deeper"
    result = CliRunner().invoke(
        main,
        [
            "trace",
            str(script),
            "--root",
            str(root),
            "--output",
            str(missing_dir / "trace.jsonl"),
            "--stream",
            "--connect",
            "ws://127.0.0.1:1",
        ],
    )
    assert result.exit_code != 0
    assert "could not open" in result.output, result.output
    # A clean ClickException, never a bubbled-up OSError.
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception


def test_trace_keyboard_interrupt_mid_run_still_writes_events_so_far(tmp_path: Path) -> None:
    """A KeyboardInterrupt raised BY THE TRACED SCRIPT must not lose events
    already written — Tracer.run() catches BaseException and the incremental
    writer already has everything on disk by the time it propagates (D12.0.9)."""
    script = tmp_path / "script.py"
    script.write_text(
        "def hot(i):\n"
        "    return i\n"
        "\n"
        "for _n in range(5):\n"
        "    hot(_n)\n"
        "    if _n == 2:\n"
        "        raise KeyboardInterrupt\n",
        encoding="utf-8",
    )
    out = tmp_path / "trace.jsonl"
    result = CliRunner().invoke(
        main,
        ["trace", str(script), "--root", str(tmp_path), "--output", str(out)],
    )
    assert result.exit_code == 0, result.output
    assert out.exists()
    assert not (tmp_path / "trace.jsonl.part").exists()
    events = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    hot_calls = [e for e in events if e["event"] == "call" and e["node_id"].endswith(":hot")]
    # hot(0), hot(1), hot(2) all ran before the raise on the _n == 2 iteration.
    assert len(hot_calls) == 3


def test_trace_output_refuses_to_clobber_an_existing_part(tmp_path: Path) -> None:
    """An existing .part is REFUSED, never silently deleted.

    A .part left by a killed run holds that run's salvaged events — the whole
    product of Phase 12.0. Clearing it on the next run would destroy exactly
    that data the moment the user reflexively re-runs the command that died.
    (It is also how two concurrent traces to one -o path corrupt each other:
    POSIX unlink succeeds on a file another process still holds open.)
    """
    script = _write_simple_script(tmp_path)
    out = tmp_path / "trace.jsonl"
    existing_part = tmp_path / "trace.jsonl.part"
    salvaged = b'{"event": "call", "node_id": "prior_run.py:f"}\n'
    existing_part.write_bytes(salvaged)

    result = CliRunner().invoke(
        main,
        ["trace", str(script), "--root", str(tmp_path), "--output", str(out)],
    )
    assert result.exit_code != 0
    assert "already exists" in result.output, result.output
    # The prior run's events are untouched, and no output file was produced.
    assert existing_part.read_bytes() == salvaged
    assert not out.exists()


async def test_trace_output_with_connect_replays_from_written_file(
    free_port: int, tmp_path: Path
) -> None:
    """-o + --connect (no --stream) must replay from the FINALIZED file — the
    incremental path retains nothing in memory, so the replayed count read
    back via read_jsonl(output) must equal the file's line count."""
    import json as _json

    from websockets.asyncio.client import connect as _ws_connect

    from grackle.server import serve as _serve

    root = tmp_path / "proj"
    root.mkdir()
    script = _write_simple_script(root)
    out = tmp_path / "trace.jsonl"
    url = f"ws://127.0.0.1:{free_port}"

    server_task = asyncio.create_task(_serve("127.0.0.1", free_port, root=root))
    await asyncio.sleep(0.05)

    received: list[dict[str, object]] = []
    consumer_done = asyncio.Event()

    async def _consume() -> None:
        async with _ws_connect(url) as ws:
            await ws.send(_json.dumps({"id": "ping0", "type": "ping", "payload": {}}))
            async for raw in ws:
                msg = _json.loads(raw)
                received.append(msg)
                if msg["type"] == "trace_session_end":
                    consumer_done.set()
                    break

    consumer_task = asyncio.create_task(_consume())
    try:
        await asyncio.sleep(0.05)

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: CliRunner().invoke(
                main,
                [
                    "trace",
                    str(script),
                    "--root",
                    str(root),
                    "--output",
                    str(out),
                    "--connect",
                    url,
                    "--no-pace",
                ],
            ),
        )

        assert result.exit_code == 0, result.output
        assert out.exists()
        file_lines = out.read_text(encoding="utf-8").splitlines()

        await asyncio.wait_for(consumer_done.wait(), timeout=5.0)

        streamed_count = sum(1 for m in received if m["type"] == "trace_event")
        # No backpressure/dropping on this reliable, awaited-send path
        # (unlike --stream's TraceStreamSender) — must be exact, not >=.
        assert streamed_count == len(file_lines)
    finally:
        server_task.cancel()
        consumer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await server_task
        with contextlib.suppress(asyncio.CancelledError):
            await consumer_task


def test_trace_streaming_writes_incrementally_as_events_arrive(tmp_path: Path) -> None:
    """A spy sink wrapping JsonlPartWriter.write must see the writer's count
    grow monotonically DURING the trace (proving persistence is per-event at
    the object level, not buffered-until-the-end) and the .part file must
    already hold real bytes on disk by the time the run completes — i.e.
    before finalize()'s close() forces a flush. (A small handful of events
    can sit in Python's internal write buffer — typically a few KB — so this
    is checked once after enough events have definitely crossed that buffer,
    not at every small checkpoint, which would be flaky by construction.)"""
    import grackle  # noqa: F401 — side-effect import triggers registration
    from grackle.adapters.base import TraceEvent, TraceOptions
    from grackle.python_runtime.adapter import PythonRuntimeAdapter
    from grackle.python_runtime.writer import JsonlPartWriter

    root = tmp_path
    script = root / "script.py"
    script.write_text(
        "def hot(i):\n    return i\n\nfor _n in range(500):\n    hot(_n)\n",
        encoding="utf-8",
    )
    out = root / "trace.jsonl"
    writer = JsonlPartWriter(out)
    part = writer.part_path

    counts_seen: list[int] = []

    def _spy_sink(event: TraceEvent) -> None:
        writer.write(event)
        if writer.count % 100 == 0:
            counts_seen.append(writer.count)
            assert part.exists()

    adapter = PythonRuntimeAdapter()
    adapter.trace_streaming(script, root, TraceOptions(), _spy_sink)

    # ~1000 call+return events at this point (500 hot() calls, 2 events
    # each) comfortably exceed a default ~8KB write buffer several times
    # over, so real bytes must already be on disk before finalize().
    assert part.stat().st_size > 0

    writer.finalize()

    assert counts_seen == sorted(counts_seen)  # observed monotonically non-decreasing
    assert len(counts_seen) >= 2  # growth observed at more than one checkpoint
    assert out.exists()


def test_trace_kill_mid_run_keeps_events_written_so_far(tmp_path: Path) -> None:
    """The headline Phase 12.0 test: SIGKILL of the tracing PROCESS (not the
    traced script) must keep every fully-flushed event on disk. This test
    FAILS against the pre-12.0 buffered -o path, which loses everything on a
    mid-run process kill (the whole run was held in memory) — that is the
    regression this chunk fixes.
    """
    root = tmp_path
    marker = root / "marker"
    script = root / "script.py"
    script.write_text(
        "import pathlib\n"
        "import time\n"
        "\n"
        "def hot(i):\n"
        "    return i\n"
        "\n"
        "for _n in range(3000):\n"
        "    hot(_n)\n"
        "    if _n == 1500:\n"
        f"        pathlib.Path({str(marker)!r}).write_text('go', encoding='utf-8')\n"
        "\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    out = root / "trace.jsonl"

    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from grackle.cli import main; main()",
            "trace",
            str(script),
            "--root",
            str(root),
            "--output",
            str(out),
        ],
        cwd=root,
    )
    try:
        deadline = time.monotonic() + 30.0
        while not marker.exists():
            if time.monotonic() > deadline:
                proc.kill()
                proc.wait(timeout=10)
                pytest.fail("subprocess never reached the marker — trace did not run")
            time.sleep(0.02)
        proc.kill()
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)

    part = root / "trace.jsonl.part"
    assert not out.exists()
    assert part.exists()
    raw = part.read_text(encoding="utf-8")
    assert raw  # non-empty: SIGKILL did not lose everything

    lines = raw.split("\n")
    # The trailing element is either "" (clean trailing newline) or a torn
    # partial line if the kill landed exactly mid-write — either way, drop
    # it and require every line BEFORE it to be complete, valid JSON. This
    # is what "kept events written so far" means without asserting an
    # exact, timing-dependent count.
    complete_lines = lines[:-1]
    assert len(complete_lines) > 0
    for line in complete_lines:
        json.loads(line)
