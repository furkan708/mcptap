"""Adversarial battery: what the REAL wire can do to the tap.

Probes (each names a real-world producer):
  A. batches      — a client sending a JSON-RPC batch (one line, array)
  B. bad bytes    — a server emitting invalid UTF-8 (must pass through
                    byte-exact; a corrupted byte is a corrupted protocol)
  C. secrets      — session files hold full payloads; mode must be 0600
  D. pipelining   — requests fired before any response is read
  E. huge line    — a 1 MB single message must survive
"""

import json
import subprocess
import sys

import pytest
from pathlib import Path

from mcptap.analysis import analyze, load

from .test_wrap import FAKE, REPO, run_wrap


def _wrap_bytes(stdin: bytes, server: Path, session: Path) -> bytes:
    proc = subprocess.run(
        [sys.executable, "-m", "mcptap", "wrap", "--out", str(session), "--", sys.executable, str(server)],
        input=stdin, capture_output=True, cwd=REPO, timeout=60, check=False,
    )
    return proc.stdout


def test_battery_a_batch_requests_counted():
    """A batch line (array) carries two calls; analysis must see both."""
    records = [
        {"ts": 1.0, "dir": "c2s", "data": [
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "add", "arguments": {"a": 1, "b": 2}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "add", "arguments": {"a": 3, "b": 4}}},
        ]},
        {"ts": 1.5, "dir": "s2c", "data": [
            {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "3"}]}},
            {"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": "7"}]}},
        ]},
    ]
    report = analyze(records)
    assert report["tool_calls"]["total"] == 2, "batch calls must count"
    assert report["unanswered_requests"] == [], "batch responses must be matched"


def test_battery_b_invalid_utf8_passthrough(tmp_path):
    """Invalid UTF-8 from the server must reach the client byte-exact."""
    session = tmp_path / "raw.jsonl"
    stdin = b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n'
    out = _wrap_bytes(stdin, REPO / "tests" / "raw_server.py", session)
    poison = b'\xff\xfe{"poison":"\xc3\x28"}\n'
    assert poison in out, "invalid bytes were altered in transit — bytes are sacred or they are not"
    # and the session must preserve them for replay, not silently mangle
    raw = session.read_text(encoding="utf-8", errors="replace")
    assert "raw_b64" in raw or "poison" in raw


def test_battery_c_session_file_permissions(tmp_path):
    """Session files carry full payloads (arguments included): 0600, and a
    0700 sessions directory. Anyone with $HOME read access is not invited.

    POSIX-only by nature: permission BITS don't exist on Windows (ACLs
    are the mechanism there and chmod is a no-op). Everything else in the
    battery runs on Windows — that's the point of the windows CI job.
    """
    if sys.platform == "win32":
        pytest.skip("POSIX permission bits; Windows governs access with ACLs")
    _, session = run_wrap(tmp_path)
    assert session.stat().st_mode & 0o777 == 0o600, "session file must be owner-only"
    assert session.parent.stat().st_mode & 0o777 == 0o700, "sessions dir must be owner-only"


def test_battery_d_pipelined_requests(tmp_path):
    """Requests fired before reading any response must all be matched."""
    session = tmp_path / "pipeline.jsonl"
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "add", "arguments": {"a": 1, "b": 1}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "add", "arguments": {"a": 2, "b": 2}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "slow_mul", "arguments": {"a": 3, "b": 3}}},
    ]
    stdin = "".join(json.dumps(m) + "\n" for m in lines).encode()
    _wrap_bytes(stdin, FAKE, session)
    report = analyze(load(session))
    assert report["tool_calls"]["total"] == 3
    assert report["unanswered_requests"] == []
    latencies = [c["latency_ms"] for c in report["tool_calls"]["detail"]]
    assert all(l is not None for l in latencies)


def test_battery_e_huge_line(tmp_path):
    """A 1 MB argument must survive the round trip."""
    session = tmp_path / "huge.jsonl"
    big = "x" * (1024 * 1024)
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "add", "arguments": {"a": big, "b": ""}}},
    ]
    stdin = "".join(json.dumps(m) + "\n" for m in lines).encode()
    out = _wrap_bytes(stdin, FAKE, session)
    assert out.count(big.encode()) >= 1, "1MB payload lost or corrupted in transit"


def test_battery_f_notification_flood(tmp_path):
    """300 notifications in a burst must ALL arrive — and be countable."""
    session = tmp_path / "flood.jsonl"
    lines = [{"jsonrpc": "2.0", "method": "notifications/progress", "params": {"n": i}} for i in range(300)]
    lines.append({"jsonrpc": "2.0", "id": 1, "method": "count"})
    stdin = "".join(json.dumps(m) + "\n" for m in lines).encode()
    out = _wrap_bytes(stdin, REPO / "tests" / "fake_count_server.py", session)
    assert b'"text": "301"' in out or b'"text":"301"' in out, (
        "the count server saw fewer than 301 lines — the tap dropped traffic"
    )


def test_battery_g_final_line_without_newline(tmp_path):
    """A server exiting mid-line (no trailing \\n) must not lose the tail."""
    session = tmp_path / "partial.jsonl"
    stdin = b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n'
    out = _wrap_bytes(stdin, REPO / "tests" / "raw_server.py", session)
    assert b'"partial":"final-line-without-newline"' in out, "trailing bytes were dropped"


def test_battery_h_garbage_lines(tmp_path):
    """Non-JSON lines from the client must be forwarded (and counted),
    not eaten by the tap's parser."""
    session = tmp_path / "garbage.jsonl"
    parts = [b"this is not json {\xc3\x28", b"", b"<<<garbage>>>"]
    parts.append(b'{"jsonrpc":"2.0","id":1,"method":"count"}\n')
    stdin = b"\n".join(parts)
    out = _wrap_bytes(stdin, REPO / "tests" / "fake_count_server.py", session)
    assert b'"text": "4"' in out or b'"text":"4"' in out, (
        "garbage lines were dropped in transit — the tap must be glass"
    )


def test_battery_i_crlf_line_endings(tmp_path):
    """Windows-style CRLF lines must cross the tap byte-exact."""
    session = tmp_path / "crlf.jsonl"
    payload = b'{"jsonrpc":"2.0","id":1,"method":"x"}\r\n{"trailing":1}\r\n'
    out = _wrap_bytes(payload, REPO / "tests" / "fake_echo_server.py", session)
    assert out == payload, f"CRLF bytes were altered in transit: {out!r}"


def test_battery_j_out_of_order_responses(tmp_path):
    """Responses arriving out of order must be matched by id, not position.

    The reverse server answers the SECOND request immediately and the
    first only after 0.25s: positional matching would swap the latencies.
    """
    session = tmp_path / "reverse.jsonl"
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "add", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "slow_mul", "arguments": {}}},
    ]
    stdin = "".join(json.dumps(m) + "\n" for m in lines).encode()
    _wrap_bytes(stdin, REPO / "tests" / "fake_reverse_server.py", session)
    from mcptap.analysis import analyze, load

    report = analyze(load(session))
    assert report["unanswered_requests"] == []
    by_tool = {c["tool"]: c for c in report["tool_calls"]["detail"]}
    assert by_tool["add"]["latency_ms"] >= 200, "first request must show the LATE answer"
    assert by_tool["slow_mul"]["latency_ms"] < 200, "second request must show the EARLY answer"


def test_token_estimate_is_unicode_aware():
    """chars/4 badly undercounts CJK (≈1 token per char); the estimate
    must not silently lowball non-English content."""
    from mcptap.analysis import _tokens

    assert _tokens("x" * 100) == 25  # ASCII stays chars/4
    assert _tokens("一" * 100) >= 100  # CJK ≈ one token per char
    assert _tokens("ş" * 40 + "x" * 60) >= 50  # Turkish mixes upward, honestly
