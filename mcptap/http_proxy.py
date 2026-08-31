"""wrap-http: a local reverse proxy in front of a Streamable HTTP MCP
server. Point your client at the local URL; the proxy forwards to the
upstream and records both directions into the same session JSONL.

Glass by design:
- request headers cross untouched (Authorization, Mcp-Session-Id,
  MCP-Protocol-Version, Accept…), minus hop-by-hop plumbing; the upstream
  session header on initialize reaches the client exactly as sent
- upstream statuses reach the client exactly (a 400 is a 400)
- SSE responses (POST or GET opened streams) are forwarded line by line
  and recorded event by event — a stream is many messages, not one body
- DELETE terminates the upstream session and is recorded

The proxy binds 127.0.0.1 by default: it has no auth of its own, and it
forwards yours. Don't bind it wider than your trust.
"""

from __future__ import annotations

import http.client
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .recorder import SessionWriter, default_session_path

HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
    "accept-encoding",  # we record what crosses the wire: no gzip mangling
}

DEFAULT_TIMEOUT_S = 30.0


class TapProxy:
    def __init__(self, upstream: str, writer: SessionWriter, timeout: float = DEFAULT_TIMEOUT_S) -> None:
        self.upstream = upstream
        self.writer = writer
        self.timeout = timeout
        parts = urlsplit(upstream)
        self.scheme = parts.scheme or "http"
        self.host = parts.hostname or "127.0.0.1"
        self.port = parts.port or (443 if self.scheme == "https" else 80)
        self.path = parts.path or "/"
        if parts.query:
            self.path += "?" + parts.query

    def connection(self) -> http.client.HTTPConnection:
        if self.scheme == "https":
            return http.client.HTTPSConnection(self.host, self.port, timeout=self.timeout)
        return http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)

    def send_upstream(self, method: str, headers: dict[str, str], body: bytes | None):
        conn = self.connection()
        fwd = dict(headers)
        if body is not None:
            fwd.setdefault("Content-Length", str(len(body)))
        conn.request(method, self.path, body=body, headers=fwd)
        return conn, conn.getresponse()


def make_handler(proxy: TapProxy) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: object) -> None:
            pass  # the session file is the log; stderr stays for tap notices

        def _upstream_headers(self) -> dict[str, str]:
            headers = {
                k: v for k, v in self.headers.items()
                if k.lower() not in HOP_HEADERS
            }
            headers["Accept-Encoding"] = "identity"
            return headers

        def _start_response(self, status: int, reason: str,
                            upstream: http.client.HTTPResponse) -> None:
            self.send_response(status, reason)
            for key, value in upstream.getheaders():
                if key.lower() in HOP_HEADERS:
                    continue
                self.send_header(key, value)
            self.send_header("Connection", "close")  # close-delimited: no chunking games
            self.end_headers()

        def _relay_sse(self, resp: http.client.HTTPResponse) -> None:
            """Forward an SSE stream line by line; record each event."""
            self._start_response(resp.status, resp.reason, resp)
            data_lines: list[str] = []
            try:
                for raw in iter(resp.readline, b""):
                    self.wfile.write(raw)
                    self.wfile.flush()
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    if line == "":
                        if data_lines:
                            payload = "\n".join(data_lines)
                            proxy.writer.message_bytes("s2c", payload.encode("utf-8"))
                            data_lines = []
                        continue
                    if line.startswith("data:"):
                        data_lines.append(line[len("data:"):].lstrip())
            except (ConnectionError, BrokenPipeError, OSError):
                pass  # client hung up mid-stream; the stream is theirs to close
            finally:
                self.close_connection = True

        def _relay_body(self, resp: http.client.HTTPResponse) -> None:
            data = resp.read()
            if data:
                proxy.writer.message_bytes("s2c", data)
            self.send_response(resp.status, resp.reason)
            for key, value in resp.getheaders():
                if key.lower() in HOP_HEADERS:
                    continue
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)

        def _proxy(self, method: str) -> None:
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(length) if length else None
            if body:
                proxy.writer.message_bytes("c2s", body)
            try:
                conn, resp = proxy.send_upstream(method, self._upstream_headers(), body)
            except OSError as exc:
                proxy.writer.event("upstream_error", detail=str(exc))
                self.send_response(502)
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                return
            try:
                if method == "DELETE":
                    proxy.writer.event("http_delete", status=resp.status)
                ctype = resp.getheader("Content-Type", "")
                if ctype.startswith("text/event-stream"):
                    self._relay_sse(resp)
                else:
                    self._relay_body(resp)
            finally:
                conn.close()

        def do_POST(self) -> None:
            self._proxy("POST")

        def do_GET(self) -> None:
            self._proxy("GET")

        def do_DELETE(self) -> None:
            self._proxy("DELETE")

        def do_PUT(self) -> None:
            self._proxy("PUT")

    return Handler


def make_server(
    upstream: str,
    host: str = "127.0.0.1",
    port: int = 0,
    out_path: Path | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> tuple[ThreadingHTTPServer, SessionWriter]:
    """Build (and bind) the tap proxy; caller runs serve_forever()."""
    writer = SessionWriter(out_path or default_session_path(["wrap-http", upstream]))
    proxy = TapProxy(upstream, writer, timeout)
    httpd = ThreadingHTTPServer((host, port), make_handler(proxy))
    writer.event(
        "proxy_start",
        upstream=upstream,
        listen_host=httpd.server_address[0],
        listen_port=httpd.server_address[1],
        path=proxy.path,
    )
    return httpd, writer


def main_wrap_http(
    upstream: str, host: str = "127.0.0.1", port: int = 0,
    out_path: Path | None = None, timeout: float = DEFAULT_TIMEOUT_S,
) -> int:
    httpd, writer = make_server(upstream, host, port, out_path, timeout)
    addr = httpd.server_address
    bound_host = addr[0]
    bound_port = addr[1]
    if isinstance(bound_host, bytes):  # not TCP, but satisfy the type honestly
        bound_host = bound_host.decode("ascii", errors="replace")
    sys.stderr.write(
        f"mcptap: tapping {upstream}\nmcptap: point your client at "
        f"http://{bound_host}:{bound_port} — recording to {writer.path}\n"
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        writer.event("proxy_stop")
        writer.close()
        httpd.server_close()
    return 0
