"""wrap-http tests: a local reverse proxy in front of a Streamable HTTP
MCP server. The client talks to the proxy; the wire must be glass
(headers, SSE events, statuses) and the session file must see it all."""

import http.client
import json
import threading
from http.server import ThreadingHTTPServer

import pytest

from mcptap.analysis import analyze, load

from .http_upstream import SESSION_ID, UpstreamHandler

UPSTREAM_TIMEOUT = 10


@pytest.fixture()
def upstream():
    server = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/mcp"
    server.shutdown()


@pytest.fixture()
def tap(upstream, tmp_path):
    from mcptap.http_proxy import make_server

    httpd, writer = make_server(upstream, "127.0.0.1", 0, tmp_path / "http-session.jsonl")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd.server_address[1], tmp_path / "http-session.jsonl"
    httpd.shutdown()
    writer.close()


def _post(port: int, obj: dict, session: str | None = None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=UPSTREAM_TIMEOUT)
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    if session:
        headers["Mcp-Session-Id"] = session
    conn.request("POST", "/mcp", body=json.dumps(obj).encode(), headers=headers)
    resp = conn.getresponse()
    return resp, resp.read()


def _handshake(port: int) -> str:
    resp, _body = _post(port, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert resp.status == 200
    session = resp.getheader("Mcp-Session-Id")
    assert session == SESSION_ID, "session header must reach the client"
    return session


def test_http_full_session_through_proxy(tap):
    port, session_path = tap
    session = _handshake(port)
    resp, body = _post(port, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, session=session)
    assert resp.status == 200, "upstream must see the Mcp-Session-Id the client sent"
    tools = json.loads(body)["result"]["tools"]
    assert [t["name"] for t in tools] == ["add", "slow_sse"]

    _post(port, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                 "params": {"name": "add", "arguments": {"a": 1, "b": 2}}}, session=session)
    _post(port, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                 "params": {"name": "fail_403", "arguments": {}}}, session=session)

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=UPSTREAM_TIMEOUT)
    conn.request("DELETE", "/mcp", headers={"Mcp-Session-Id": session})
    assert conn.getresponse().status == 204
    conn.close()

    report = analyze(load(session_path))
    assert report["server"] == {"name": "http-fake", "version": "1.0"}
    assert report["tool_surface"]["count"] == 2
    by_tool = {c["tool"]: c for c in report["tool_calls"]["detail"]}
    assert by_tool["add"]["error"] is False
    assert by_tool["fail_403"]["error_category"] == "forbidden"  # layer 0 survived the proxy
    assert by_tool["fail_403"]["http_status"] == 403
    assert report["unanswered_requests"] == []
    assert report["lifecycle"]["crashed"] is False


def test_http_missing_session_header_is_forwarded_honestly(tap):
    port, _ = tap
    resp, body = _post(port, {"jsonrpc": "2.0", "id": 9, "method": "tools/list"})  # no session
    assert resp.status == 400, "the proxy is glass: upstream's 400 must reach the client"
    assert b"Mcp-Session-Id" in body


def test_http_sse_response_recorded_event_by_event(tap):
    port, session_path = tap
    session = _handshake(port)
    resp, body = _post(port, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                              "params": {"name": "slow_sse", "arguments": {}}}, session=session)
    assert resp.getheader("Content-Type", "").startswith("text/event-stream")
    assert body.count(b"data:") == 2, "both SSE events must reach the client"

    report = analyze(load(session_path))
    s2c = report["messages"]["server_to_client"]
    assert s2c >= 3  # initialize + progress notification + response
    by_tool = {c["tool"]: c for c in report["tool_calls"]["detail"]}
    assert by_tool["slow_sse"]["latency_ms"] is not None, "SSE response must still match its request"
    assert by_tool["slow_sse"]["error"] is False


def test_http_get_sse_stream_passthrough(tap):
    port, session_path = tap
    session = _handshake(port)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=UPSTREAM_TIMEOUT)
    conn.request("GET", "/mcp", headers={"Mcp-Session-Id": session, "Accept": "text/event-stream"})
    resp = conn.getresponse()
    body = resp.read()
    assert resp.status == 200
    assert b"notifications/message" in body, "server-initiated SSE must cross the proxy"

    report = analyze(load(session_path))
    assert report["messages"]["server_to_client"] >= 2  # initialize + the notification
