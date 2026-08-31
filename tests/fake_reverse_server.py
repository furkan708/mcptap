"""Answers two tools/call requests in REVERSE order: the second request
is answered immediately, the first only after a sleep. Latency analysis
must match responses by id, not by arrival order."""

import json
import sys
import time


def respond(req_id: int, text: str) -> None:
    msg = {"jsonrpc": "2.0", "id": req_id,
           "result": {"content": [{"type": "text", "text": text}]}}
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


buf: list[dict] = []
for line in sys.stdin:
    if not line.strip():
        continue
    msg = json.loads(line)
    if msg.get("method") == "tools/call" and "id" in msg:
        buf.append(msg)
        if len(buf) == 2:
            first, second = buf
            respond(second["id"], "answered first (out of order)")
            time.sleep(0.25)
            respond(first["id"], "answered second")
