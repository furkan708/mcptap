"""Fixture HTTP API for the mcptap × mcpify composition demo.

Deterministic endpoints so the wire story is readable:
  GET /ok      -> 200 JSON
  GET /slow    -> 200 JSON after ~0.4s (visible in per-tool p95)
  GET /err500  -> 500 (mcpify should surface retryable:)
  GET /err401  -> 401 (mcpify should surface forbidden:)
"""

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/ok"):
            body = {"status": "fine", "pets": [{"id": 1, "name": "Rex"}]}
            self._send(200, body)
        elif self.path.startswith("/slow"):
            time.sleep(0.4)
            self._send(200, {"status": "slow but fine"})
        elif self.path.startswith("/err500"):
            self._send(500, {"error": "internal petstore failure"})
        elif self.path.startswith("/err401"):
            self._send(401, {"error": "missing or invalid API key"})
        else:
            self._send(404, {"error": "no such endpoint"})

    def _send(self, code: int, body: dict) -> None:
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("fixture-api: " + fmt % args + "\n")


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
