"""A Streamable HTTP MCP server fixture with teeth.

Behaviour worth testing against:
- initialize answers JSON and issues Mcp-Session-Id; every later request
  WITHOUT that header gets 400 (forces the proxy to pass headers through)
- tools/call "slow_sse" is answered as text/event-stream with TWO events
  (a notification, then the response) — forces SSE parsing, not body reads
- tools/call "fail_403" returns an isError result with structuredContent
  (layer-0 taxonomy must survive the proxy)
- GET opens an SSE stream with one server-initiated notification
- DELETE terminates the session (204)
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SESSION_ID = "sess-tap-1"

TOOLS = [
    {"name": "add", "description": "Add two integers.",
     "inputSchema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}}},
    {"name": "slow_sse", "description": "Answered over SSE, with a progress event first.",
     "inputSchema": {"type": "object", "properties": {}}},
]


def sse(obj: dict) -> bytes:
    return b"data: " + json.dumps(obj).encode("utf-8") + b"\n\n"


class UpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("upstream: " + fmt % args + "\n")

    def _json(self, code: int, obj: dict, extra: dict | None = None) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _session_ok(self) -> bool:
        return self.headers.get("Mcp-Session-Id") == SESSION_ID

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        msg = json.loads(self.rfile.read(length))
        method = msg.get("method")

        if method == "initialize":
            self._json(
                200,
                {"jsonrpc": "2.0", "id": msg["id"],
                 "result": {"protocolVersion": "2025-06-18",
                            "serverInfo": {"name": "http-fake", "version": "1.0"},
                            "capabilities": {}}},
                extra={"Mcp-Session-Id": SESSION_ID},
            )
            return
        if not self._session_ok():
            self._json(400, {"error": "Mcp-Session-Id header missing or wrong"})
            return
        if method == "tools/list":
            self._json(200, {"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": TOOLS}})
        elif method == "tools/call":
            name = msg["params"]["name"]
            if name == "slow_sse":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(sse({"jsonrpc": "2.0", "method": "notifications/progress",
                                      "params": {"progress": 1}}))
                self.wfile.flush()
                self.wfile.write(sse({"jsonrpc": "2.0", "id": msg["id"],
                                      "result": {"content": [{"type": "text", "text": "done slowly"}]}}))
                self.wfile.flush()
                self.close_connection = True
            elif name == "fail_403":
                self._json(200, {"jsonrpc": "2.0", "id": msg["id"],
                                 "result": {"isError": True,
                                            "content": [{"type": "text", "text": "forbidden: HTTP 403 upstream said no"}],
                                            "structuredContent": {"error_category": "forbidden",
                                                                  "http_status": 403, "retryable": False}}})
            else:
                self._json(200, {"jsonrpc": "2.0", "id": msg["id"],
                                 "result": {"content": [{"type": "text", "text": "42"}]}})
        else:
            self._json(200, {"jsonrpc": "2.0", "id": msg.get("id"),
                             "error": {"code": -32601, "message": f"unknown method {method}"}})

    def do_GET(self) -> None:
        if not self._session_ok():
            self._json(400, {"error": "Mcp-Session-Id header missing or wrong"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(sse({"jsonrpc": "2.0", "method": "notifications/message",
                              "params": {"level": "info", "data": "server says hi"}}))
        self.wfile.flush()
        self.close_connection = True

    def do_DELETE(self) -> None:
        if not self._session_ok():
            self._json(400, {"error": "Mcp-Session-Id header missing or wrong"})
            return
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), UpstreamHandler).serve_forever()
