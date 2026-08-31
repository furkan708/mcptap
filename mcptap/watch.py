"""Live view: refresh a report while the tap is recording.

Everything testable is pure: which session file is newest, and how to
build one frame (report + the last few wire lines). The refresh loop in
the CLI is a thin shell around these.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .analysis import analyze
from .recorder import DEFAULT_SESSIONS_DIR
from .render import render


def latest_session(directory: Path = DEFAULT_SESSIONS_DIR) -> Path | None:
    """Newest .jsonl in the sessions dir (files are stamped, so tie-break
    on the name — mtimes can be identical on fast machines)."""
    files = sorted(
        directory.glob("*.jsonl"),
        key=lambda p: (p.stat().st_mtime, p.name),
    )
    return files[-1] if files else None


def wire_line(record: dict[str, Any]) -> str:
    arrow = "→ server" if record.get("dir") == "c2s" else "← client"
    data = record.get("data")
    if isinstance(data, dict):
        if "method" in data:
            label = str(data.get("method"))
            params = data.get("params") or {}
            if isinstance(params, dict) and params.get("name"):
                label += f" ({params['name']})"
        elif "id" in data:
            if data.get("error"):
                label = f"error response: {str(data['error'].get('message', ''))[:60]}"
            else:
                label = "ok response"
        else:
            label = "message"
    else:
        raw = data.get("raw", "") if isinstance(data, dict) else str(data)
        label = f"raw: {raw[:60]}"
    return f"  {arrow}  {label}"


def watch_frame(records: list[dict[str, Any]], tail: int = 5, path: Path | None = None) -> str:
    """One screenful: the full report plus the last `tail` wire lines."""
    report = analyze(records)
    header = render(report, path or Path("live"))
    lines = [header.rstrip(), "", "wire (last few):"]
    wire = [r for r in records if "dir" in r]
    lines.extend(wire_line(record) for record in wire[-tail:])
    return "\n".join(lines) + "\n"
