"""replay tests: a recorded session is a regression harness. Replay the
client script against a server, compare what the wire looks like now."""

import os
import sys

from mcptap.analysis import analyze, load
from mcptap.replay import compare_reports, replay_session

from .test_wrap import FAKE, run_wrap


def test_replay_against_same_server_finds_no_differences(tmp_path):
    _, session = run_wrap(tmp_path)
    result = replay_session(session, [sys.executable, str(FAKE)], out_path=tmp_path / "replay.jsonl")
    assert result["differences"] == []
    assert result["report"]["lifecycle"]["exit_code"] == 0
    # the replayed session itself is a valid session file
    replayed = analyze(load(result["new_session"]))
    assert replayed["tool_surface"]["count"] == 4


def test_replay_against_upgraded_server_finds_regressions(tmp_path):
    _, session = run_wrap(tmp_path)  # recorded against v1
    env = dict(os.environ, FAKE_TOOLSET="v2")
    result = replay_session(session, [sys.executable, str(FAKE)], out_path=tmp_path / "replay.jsonl", env=env)
    kinds = [d["kind"] for d in result["differences"]]
    assert "tool_removed" in kinds  # delete_everything is gone in v2
    assert "tool_added" in kinds  # search arrived
    assert "error_category" in kinds  # send_email retryable -> forbidden


def test_compare_reports_is_pure_and_ordered():
    old = {"server": {"name": "a", "version": "1"}, "tool_surface": {"tools": [{"name": "x", "est_tokens": 5}], "est_tokens": 5, "unused_tools": []}, "tool_calls": {"detail": []}, "lifecycle": {"exit_code": 0}}
    new = {"server": {"name": "a", "version": "2"}, "tool_surface": {"tools": [{"name": "y", "est_tokens": 7}], "est_tokens": 7, "unused_tools": []}, "tool_calls": {"detail": []}, "lifecycle": {"exit_code": 3}}
    d = compare_reports(old, new)
    kinds = [x["kind"] for x in d]
    assert kinds == ["server", "tool_removed", "tool_added", "surface_total", "exit_code"]


def test_cli_replay_exit_code(tmp_path, capsys):
    _, session = run_wrap(tmp_path)
    from mcptap.cli import main as cli_main

    code = cli_main(["replay", str(session), "--out", str(tmp_path / "r.jsonl"), "--", sys.executable, str(FAKE)])
    assert code == 0  # no differences
    out = capsys.readouterr().out
    assert "no differences" in out
