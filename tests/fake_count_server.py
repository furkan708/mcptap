"""Counts every line it receives (parsed or not) and answers a `count`
request with the total. Proves the tap forwards EVERY line — garbage,
notifications, everything."""

import json
import sys

count = 0
for line in sys.stdin.buffer:
    count += 1
    try:
        msg = json.loads(line)
    except (ValueError, UnicodeDecodeError):
        continue
    if isinstance(msg, dict) and msg.get("method") == "count" and "id" in msg:
        out = json.dumps({"jsonrpc": "2.0", "id": msg["id"],
                          "result": {"content": [{"type": "text", "text": str(count)}]}})
        sys.stdout.buffer.write(out.encode("utf-8") + b"\n")
        sys.stdout.buffer.flush()
