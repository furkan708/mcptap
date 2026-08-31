"""End-to-end tests: run `mcptap wrap` around the fake server and check
both the passthrough protocol behaviour and the recorded session file."""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FAKE = REPO / "tests" / "fake_server.py"

SESSION_LINES = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "add", "arguments": {"a": 20, "b": 22}}},
    {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "slow_mul", "arguments": {"a": 6, "b": 7}}},
    {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "send_email", "arguments": {"to": "x@y.z"}}},
    {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "boom", "arguments": {}}},
    {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "lying_label", "arguments": {}}},
]


def run_wrap(tmp_path: Path, toolset: str | None = None) -> tuple[str, Path]:
    session = tmp_path / f"session{('-' + toolset) if toolset else ''}.jsonl"
    stdin = "".join(json.dumps(m) + "\n" for m in SESSION_LINES)
    env = dict(os.environ)
    if toolset:
        env["FAKE_TOOLSET"] = toolset
    proc = subprocess.run(
        [sys.executable, "-m", "mcptap", "wrap", "--out", str(session), "--", sys.executable, str(FAKE)],
        input=stdin,
        check=False,
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=30,
        env=env,
    )
    return proc.stdout, session


def test_wrap_passes_protocol_through(tmp_path):
    stdout, _ = run_wrap(tmp_path)
    responses = [json.loads(line) for line in stdout.splitlines() if line.strip()]
    ids = [r.get("id") for r in responses]
    assert ids == [1, 2, 3, 4, 5, 6, 7], f"expected all seven responses, got {ids}"
    assert responses[0]["result"]["serverInfo"]["name"] == "fake-math"
    assert responses[2]["result"]["content"][0]["text"] == "42"  # add(20, 22)


def test_wrap_records_both_directions_and_lifecycle(tmp_path):
    _, session = run_wrap(tmp_path)
    records = [json.loads(line) for line in session.read_text().splitlines() if line.strip()]
    events = [r["event"] for r in records if "event" in r]
    assert events[0] == "start"
    assert "exit" in events
    c2s = [r for r in records if r.get("dir") == "c2s"]
    s2c = [r for r in records if r.get("dir") == "s2c"]
    assert len(c2s) == 8  # 8 client lines (incl. notification)
    assert len(s2c) == 7  # 7 responses
    exit_rec = next(r for r in records if r.get("event") == "exit")
    assert exit_rec["code"] == 0
