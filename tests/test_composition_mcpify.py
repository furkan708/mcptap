"""Composition tests: mcptap wrapping mcpify, a real third-party (to us)
MCP server that self-describes errors on the wire.

Chain under test: client → mcptap wrap → mcpify serve → fixture HTTP API.
Skipped automatically when mcpify is not installed (CI runs a dedicated
job that installs it from PyPI). This pins the contract the demo proved:

- mcpify's leading tokens classify correctly through the tap,
- structuredContent error_category is honoured as layer 0,
- a clean session is reported clean end-to-end.
"""

import json
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from mcptap.analysis import analyze, load

REPO = Path(__file__).resolve().parent.parent
DEMO_API = REPO / "demo" / "fixture_api.py"

pytestmark = pytest.mark.skipif(
    shutil.which("mcpify") is None,
    reason="mcpify not installed (optional composition test)",
)


@pytest.fixture()
def fixture_api(tmp_path):
    """In-process copy of the demo fixture API on an ephemeral port."""
    spec = spec_from_file_location("fixture_api", DEMO_API)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    server = HTTPServer(("127.0.0.1", 0), module.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


def _paced_talk(session: Path, api_spec: Path) -> None:
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcptap", "wrap", "--out", str(session), "--",
         "mcpify", "serve", "--retry", "0", str(api_spec)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, bufsize=1, cwd=REPO,
    )
    assert proc.stdin is not None and proc.stdout is not None

    def send(obj: dict) -> None:
        proc.stdin.write(json.dumps(obj) + "\n")
        proc.stdin.flush()

    def recv(want_id: int, timeout: float = 20.0) -> dict:
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
                     "clientInfo": {"name": "mcptap-composition", "version": "0"}}})
    info = recv(1)["result"]["serverInfo"]
    assert info["name"] == "mcpify"
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    recv(2)
    for i, tool in enumerate(("boom_500", "no_auth", "health_check"), start=3):
        send({"jsonrpc": "2.0", "id": i, "method": "tools/call",
              "params": {"name": tool, "arguments": {}}})
        recv(i)
    proc.stdin.close()
    assert proc.wait(timeout=15) == 0


def _api_spec(port: int) -> dict:
    spec = json.loads((REPO / "demo" / "api.json").read_text())
    spec["servers"] = [{"url": f"http://127.0.0.1:{port}"}]
    return spec


def test_composition_mcpify_through_the_tap(tmp_path, fixture_api):
    api_spec = tmp_path / "api.json"
    api_spec.write_text(json.dumps(_api_spec(fixture_api)))
    session = tmp_path / "composition.jsonl"
    _paced_talk(session, api_spec)

    report = analyze(load(session))
    assert report["server"]["name"] == "mcpify"
    by_tool = {c["tool"]: c for c in report["tool_calls"]["detail"]}
    # mcpify says so itself, in both the leading token and structuredContent
    assert by_tool["boom_500"]["error_category"] == "retryable"
    assert by_tool["boom_500"]["http_status"] == 500
    assert by_tool["no_auth"]["error_category"] == "forbidden"
    assert by_tool["no_auth"]["http_status"] == 401
    assert by_tool["health_check"]["error"] is False
    assert report["unanswered_requests"] == []
    assert report["lifecycle"]["crashed"] is False
