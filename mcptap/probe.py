"""Probe a stdio MCP server the way a real client would: paced
initialize → notifications/initialized → tools/list → resources/list →
prompts/list, then a clean close. One call, honest numbers.

Tolerant by design: a server that answers tools/list but errors on
resources/list (-32601) is healthy with resources = 0, not broken —
capabilities differ.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from typing import Any

from .analysis import _tokens

PROBE_TIMEOUT_S = 15.0


def _empty(error: str | None = None, **extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "server": {},
        "init_latency_ms": 0.0,
        "tool_surface": 0,
        "surface_tokens": 0,
        "resources": 0,
        "prompts": 0,
        "exit_code": None,
        "error": error,
    }
    result.update(extra)
    return result


def probe_server(argv: list[str], env: dict[str, str] | None = None, timeout_s: float = PROBE_TIMEOUT_S) -> dict[str, Any]:
    if not argv or not argv[0]:
        return _empty("no command configured")
    try:
        child = subprocess.Popen(  # the operator's own config — that is the product
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
        )
    except (FileNotFoundError, PermissionError) as exc:
        return _empty(f"command not found: {exc}")

    assert child.stdin is not None and child.stdout is not None
    wire: queue.Queue[bytes | None] = queue.Queue()

    def pump() -> None:
        for line in iter(child.stdout.readline, b""):  # type: ignore[union-attr]
            wire.put(line)
        wire.put(None)

    threading.Thread(target=pump, daemon=True).start()

    def send(obj: dict[str, Any]) -> None:
        try:
            child.stdin.write(json.dumps(obj).encode("utf-8") + b"\n")  # type: ignore[union-attr]
            child.stdin.flush()  # type: ignore[union-attr]
        except (BrokenPipeError, ValueError):
            pass

    def ask(msg: dict[str, Any], deadline: float) -> dict[str, Any] | None:
        """Send a request and wait for the response with its id."""
        send(msg)
        while time.time() < deadline:
            try:
                line = wire.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                return None
            if line is None:
                return None
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if isinstance(parsed, dict) and parsed.get("id") == msg.get("id"):
                return parsed
        return None

    started = time.time()
    init = ask(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "mcptap-doctor", "version": "0"}}},
        started + timeout_s,
    )
    if init is None:
        child.kill()
        code = child.wait()
        return _empty("no answer to initialize (server died, hung, or is not an MCP stdio server)",
                      exit_code=code)
    init_latency_ms = (time.time() - started) * 1000.0
    info = ((init.get("result") or {}).get("serverInfo") or {})
    send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    tools: list[Any] = []
    r = ask({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, time.time() + timeout_s)
    if r is not None and "result" in r:
        tools = (r.get("result") or {}).get("tools") or []

    def count_list(method: str, req_id: int, key: str) -> int:
        resp = ask({"jsonrpc": "2.0", "id": req_id, "method": method}, time.time() + timeout_s)
        if resp is None or "result" not in resp:
            return 0  # unsupported (e.g. -32601) is healthy-with-zero, not broken
        return len((resp.get("result") or {}).get(key) or [])

    resources = count_list("resources/list", 3, "resources")
    prompts = count_list("prompts/list", 4, "prompts")

    try:
        child.stdin.close()  # type: ignore[union-attr]
    except (BrokenPipeError, OSError):
        pass
    try:
        code = child.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        child.kill()
        code = child.wait()
        return _empty("server did not exit after stdin closed", exit_code=code,
                      server={"name": info.get("name"), "version": info.get("version")},
                      init_latency_ms=round(init_latency_ms, 1),
                      tool_surface=len(tools),
                      surface_tokens=_tokens(json.dumps(tools, ensure_ascii=False)) if tools else 0,
                      resources=resources, prompts=prompts)

    result = _empty(exit_code=code,
                    server={"name": info.get("name"), "version": info.get("version")},
                    init_latency_ms=round(init_latency_ms, 1),
                    tool_surface=len(tools),
                    surface_tokens=_tokens(json.dumps(tools, ensure_ascii=False)) if tools else 0,
                    resources=resources, prompts=prompts)
    result["ok"] = code == 0
    if code != 0:
        result["error"] = f"server exited with code {code} after the handshake"
    return result


def merged_env(entry: dict[str, Any]) -> dict[str, str] | None:
    """Full environment for a config entry: current env + entry env."""
    extra = entry.get("env")
    if not isinstance(extra, dict) or not extra:
        return None
    env = dict(os.environ)
    env.update({str(k): str(v) for k, v in extra.items()})
    return env
