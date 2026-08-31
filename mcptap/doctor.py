"""doctor: read a client config, probe every stdio server in it, and say
which ones are alive, what they cost, and which are broken.

Understands the config formats people actually have:
  .mcp.json (VS Code workspace / Claude Code project), .cursor/mcp.json and
  ~/.cursor/mcp.json (Cursor), ~/.claude.json (Claude Code), and the Claude
  Desktop config per platform. Entries with a "command" are stdio — probed;
  entries with only a "url" are remote — skipped with a note (that's the
  HTTP-transport gap, stated honestly rather than hidden).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from .probe import merged_env, probe_server


def _candidates() -> list[Path]:
    home = Path.home()
    paths = [
        Path(".mcp.json"),
        Path(".cursor/mcp.json"),
        home / ".cursor/mcp.json",
        home / ".claude.json",
        home / ".config/Claude/claude_desktop_config.json",
        home / "Library/Application Support/Claude/claude_desktop_config.json",
    ]
    appdata = os.environ.get("APPDATA")
    if appdata:
        paths.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
    return paths


def discover_config(explicit: Path | None = None) -> Path | None:
    if explicit is not None:
        return explicit if explicit.exists() else None
    for candidate in _candidates():
        if candidate.exists():
            return candidate
    return None


def load_servers(path: Path) -> tuple[dict[str, dict], dict[str, dict], str | None]:
    """(stdio entries, non-stdio entries, error message or None)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {}, {}, f"cannot read config: {exc}"
    if not isinstance(data, dict):
        return {}, {}, "cannot read config: root must be a JSON object"
    raw = data.get("mcpServers")
    if not isinstance(raw, dict):
        raw = data.get("servers")
    if not isinstance(raw, dict):
        return {}, {}, "cannot read config: no mcpServers/servers object found"
    stdio: dict[str, dict] = {}
    remote: dict[str, dict] = {}
    for name, entry in raw.items():
        if isinstance(entry, dict) and entry.get("command"):
            stdio[str(name)] = entry
        else:
            remote[str(name)] = entry if isinstance(entry, dict) else {}
    return stdio, remote, None


def doctor(config_path: Path) -> list[dict[str, Any]]:
    stdio, remote, error = load_servers(config_path)
    if error:
        return [{"name": str(config_path), "status": "broken", "note": error}]
    results: list[dict[str, Any]] = []
    for name, entry in stdio.items():
        argv = [str(entry.get("command", ""))]
        args = entry.get("args")
        if isinstance(args, list):
            argv.extend(str(a) for a in args)
        probe = probe_server(argv, env=merged_env(entry))
        results.append({"name": name, "status": "ok" if probe["ok"] else "broken", **probe})
    for name, entry in remote.items():
        detail = entry.get("url") or entry.get("type") or "no command"
        results.append({"name": name, "status": "skipped",
                        "note": f"not stdio ({detail}); HTTP tapping is on the roadmap"})
    return results


def render_doctor(results: list[dict[str, Any]], config_path: Path) -> str:
    lines = [f"mcptap doctor — {config_path}"]
    broken = 0
    probed = 0
    for r in results:
        status = r["status"]
        if status == "skipped":
            lines.append(f"  ⚠ {r['name']}: {r.get('note', 'skipped')}")
            continue
        probed += 1
        if status == "ok":
            server = r.get("server") or {}
            n_prompts = r["prompts"]
            lines.append(
                f"  ✓ {r['name']}: {server.get('name') or '?'} {server.get('version') or ''} — "
                f"init {r['init_latency_ms']}ms, {r['tool_surface']} tools ≈ {r['surface_tokens']} tk, "
                f"{r['resources']} res, {n_prompts} prompt{'s' if n_prompts != 1 else ''}".rstrip()
            )
        else:
            broken += 1
            reason = r.get("error") or f"exit code {r.get('exit_code')}"
            lines.append(f"  ✗ {r['name']}: {reason}")
    if probed:
        lines.append(f"{broken} of {probed} probed servers broken")
    return "\n".join(lines) + "\n"


def main_doctor(config: Path | None) -> int:
    path = discover_config(config)
    if path is None:
        searched = ", ".join(str(p) for p in _candidates())
        sys.stderr.write(f"mcptap: no config found (searched: {searched})\n")
        return 2
    results = doctor(path)
    sys.stdout.write(render_doctor(results, path))
    return 1 if any(r["status"] == "broken" for r in results) else 0
