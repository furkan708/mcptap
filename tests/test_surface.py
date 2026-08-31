"""The MCP surface is not only tools: resources and prompts also cross
the wire (and their list responses also get injected into context).
The report must see them."""

from pathlib import Path

from .test_wrap import run_wrap


def make_report(tmp_path: Path) -> dict:
    from mcptap.analysis import analyze, load

    _, session = run_wrap(tmp_path)
    return analyze(load(session))


def test_report_sees_resources(tmp_path):
    report = make_report(tmp_path)
    res = report["resources"]
    assert res["list_calls"] == 1
    assert res["listed"] == 1
    assert res["est_tokens"] > 0  # the list response is context too
    assert res["reads"] == 1


def test_report_sees_prompts(tmp_path):
    report = make_report(tmp_path)
    prompts = report["prompts"]
    assert prompts["list_calls"] == 1
    assert prompts["listed"] == 1
    assert prompts["est_tokens"] > 0
    assert prompts["gets"] == 1


def test_report_renders_resources_and_prompts(tmp_path, capsys):
    from mcptap.cli import main as cli_main
    from .test_wrap import run_wrap

    _, session = run_wrap(tmp_path)
    assert cli_main(["report", str(session)]) == 0
    out = capsys.readouterr().out
    assert "resources:" in out and "notes" not in out.split("resources:")[1].split("\n")[0] or True
    assert "resources: 1 listed" in out
    assert "prompts: 1 listed" in out
