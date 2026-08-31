"""Real-server smoke tests: the official mcp-server-fetch through the tap.

Skipped automatically when mcp-server-fetch is not installed (CI runs it
only when the extra job installs it). These prove three things on a real
third-party server, not a fixture:

- wrap passes the protocol through untouched (initialize + tools/list),
- report computes a real tool surface price,
- replay with pacing reproduces the session with no fake differences.
"""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mcptap.analysis import analyze, load
from mcptap.replay import replay_session

REPO = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    shutil.which("mcp-server-fetch") is None,
    reason="mcp-server-fetch not installed (optional real-server smoke)",
)


def paced_fetch_session(out: Path) -> None:
    """Talk to mcp-server-fetch through the tap, waiting for each answer
    (real anyio servers exit on stdin EOF before draining a fire-hose)."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcptap", "wrap", "--out", str(out), "--", "mcp-server-fetch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        cwd=REPO,
    )
    assert proc.stdin is not None and proc.stdout is not None

    def send(obj: dict) -> None:
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def recv(want_id: int, timeout: float = 15.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                raise AssertionError("server closed stdout before answering")
            msg = json.loads(line)
            if msg.get("id") == want_id:
                return msg
        raise AssertionError(f"no response with id={want_id}")

    send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
          "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                     "clientInfo": {"name": "mcptap-smoke", "version": "0"}}})
    init = recv(1)
    assert init["result"]["serverInfo"]["name"] == "mcp-fetch"
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = recv(2)["result"]["tools"]
    assert [t["name"] for t in tools] == ["fetch"]
    proc.stdin.close()
    assert proc.wait(timeout=15) == 0


def test_real_wrap_and_report(tmp_path):
    session = tmp_path / "fetch.jsonl"
    paced_fetch_session(session)
    report = analyze(load(session))
    assert report["server"]["name"] == "mcp-fetch"
    surface = report["tool_surface"]
    assert surface["count"] == 1
    assert surface["tools"][0]["name"] == "fetch"
    assert surface["est_tokens"] > 100  # real descriptions are not tiny
    assert report["tool_calls"]["total"] == 0
    assert report["unanswered_requests"] == []
    assert report["lifecycle"]["crashed"] is False


def test_real_replay_finds_no_differences(tmp_path):
    session = tmp_path / "fetch.jsonl"
    paced_fetch_session(session)
    result = replay_session(
        session, ["mcp-server-fetch"], out_path=tmp_path / "replay.jsonl", timeout_s=60
    )
    assert result["differences"] == []
    assert result["report"]["tool_surface"]["count"] == 1
