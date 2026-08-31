"""Command line: `mcptap wrap` and `mcptap report`.

    mcptap wrap -- uvx mcp-server-fetch
    mcptap report ~/.mcptap/sessions/20260831-101500-uvx.jsonl

One config line turns the tap on (see README). stderr carries tap notices,
stdout stays a clean protocol channel.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .analysis import analyze, load
from .recorder import wrap as run_wrap


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mcptap",
        description="A zero-dependency wire tap for MCP stdio servers.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_wrap = sub.add_parser("wrap", help="run an MCP stdio server and record all traffic")
    p_wrap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="session file (default: ~/.mcptap/sessions/…)",
    )
    p_wrap.add_argument(
        "cmd",
        nargs=argparse.REMAINDER,
        metavar="COMMAND",
        help="server command; put -- before it if it starts with flags",
    )

    p_report = sub.add_parser("report", help="summarize a recorded session")
    p_report.add_argument("session", type=Path, help="path to a .jsonl session file")
    p_report.add_argument("--json", action="store_true", help="machine-readable output")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "wrap":
        cmd = list(args.cmd)
        if cmd and cmd[0] == "--":
            cmd = cmd[1:]
        if not cmd:
            parser.error("wrap needs a command, e.g.: mcptap wrap -- uvx mcp-server-fetch")
        return run_wrap(cmd, out_path=args.out)

    records = load(args.session)
    if not records:
        sys.stderr.write(f"mcptap: no records in {args.session}\n")
        return 1
    report = analyze(records)
    if args.json:
        sys.stdout.write(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(render(report, args.session))
    return 0


def render(report: dict[str, Any], path: Path) -> str:
    """Human-readable, numbers-first summary."""
    lines: list[str] = []
    add = lines.append

    server = report.get("server") or {}
    name = server.get("name") or "unknown server"
    add(f"mcptap report — {path.name}")
    add(f"  server: {name} {server.get('version') or ''}".rstrip())
    add(
        f"  session: {report['duration_s']}s, "
        f"{report['messages']['client_to_server']}→ client msgs, "
        f"{report['messages']['server_to_client']}← server msgs, "
        f"init {report['initialize_latency_ms']}ms"
    )

    surface = report["tool_surface"]
    add(f"\ntool surface: {surface['count']} tools ≈ {surface['est_tokens']} tokens "
        f"({surface['tools_list_calls']}× tools/list)")
    for tool in sorted(surface["tools"], key=lambda t: -t["est_tokens"])[:10]:
        flag = "  ⚠ imperative description" if tool["suspicious_description"] else ""
        add(f"  {tool['est_tokens']:>7}  {tool['name']}{flag}")
    if surface["unused_tools"]:
        add(f"  unused (paid for, never called): {', '.join(surface['unused_tools'])}")

    calls = report["tool_calls"]
    add(f"\ntool calls: {calls['total']} total, {calls['errors']} errors")
    if calls["errors"]:
        for category, count in sorted(calls["categories"].items()):
            add(f"  {count}× {category}")
    overall = calls["overall"]
    if calls["total"]:
        add(f"  latency: p50 {overall['p50_ms']}ms, p95 {overall['p95_ms']}ms")
    for call in calls["detail"]:
        if call["error"]:
            add(
                f"  ✗ {call['tool']} [{call['error_category']}] "
                f"({call['latency_ms']}ms): {(call['error_excerpt'] or '')[:120]}"
            )

    if report["prompt_injection_suspects"]:
        add(f"\nprompt-injection suspects (imperative tool descriptions): "
            f"{', '.join(report['prompt_injection_suspects'])}")

    life = report["lifecycle"]
    if report["unanswered_requests"]:
        add(f"\n⚠ unanswered requests (server died, hung, or swallowed them): "
            f"{len(report['unanswered_requests'])}× {', '.join(sorted(set(report['unanswered_requests'])))}")
    if life["crashed"]:
        add(f"⚠ server did NOT exit cleanly (code={life['exit_code']}, "
            f"early_close={life['early_stdout_close']}, interrupted={life['interrupted']})")
    else:
        add(f"\nlifecycle: clean exit (code={life['exit_code']})")
    return "\n".join(lines) + "\n"
