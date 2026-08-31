"""Turn a recorded session into an honest report.

The report answers, from the wire alone (no SDK, no cooperation from the
client or server):

- what ran, for how long, and how chatty it was
- the tool surface and its token price (chars/4 heuristic, like the big
  providers' tokenizers approximately behave for JSON)
- which tools were actually called — and which were paid for and never used
- per-tool latency (p50/p95) and errors with a best-effort taxonomy
- whether the server exited cleanly, crashed, or hung until killed
- tool descriptions that read like instructions to the model (a prompt
  injection hygiene signal worth surfacing, not a verdict)

Error taxonomy is layered, most-trusted first: (0) a result's
structuredContent.error_category is believed verbatim (mcpify ≥ 1.18
states it); (1) servers that speak mcptap/mcpify-style leading tokens
("retryable:") are honoured exactly; (2) JSON-RPC error codes are
classified by protocol class; (3) else keyword heuristics on the message
text, honestly labelled as such.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

CHUNK = 4  # chars per token heuristic

# Imperative phrasing in a *description* is how most tool-poisoning
# payloads start. Heuristic, surfaced as a signal — never a verdict.
SUSPICIOUS_STARTS = (
    "you must",
    "you should",
    "you are",
    "always",
    "never",
    "ignore",
    "do not",
    "don't",
    "before calling",
    "after calling",
    "important:",
    "note:",
    "remember",
    "make sure",
)

RETRYABLE_WORDS = (
    "timeout", "timed out", "connection", "reset", "refused", "temporarily",
    "overloaded", "unavailable", "502", "503", "504", "429", "rate limit",
    "econnreset", "econnrefused", "socket",
)
FORBIDDEN_WORDS = ("401", "403", "unauthorized", "forbidden", "permission", "api key", "token")
INVALID_WORDS = ("400", "404", "422", "invalid", "not found", "unknown", "unsupported", "missing")


def _tokens(text: str) -> int:
    return math.ceil(len(text) / CHUNK)


def _is_request(msg: dict[str, Any]) -> bool:
    return "method" in msg and "id" in msg


def _is_response(msg: dict[str, Any]) -> bool:
    return "method" not in msg and "id" in msg


def _iter_messages(data: Any) -> list[dict[str, Any]]:
    """One wire line may carry a JSON-RPC batch (an array); yield the
    message objects inside, whatever the container."""
    if isinstance(data, list):
        return [m for m in data if isinstance(m, dict)]
    if isinstance(data, dict):
        return [data]
    return []


def _error_category(kind: str, detail: str) -> str:
    text = detail.lower()
    for prefix in ("retryable:", "invalid_request:", "forbidden:"):
        if text.startswith(prefix):
            return prefix.rstrip(":")
    if kind == "rpc":
        code = detail.lower()
        if code in ("-32700", "-32600", "-32601", "-32602"):
            return "invalid_request"
        if code in ("-32603",):
            return "retryable"
    for word in FORBIDDEN_WORDS:
        if word in text:
            return "forbidden"
    for word in RETRYABLE_WORDS:
        if word in text:
            return "retryable"
    for word in INVALID_WORDS:
        if word in text:
            return "invalid_request"
    return "unknown"


def _suspicious(description: str) -> bool:
    lowered = description.lower().lstrip()
    return any(lowered.startswith(phrase) for phrase in SUSPICIOUS_STARTS)


def _percentile(sorted_values: list[float], fraction: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, max(0, round(fraction * (len(sorted_values) - 1))))
    return sorted_values[index]


def load(path: Path) -> list[dict[str, Any]]:
    """Load a session JSONL; unparseable lines are skipped (with a count)."""
    records: list[dict[str, Any]] = []
    skipped = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            skipped += 1
    if skipped:
        records.append({"event": "tap_warning", "detail": f"{skipped} unparseable lines skipped"})
    return records


def analyze(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute the full report dict from a loaded session."""
    start_ts = records[0]["ts"] if records else 0.0
    last_ts = records[-1]["ts"] if records else 0.0
    duration_s = max(0.0, last_ts - start_ts)

    messages = [r for r in records if "dir" in r]
    events = [r for r in records if "event" in r]

    server_info: dict[str, Any] = {}
    initialize_latency_ms = 0.0
    tool_surface: list[dict[str, Any]] = []
    surface_tokens = 0
    tools_list_calls = 0
    requests_by_id: dict[Any, tuple[dict[str, Any], float]] = {}
    responses_by_id: dict[Any, tuple[dict[str, Any], float]] = {}
    calls: list[dict[str, Any]] = []

    for record in messages:
        ts = record["ts"]
        for msg in _iter_messages(record.get("data")):
            if _is_request(msg):
                requests_by_id[msg["id"]] = (msg, ts)
            elif _is_response(msg):
                responses_by_id[msg["id"]] = (msg, ts)

    for rid, (request, request_ts) in requests_by_id.items():
        response: dict[str, Any] | None
        response_ts: float | None
        entry = responses_by_id.get(rid)
        if entry is None:
            response, response_ts = None, None
        else:
            response, response_ts = entry
        latency_ms = (response_ts - request_ts) * 1000.0 if response_ts is not None else None

        method = request.get("method", "")
        if method == "initialize" and response is not None:
            initialize_latency_ms = latency_ms or 0.0
            info = ((response.get("result") or {}).get("serverInfo") or {})
            server_info = {"name": info.get("name"), "version": info.get("version")}
        if method == "tools/list":
            tools_list_calls += 1
            tools = ((response or {}).get("result") or {}).get("tools") or []
            if tools:
                tool_surface, surface_tokens = _measure_surface(tools)
        if method == "tools/call" and response is not None:
            calls.append(_measure_call(request, response, latency_ms))

    used_names = {c["tool"] for c in calls}
    unused = [t["name"] for t in tool_surface if t["name"] not in used_names]

    # Client requests that never got a response: the server died, hung, or
    # swallowed them. This is the wire-level signature of a silent failure.
    unanswered = sorted(
        str(msg.get("method"))
        for rid, (msg, _ts) in requests_by_id.items()
        if rid not in responses_by_id
    )

    errors = [c for c in calls if c["error"]]
    categories = Counter(c["error_category"] for c in errors)
    latency_all = sorted(c["latency_ms"] for c in calls if c["latency_ms"] is not None)
    per_tool: dict[str, list[float]] = {}
    for call in calls:
        if call["latency_ms"] is not None:
            per_tool.setdefault(call["tool"], []).append(call["latency_ms"])
    per_tool_latency = {
        name: {
            "calls": len(values),
            "p50_ms": _percentile(sorted(values), 0.50),
            "p95_ms": _percentile(sorted(values), 0.95),
        }
        for name, values in per_tool.items()
    }

    exit_event = next((e for e in reversed(events) if e.get("event") == "exit"), None)
    # stdout always closes when the server exits — that is normal. "Early"
    # means it closed while the client was still talking: the server died
    last_c2s_ts = max((r["ts"] for r in messages if r["dir"] == "c2s"), default=0.0)
    early_close = any(
        e["ts"] < last_c2s_ts
        for e in events
        if e.get("event") == "server_stdout_closed"
    )
    interrupted = any(e.get("event") == "interrupted" for e in events)
    if exit_event is not None:
        exit_code: int | None = exit_event.get("code")
    else:
        exit_code = None
    crashed = bool(unanswered) or exit_code not in (0, None) or early_close

    return {
        "server": server_info,
        "duration_s": round(duration_s, 2),
        "unanswered_requests": unanswered,
        "initialize_latency_ms": round(initialize_latency_ms, 1),
        "messages": {
            "client_to_server": sum(1 for r in messages if r["dir"] == "c2s"),
            "server_to_client": sum(1 for r in messages if r["dir"] == "s2c"),
        },
        "tool_surface": {
            "tools": tool_surface,
            "count": len(tool_surface),
            "est_tokens": surface_tokens,
            "tools_list_calls": tools_list_calls,
            "unused_tools": unused,
        },
        "tool_calls": {
            "total": len(calls),
            "errors": len(errors),
            "categories": dict(categories),
            "per_tool_latency_ms": {
                k: {kk: round(vv, 1) for kk, vv in v.items()} for k, v in per_tool_latency.items()
            },
            "overall": {
                "p50_ms": round(_percentile(latency_all, 0.50), 1),
                "p95_ms": round(_percentile(latency_all, 0.95), 1),
            },
            "detail": calls,
        },
        "prompt_injection_suspects": [
            t["name"] for t in tool_surface if t.get("suspicious_description")
        ],
        "lifecycle": {
            "exit_code": exit_code,
            "crashed": crashed,
            "early_stdout_close": early_close,
            "interrupted": interrupted,
        },
    }


def _measure_surface(tools: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    surface = []
    total = 0
    for tool in tools:
        blob = json.dumps(tool, ensure_ascii=False)
        cost = _tokens(blob)
        total += cost
        description = tool.get("description") or ""
        surface.append(
            {
                "name": tool.get("name"),
                "est_tokens": cost,
                "suspicious_description": _suspicious(description),
            }
        )
    return surface, total


def _measure_call(
    request: dict[str, Any], response: dict[str, Any], latency_ms: float | None
) -> dict[str, Any]:
    name = ((request.get("params") or {}).get("name")) or "?"
    result = response.get("result") or {}
    error_obj = response.get("error")
    detail = ""
    if error_obj is not None:
        kind, detail = "rpc", str(error_obj.get("code", ""))
        text = str(error_obj.get("message", ""))
    elif result.get("isError"):
        kind, text = "isError", _first_text(result)
    else:
        kind, text = "", ""
    structured = result.get("structuredContent")
    structured = structured if isinstance(structured, dict) else {}
    # Layer 0: a server that states its error category in structuredContent
    # (mcpify ≥ 1.18 does) is believed verbatim — it knows better than any
    # reading of prose.
    category = structured.get("error_category") or (_error_category(kind, f"{detail} {text}".strip()) if kind else None)
    return {
        "tool": name,
        "latency_ms": round(latency_ms, 1) if latency_ms is not None else None,
        "error": bool(kind),
        "error_kind": kind or None,
        "error_category": category,
        "http_status": structured.get("http_status"),
        # single line: wire errors carry multi-line JSON bodies, and the
        # report stays one-error-per-line
        "error_excerpt": (" ".join(text.split())[:200] or None) if text else None,
    }


def _first_text(result: dict[str, Any]) -> str:
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            return str(item.get("text", ""))
    return ""
