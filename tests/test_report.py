"""Analyzer tests: the recorded session must produce the honest numbers —
token price, unused tools, latency, error taxonomy, suspects, lifecycle."""

import json
from pathlib import Path

from mcptap.analysis import analyze, load

from .test_wrap import run_wrap


def make_report(tmp_path: Path) -> dict:
    _, session = run_wrap(tmp_path)
    return analyze(load(session))


def test_report_server_and_message_counts(tmp_path):
    report = make_report(tmp_path)
    assert report["server"] == {"name": "fake-math", "version": "9.9.9"}
    assert report["messages"] == {"client_to_server": 8, "server_to_client": 7}


def test_report_tool_surface_price(tmp_path):
    report = make_report(tmp_path)
    surface = report["tool_surface"]
    assert surface["count"] == 4
    assert surface["est_tokens"] > 0
    names = {t["name"] for t in surface["tools"]}
    assert names == {"add", "slow_mul", "send_email", "delete_everything"}


def test_report_unused_tools(tmp_path):
    report = make_report(tmp_path)
    assert report["tool_surface"]["unused_tools"] == ["delete_everything"]


def test_report_error_taxonomy(tmp_path):
    report = make_report(tmp_path)
    calls = report["tool_calls"]
    assert calls["total"] == 5
    assert calls["errors"] == 3
    assert calls["categories"] == {"retryable": 1, "forbidden": 1, "invalid_request": 1}
    by_tool = {c["tool"]: c for c in calls["detail"]}
    assert by_tool["send_email"]["error_category"] == "retryable"  # leading token honoured
    assert by_tool["boom"]["error_category"] == "forbidden"  # 401 heuristic
    # The prefix CONTRADICTS the keywords (connection/socket → retryable);
    # if heuristics won, this would be retryable. Leading token must win.
    assert by_tool["lying_label"]["error_category"] == "invalid_request"


def test_report_latency(tmp_path):
    report = make_report(tmp_path)
    per_tool = report["tool_calls"]["per_tool_latency_ms"]
    assert per_tool["slow_mul"]["p50_ms"] >= 250  # server sleeps 0.3s
    assert per_tool["add"]["p50_ms"] < 250


def test_report_prompt_injection_suspect(tmp_path):
    report = make_report(tmp_path)
    assert report["prompt_injection_suspects"] == ["send_email"]


def test_report_lifecycle_clean(tmp_path):
    report = make_report(tmp_path)
    assert report["lifecycle"] == {
        "exit_code": 0,
        "crashed": False,
        "early_stdout_close": False,
        "interrupted": False,
    }


def test_cli_report_json_roundtrip(tmp_path, capsys):
    from mcptap.cli import main as cli_main

    from .test_wrap import run_wrap

    _, session = run_wrap(tmp_path)
    code = cli_main(["report", str(session), "--json"])
    out = capsys.readouterr().out
    assert code == 0
    parsed = json.loads(out)
    assert parsed["tool_surface"]["count"] == 4
