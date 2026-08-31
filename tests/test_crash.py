"""Crash detection: a server that dies mid-session must be reported as
crashed, with the non-zero exit code and the early stdout close visible."""

import json
import subprocess
import sys
from pathlib import Path

from mcptap.analysis import analyze, load

REPO = Path(__file__).resolve().parent.parent
CRASHER = REPO / "tests" / "fake_crash_server.py"


def test_report_detects_mid_session_crash(tmp_path):
    session = tmp_path / "crash.jsonl"
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},  # server already dead
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},  # still talking
    ]
    stdin = "".join(json.dumps(m) + "\n" for m in lines)
    proc = subprocess.run(
        [sys.executable, "-m", "mcptap", "wrap", "--out", str(session), "--", sys.executable, str(CRASHER)],
        input=stdin,
        check=False,
        capture_output=True,
        text=True,
        cwd=REPO,
        timeout=30,
    )
    # the tap must propagate the child's exit code, not hide it
    assert proc.returncode == 3

    report = analyze(load(session))
    life = report["lifecycle"]
    assert life["exit_code"] == 3
    assert life["crashed"] is True
    # the wire-level signature: requests still expecting an answer
    assert report["unanswered_requests"] == ["tools/list", "tools/list"]
    # the one completed exchange still made it through and into the report
    assert report["server"] == {"name": "crasher", "version": "0.0.1"}
    assert report["messages"]["server_to_client"] == 1
