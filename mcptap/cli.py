"""Command line: `mcptap wrap | report | watch | diff | replay`.

    mcptap wrap -- uvx mcp-server-fetch
    mcptap report ~/.mcptap/sessions/20260831-101500-uvx.jsonl
    mcptap watch            # live view of the newest session
    mcptap diff OLD.jsonl NEW.jsonl
    mcptap replay OLD.jsonl -- uvx mcp-server-fetch

One config line turns the tap on (see README). stderr carries tap notices,
stdout stays a clean protocol channel.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .analysis import analyze, load
from .diff import diff_reports
from .recorder import DEFAULT_SESSIONS_DIR
from .recorder import wrap as run_wrap
from .render import render
from .replay import replay_session
from .watch import latest_session, watch_frame


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

    p_report = sub.add_parser("report", help="summarize a recorded session")
    p_report.add_argument("session", type=Path, help="path to a .jsonl session file")
    p_report.add_argument("--json", action="store_true", help="machine-readable output")

    p_watch = sub.add_parser("watch", help="live-refreshing report of a session")
    p_watch.add_argument("session", type=Path, nargs="?", default=None,
                         help="session to watch (default: newest in ~/.mcptap/sessions)")
    p_watch.add_argument("--interval", type=float, default=1.0, help="refresh seconds")
    p_watch.add_argument("--tail", type=int, default=5, help="wire lines to show")
    p_watch.add_argument("--once", action="store_true", help="print one frame and exit")

    p_diff = sub.add_parser("diff", help="compare two sessions (e.g. before/after an upgrade)")
    p_diff.add_argument("old", type=Path, help="older session .jsonl")
    p_diff.add_argument("new", type=Path, help="newer session .jsonl")

    p_replay = sub.add_parser(
        "replay", help="re-send a recorded client script to a server and diff the wire"
    )
    p_replay.add_argument("session", type=Path, help="recorded session .jsonl")
    p_replay.add_argument("--out", type=Path, default=None, help="replay session file")

    p_doctor = sub.add_parser(
        "doctor", help="probe every stdio server in a client config and report health"
    )
    p_doctor.add_argument("config", type=Path, nargs="?", default=None,
                          help="config file (default: search .mcp.json, Cursor, Claude Code, Claude Desktop)")

    p_sessions = sub.add_parser("sessions", help="list recorded sessions with summaries")
    p_sessions.add_argument("--dir", type=Path, default=DEFAULT_SESSIONS_DIR,
                            help=f"sessions directory (default: {DEFAULT_SESSIONS_DIR})")

    return parser


def _split_dash_dash(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split argv at the first bare `--`: (tap args, server command).

    Done by hand because argparse REMAINDER swallows legitimate options
    (e.g. `replay session --out X -- cmd`) once a positional is filled.
    """
    if "--" in argv:
        i = argv.index("--")
        return argv[:i], argv[i + 1 :]
    return argv, []


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    tap_args, cmd = _split_dash_dash(list(sys.argv[1:] if argv is None else argv))
    args = parser.parse_args(tap_args)

    if args.command == "wrap":
        if not cmd:
            parser.error("wrap needs a command, e.g.: mcptap wrap -- uvx mcp-server-fetch")
        return run_wrap(cmd, out_path=args.out)

    if args.command == "report":
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

    if args.command == "watch":
        path = args.session or latest_session()
        if path is None:
            sys.stderr.write(f"mcptap: no sessions in {DEFAULT_SESSIONS_DIR}\n")
            return 1
        return _watch_loop(path, args.interval, args.tail, args.once)

    if args.command == "diff":
        old, new = analyze(load(args.old)), analyze(load(args.new))
        sys.stdout.write(_render_diff(diff_reports(old, new), args.old, args.new))
        return 0

    if args.command == "replay":
        if not cmd:
            parser.error("replay needs a command, e.g.: mcptap replay session.jsonl -- uvx server")
        result = replay_session(args.session, cmd, out_path=args.out)
        sys.stdout.write(_render_diff(result["differences"], args.session, result["new_session"]))
        return 1 if result["differences"] else 0

    if args.command == "doctor":
        from .doctor import main_doctor

        return main_doctor(args.config)

    if args.command == "sessions":
        return _list_sessions(args.dir)

    parser.error(f"unknown command {args.command!r}")
    return 2  # unreachable


def _list_sessions(directory: Path) -> int:
    from .analysis import analyze, load
    from .watch import latest_session

    newest = latest_session(directory)
    files = sorted(directory.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        sys.stdout.write(f"no sessions in {directory}\n")
        return 0
    print(f"mcptap sessions — {directory}")
    for path in files:
        try:
            report = analyze(load(path))
        except (OSError, json.JSONDecodeError):
            print(f"  {path.name}  (unparseable)")
            continue
        server = report.get("server") or {}
        mark = "  (newest)" if path == newest else ""
        print(
            f"  {path.name}  {server.get('name') or '?'} {server.get('version') or ''}  "
            f"{report['duration_s']}s  {report['tool_surface']['count']} tools ≈ "
            f"{report['tool_surface']['est_tokens']} tk{mark}"
        )
    return 0


def _watch_loop(path: Path, interval: float, tail: int, once: bool) -> int:
    try:
        while True:
            records = load(path)
            if not records:
                sys.stderr.write(f"mcptap: no records (yet) in {path}\n")
                return 1
            frame = watch_frame(records, tail=tail, path=path)
            sys.stdout.write("\x1b[2J\x1b[H" + frame)  # clear + home
            if once:
                return 0
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0


def _render_diff(diffs: list[dict[str, Any]], old: Path, new: Path) -> str:
    lines = [f"mcptap diff — {old.name} → {new.name}"]
    if not diffs:
        lines.append("  no differences on the wire")
        return "\n".join(lines) + "\n"
    for d in diffs:
        kind = d["kind"]
        if kind == "server":
            lines.append(f"  server: {d['old'].get('name')} {d['old'].get('version')} → "
                         f"{d['new'].get('name')} {d['new'].get('version')}")
        elif kind == "tool_added":
            lines.append(f"  + {d['tool']} ({d['tokens']} tokens)")
        elif kind == "tool_removed":
            lines.append(f"  - {d['tool']} ({d['tokens']} tokens)")
        elif kind == "tool_tokens":
            delta = d["new"] - d["old"]
            sign = "+" if delta >= 0 else ""
            lines.append(f"  ~ {d['tool']}: {d['old']} → {d['new']} tokens ({sign}{delta})")
        elif kind == "surface_total":
            delta = d["new"] - d["old"]
            sign = "+" if delta >= 0 else ""
            lines.append(f"  tool surface total: {d['old']} → {d['new']} tokens ({sign}{delta})")
        elif kind == "error_category":
            lines.append(f"  ~ {d['tool']} errors: {d['old']} → {d['new']}")
        elif kind == "exit_code":
            lines.append(f"  server exit code: {d['old']} → {d['new']}")
    return "\n".join(lines) + "\n"
