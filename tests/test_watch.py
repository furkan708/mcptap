"""watch tests: newest-session selection and the frame builder (report +
live tail). The loop itself is thin; everything testable is pure."""

import json

from mcptap.analysis import load
from mcptap.watch import latest_session, watch_frame

from .test_wrap import run_wrap


def test_latest_session_picks_newest(tmp_path):
    old = tmp_path / "20260101-000000-a.jsonl"
    new = tmp_path / "20260102-000000-b.jsonl"
    for p, stamp in ((old, 1000), (new, 2000)):
        p.write_text(json.dumps({"ts": stamp, "event": "exit", "code": 0}) + "\n")
    assert latest_session(tmp_path) == new
    assert latest_session(tmp_path / "empty") is None


def test_watch_frame_shows_report_and_tail(tmp_path):
    _, session = run_wrap(tmp_path)
    records = load(session)
    frame = watch_frame(records, tail=3)
    assert "mcptap" in frame  # report header present
    assert "fake-math" in frame
    assert "lying_label" in frame  # the last wire exchanges are tailed


def test_cli_watch_once(tmp_path, capsys):
    from mcptap.cli import main as cli_main

    _, session = run_wrap(tmp_path)
    code = cli_main(["watch", str(session), "--once"])
    out = capsys.readouterr().out
    assert code == 0
    assert "fake-math" in out
