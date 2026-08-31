"""sessions command tests: list recorded sessions with honest summaries."""

from mcptap.cli import main as cli_main

from .test_wrap import run_wrap


def test_cli_sessions_lists_sessions(tmp_path, capsys):
    run_wrap(tmp_path)
    code = cli_main(["sessions", "--dir", str(tmp_path)])
    assert code == 0
    out = capsys.readouterr().out
    assert "fake-math 9.9.9" in out
    assert "newest" in out


def test_cli_sessions_empty_dir(tmp_path, capsys):
    code = cli_main(["sessions", "--dir", str(tmp_path)])
    assert code == 0
    assert "no sessions" in capsys.readouterr().out
