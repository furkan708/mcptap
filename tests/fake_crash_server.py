"""A stdio server that answers initialize, then dies mid-session
(exit code 3) while the client is still talking. Fixture for crash
detection tests."""

import json
import sys


def main() -> None:
    answered = False
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        if msg.get("method") == "initialize" and "id" in msg and not answered:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"serverInfo": {"name": "crasher", "version": "0.0.1"}}}) + "\n")
            sys.stdout.flush()
            answered = True
            sys.stderr.write("crasher: segfault simulated in worker\n")
            sys.exit(3)


if __name__ == "__main__":
    main()
