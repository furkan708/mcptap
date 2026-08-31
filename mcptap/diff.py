"""Compare two session reports: what changed on the wire between them.

The typical use is the same server before/after an upgrade (or two clients
against the same server): surface changes, token price drift, error
semantics flips, exit codes. Output is a list of small structured records
so the CLI (and future tools) can render it any way they like.
"""

from __future__ import annotations

from collections import Counter
from typing import Any


def _surface_map(report: dict[str, Any]) -> dict[str, int]:
    return {t["name"]: t["est_tokens"] for t in report["tool_surface"]["tools"]}


def _error_map(report: dict[str, Any]) -> dict[str, Counter]:
    per_tool: dict[str, Counter] = {}
    for call in report["tool_calls"]["detail"]:
        if call["error"]:
            per_tool.setdefault(call["tool"], Counter())[call["error_category"]] += 1
    return per_tool


def compare_reports(old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
    """Structured differences between two `analyze()` reports, or []."""
    out: list[dict[str, Any]] = []

    if old["server"] != new["server"]:
        out.append({"kind": "server", "old": old["server"], "new": new["server"]})

    old_tools, new_tools = _surface_map(old), _surface_map(new)
    for name in sorted(set(old_tools) - set(new_tools)):
        out.append({"kind": "tool_removed", "tool": name, "tokens": old_tools[name]})
    for name in sorted(set(new_tools) - set(old_tools)):
        out.append({"kind": "tool_added", "tool": name, "tokens": new_tools[name]})
    for name in sorted(set(old_tools) & set(new_tools)):
        if old_tools[name] != new_tools[name]:
            out.append(
                {"kind": "tool_tokens", "tool": name, "old": old_tools[name], "new": new_tools[name]}
            )
    old_total = old["tool_surface"]["est_tokens"]
    new_total = new["tool_surface"]["est_tokens"]
    if old_total != new_total:
        out.append({"kind": "surface_total", "old": old_total, "new": new_total})

    old_errs, new_errs = _error_map(old), _error_map(new)
    for tool in sorted(set(old_errs) | set(new_errs)):
        if old_errs.get(tool, Counter()) != new_errs.get(tool, Counter()):
            out.append(
                {
                    "kind": "error_category",
                    "tool": tool,
                    "old": dict(old_errs.get(tool, Counter())),
                    "new": dict(new_errs.get(tool, Counter())),
                }
            )

    old_exit = old["lifecycle"]["exit_code"]
    new_exit = new["lifecycle"]["exit_code"]
    if old_exit != new_exit:
        out.append({"kind": "exit_code", "old": old_exit, "new": new_exit})

    return out


# `diff` is `compare_reports` spelled the way the CLI says it.
diff_reports = compare_reports
