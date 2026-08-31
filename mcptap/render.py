"""Human-readable rendering of a session report (shared by report/diff
watch). Numbers first; every warning is a wire fact, not an opinion."""

from __future__ import annotations

from pathlib import Path
from typing import Any


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

    res = report.get("resources") or {}
    pro = report.get("prompts") or {}
    if res.get("list_calls") or res.get("reads"):
        add(f"\nresources: {res.get('listed', 0)} listed ≈ {res.get('est_tokens', 0)} tokens "
            f"({res.get('list_calls', 0)}× list, {res.get('reads', 0)}× read)")
    if pro.get("list_calls") or pro.get("gets"):
        add(f"prompts: {pro.get('listed', 0)} listed ≈ {pro.get('est_tokens', 0)} tokens "
            f"({pro.get('list_calls', 0)}× list, {pro.get('gets', 0)}× get)")

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
