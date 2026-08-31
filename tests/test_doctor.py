"""doctor tests: probe a stdio server with a paced handshake, read client
configs (Claude Desktop / Cursor / Claude Code formats), and turn a mixed
config into an honest per-server verdict."""

import json
import sys

from mcptap.doctor import discover_config, doctor, load_servers
from mcptap.probe import probe_server

from .test_wrap import FAKE


def test_probe_healthy_server():
    r = probe_server([sys.executable, str(FAKE)])
    assert r["ok"] is True
    assert r["server"] == {"name": "fake-math", "version": "9.9.9"}
    assert r["tool_surface"] == 4
    assert r["surface_tokens"] > 0
    assert r["resources"] == 1
    assert r["prompts"] == 1
    assert r["exit_code"] == 0
    assert r["init_latency_ms"] >= 0


def test_probe_command_not_found():
    r = probe_server(["mcptap-definitely-not-a-command-xyz"])
    assert r["ok"] is False
    assert "not found" in r["error"].lower()


def test_probe_dying_server():
    r = probe_server([sys.executable, "-c", "import sys; sys.exit(3)"])
    assert r["ok"] is False
    assert r["exit_code"] == 3 or r["error"]


def test_load_servers_mcpServers_and_alias(tmp_path):
    cfg = tmp_path / "a.json"
    cfg.write_text(json.dumps({"mcpServers": {"a": {"command": "x"}}}))
    stdio, remote, error = load_servers(cfg)
    assert error is None and list(stdio) == ["a"] and remote == {}
    cfg2 = tmp_path / "b.json"
    cfg2.write_text(json.dumps({"servers": {"b": {"command": "y"}, "c": {"url": "https://x"}}}))
    stdio, remote, error = load_servers(cfg2)
    assert error is None and list(stdio) == ["b"] and list(remote) == ["c"]


def test_load_servers_malformed_config(tmp_path):
    cfg = tmp_path / "bad.json"
    cfg.write_text("{not json")
    stdio, remote, error = load_servers(cfg)
    assert stdio == {} and remote == {} and error and "cannot read" in error


def test_doctor_mixed_config(tmp_path):
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"mcpServers": {
        "math": {"command": sys.executable, "args": [str(FAKE)]},
        "dies": {"command": sys.executable, "args": ["-c", "import sys; sys.exit(3)"]},
        "remote": {"url": "https://example.com", "type": "http"},
    }}))
    results = doctor(cfg)
    by = {r["name"]: r for r in results}
    assert by["math"]["status"] == "ok"
    assert by["math"]["tool_surface"] == 4
    assert by["dies"]["status"] == "broken"
    assert by["remote"]["status"] == "skipped"
    assert "stdio" in by["remote"]["note"]


def test_cli_doctor_exit_codes(tmp_path, capsys):
    from mcptap.cli import main as cli_main

    good = tmp_path / "good.json"
    good.write_text(json.dumps({"mcpServers": {"math": {"command": sys.executable, "args": [str(FAKE)]}}}))
    assert cli_main(["doctor", str(good)]) == 0
    out = capsys.readouterr().out
    assert "fake-math" in out and "✓" in out

    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"mcpServers": {
        "dies": {"command": sys.executable, "args": ["-c", "import sys; sys.exit(3)"]}}}))
    assert cli_main(["doctor", str(bad)]) == 1
    out = capsys.readouterr().out
    assert "✗" in out and "1 of 1 probed servers broken" in out


def test_discover_config_prefers_explicit(tmp_path):
    cfg = tmp_path / "explicit.json"
    cfg.write_text("{}")
    assert discover_config(cfg) == cfg
    assert discover_config(tmp_path / "missing.json") is None
