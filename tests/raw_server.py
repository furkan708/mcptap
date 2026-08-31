"""A server that misbehaves on purpose: answers one request properly,
then emits a line of INVALID UTF-8 bytes on stdout, then a final line
with NO trailing newline, then exits cleanly. A wire tap must forward
those bytes exactly, not their apology."""

import sys

sys.stdout.buffer.write(
    b'{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"ok"}]}}\n'
)
sys.stdout.buffer.write(b'\xff\xfe{"poison":"\xc3\x28"}\n')
sys.stdout.buffer.write(b'{"partial":"final-line-without-newline"}')
sys.stdout.buffer.flush()
