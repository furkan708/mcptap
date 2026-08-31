"""The wire tap itself: spawn a stdio MCP server and tee both directions.

Design notes
------------
- The child's stderr is inherited (logs stay visible to the client, which
  is where MCP servers are supposed to log).
- Every protocol line is recorded as one JSON object per line: the parsed
  JSON-RPC message when it parses, else the raw text — a broken peer must
  never break the tap.
- Lifecycle events (start / exit / early stdout close) are recorded in the
  same stream, so a session file is fully self-describing.
- Bytes are forwarded verbatim (only the trailing newline is normalized),
  so framing survives even for clients that send unusual line endings.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, TextIO

DEFAULT_SESSIONS_DIR = Path.home() / ".mcptap" / "sessions"


class SessionWriter:
    """Thread-safe JSONL appender with a monotonic timeline.

    Session files carry full payloads — arguments may hold secrets — so
    the file is 0600 and the sessions directory 0700, always.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.parent.chmod(0o700)
        except OSError:
            pass
        self._lock = threading.Lock()
        self._file: TextIO = path.open("a", encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def event(self, kind: str, **fields: Any) -> None:
        self.write({"event": kind, **fields})

    def message(self, direction: str, line: str) -> None:
        try:
            data: Any = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            data = {"raw": line}
        self.write({"dir": direction, "data": data})

    def message_bytes(self, direction: str, raw: bytes) -> None:
        """Record one wire line from bytes.

        UTF-8 that parses as JSON is stored parsed; UTF-8 that does not
        parse is stored as text; anything undecodable is stored base64-
        exact so a replay can resend it bit for bit.
        """
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            self.write(
                {
                    "dir": direction,
                    "raw_b64": base64.b64encode(raw).decode("ascii"),
                    "decode": "failed",
                }
            )
            return
        self.message(direction, text)

    def write(self, record: dict[str, Any]) -> None:
        payload = json.dumps({"ts": time.time(), **record}, ensure_ascii=False)
        with self._lock:
            self._file.write(payload + "\n")
            self._file.flush()

    def close(self) -> None:
        with self._lock:
            self._file.close()


def _pump_client_to_server(writer: SessionWriter, child: subprocess.Popen[bytes]) -> None:
    assert child.stdin is not None
    for line in iter(sys.stdin.buffer.readline, b""):
        if not line.endswith(b"\n"):
            line += b"\n"
        writer.message_bytes("c2s", line)
        try:
            child.stdin.write(line)
            child.stdin.flush()
        except (BrokenPipeError, ValueError):
            writer.event("server_stdin_closed")
            break
    try:
        child.stdin.close()
    except (BrokenPipeError, OSError):
        pass


def _pump_server_to_client(writer: SessionWriter, child: subprocess.Popen[bytes]) -> None:
    assert child.stdout is not None
    for line in iter(child.stdout.readline, b""):
        if not line.endswith(b"\n"):
            line += b"\n"
        writer.message_bytes("s2c", line)
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()
    writer.event("server_stdout_closed")


def default_session_path(argv: list[str]) -> Path:
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe = Path(argv[0]).name if argv else "server"
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in safe)[:40] or "server"
    return DEFAULT_SESSIONS_DIR / f"{stamp}-{safe}.jsonl"


def wrap(argv: list[str], out_path: Path | None = None) -> int:
    """Run argv as a stdio server, teeing traffic. Returns the child's code."""
    if not argv:
        raise ValueError("no command given")
    writer = SessionWriter(out_path or default_session_path(argv))
    writer.event("start", argv=argv, tap_version=_tap_version())

    # The operator's own command — that is the product. Binary pipes: the
    # tap forwards bytes, and only decodes (best effort) to take notes.
    child = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,  # inherited: server logs belong to the client's console
    )
    to_server = threading.Thread(
        target=_pump_client_to_server, args=(writer, child), daemon=True
    )
    to_client = threading.Thread(
        target=_pump_server_to_client, args=(writer, child), daemon=True
    )
    to_server.start()
    to_client.start()

    try:
        code = child.wait()
    except KeyboardInterrupt:
        child.terminate()
        code = child.wait()
        writer.event("interrupted")
    # The child has exited, but its stdout pipe may still hold undrained
    # bytes the pump thread has not forwarded yet — join before exiting.
    to_client.join(timeout=2.0)
    writer.event("exit", code=code)
    writer.close()
    sys.stderr.write(f"mcptap: session saved to {writer.path}\n")
    return code


def _tap_version() -> str:
    from . import __version__

    return __version__
