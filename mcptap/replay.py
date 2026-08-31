"""Replay a recorded session against a server: regression tests for MCP
wiring with zero test infrastructure.

The recorded client lines are re-sent verbatim (in order, notifications
included) to a fresh server process — but *paced*: after each request that
expects a response, the replay waits for that response before sending the
next line. Real servers (anyio-based ones like mcp-server-fetch) shut down
on stdin EOF before draining their queue, so a fire-hose replay would kill
them mid-script and fake a regression.

The exchange is recorded as a new session and compared with the original
report. Whatever changed — tool surface, token price, error semantics,
exit code — is reported as a structured difference, same shape as
`mcptap diff`.
"""

from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from .analysis import analyze, load
from .diff import compare_reports
from .recorder import SessionWriter, default_session_path

RESPONSE_TIMEOUT_S = 10.0


def _client_lines(records: list[dict[str, Any]]) -> list[str]:
    lines = []
    for record in records:
        if record.get("dir") != "c2s":
            continue
        data = record.get("data")
        if isinstance(data, dict) and "raw" in data and "jsonrpc" not in data:
            lines.append(str(data["raw"]))  # unparseable then — replay raw
        else:
            lines.append(json.dumps(data, ensure_ascii=False))
    return lines


def replay_session(
    session_path: Path,
    argv: list[str],
    out_path: Path | None = None,
    env: dict[str, str] | None = None,
    timeout_s: float = 60.0,
    pace: bool = True,
) -> dict[str, Any]:
    """Re-send the recorded client script to argv; return the new report
    plus the differences against the original session."""
    original = analyze(load(session_path))
    lines = _client_lines(load(session_path))

    out = out_path or default_session_path(argv).with_name(
        default_session_path(argv).stem + "-replay.jsonl"
    )
    writer = SessionWriter(out)
    writer.event("replay_start", argv=argv, source=str(session_path), lines=len(lines), pace=pace)

    child = subprocess.Popen(  # the operator's own command — that is the product
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,
        bufsize=1,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    wire: queue.Queue[str | None] = queue.Queue()

    def pump() -> None:
        assert child.stdout is not None
        for line in child.stdout:
            wire.put(line)
        wire.put(None)  # stdout closed
        writer.event("server_stdout_closed")

    pumper = threading.Thread(target=pump, daemon=True)
    pumper.start()

    def record_and_echo(line: str) -> None:
        writer.message("s2c", line)
        sys.stdout.write(line)  # let the operator see the server answer live
        sys.stdout.flush()

    def drain(seconds: float) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            try:
                line = wire.get(timeout=max(0.05, deadline - time.time()))
            except queue.Empty:
                return
            if line is None:
                return
            record_and_echo(line)

    def await_response(want_id: Any, deadline: float) -> None:
        """Drain lines until `want_id` answers or the deadline hits."""
        while time.time() < deadline:
            try:
                line = wire.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                return
            if line is None:
                return
            record_and_echo(line)
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("id") == want_id:
                return

    assert child.stdin is not None
    try:
        for line in lines:
            payload = line if line.endswith("\n") else line + "\n"
            try:
                child.stdin.write(payload)
                child.stdin.flush()
            except (BrokenPipeError, ValueError):
                writer.event("server_stdin_closed")
                break
            writer.message("c2s", payload)
            if pace:
                try:
                    msg = json.loads(payload)
                except ValueError:
                    continue
                if isinstance(msg, dict) and "id" in msg and "method" in msg:
                    await_response(msg["id"], time.time() + RESPONSE_TIMEOUT_S)
        child.stdin.close()
    except BrokenPipeError:
        writer.event("server_stdin_closed")

    try:
        code = child.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        child.terminate()
        code = child.wait()
        writer.event("replay_timeout")
    # drain anything the server said on its way out
    drain(1.0)
    writer.event("exit", code=code)
    writer.close()

    report = analyze(load(out))
    return {
        "new_session": out,
        "report": report,
        "differences": compare_reports(original, report),
    }
