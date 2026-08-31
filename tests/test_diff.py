"""diff tests: two sessions of the same server, one "upgraded" (TOOLSET=v2).
The diff must show exactly what the upgrade changed on the wire."""

from pathlib import Path

from mcptap.analysis import analyze, load
from mcptap.diff import diff_reports

from .test_wrap import run_wrap


def both_reports(tmp_path: Path) -> tuple[dict, dict]:
    _, old_session = run_wrap(tmp_path)
    _, new_session = run_wrap(tmp_path, toolset="v2")
    return analyze(load(old_session)), analyze(load(new_session))


def test_diff_detects_surface_changes(tmp_path):
    old, new = both_reports(tmp_path)
    d = diff_reports(old, new)
    kinds = [x["kind"] for x in d]
    assert "tool_removed" in kinds  # delete_everything gone
    assert "tool_added" in kinds  # search arrived
    removed = next(x for x in d if x["kind"] == "tool_removed")
    added = next(x for x in d if x["kind"] == "tool_added")
    assert removed["tool"] == "delete_everything"
    assert added["tool"] == "search"
    assert added["tokens"] > 0


def test_diff_detects_token_price_change(tmp_path):
    old, new = both_reports(tmp_path)
    d = diff_reports(old, new)
    changed = next(x for x in d if x["kind"] == "tool_tokens")
    assert changed["tool"] == "add"  # description grew in v2
    assert changed["old"] < changed["new"]
    totals = next(x for x in d if x["kind"] == "surface_total")
    assert totals["old"] != totals["new"]


def test_diff_detects_error_category_change(tmp_path):
    old, new = both_reports(tmp_path)
    d = diff_reports(old, new)
    cat = next(x for x in d if x["kind"] == "error_category")
    assert cat["tool"] == "send_email"
    assert cat["old"] == {"retryable": 1}
    assert cat["new"] == {"forbidden": 1}


def test_diff_detects_server_version_change(tmp_path):
    old, new = both_reports(tmp_path)
    d = diff_reports(old, new)
    server = next(x for x in d if x["kind"] == "server")
    assert server["old"]["version"] == "9.9.9"
    assert server["new"]["version"] == "9.10.0"


def test_diff_identical_sessions_is_empty(tmp_path):
    _, session = run_wrap(tmp_path)
    report = analyze(load(session))
    assert diff_reports(report, analyze(load(session))) == []
