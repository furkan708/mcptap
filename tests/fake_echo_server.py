"""A byte-exact echo server: returns everything stdin gave it, verbatim.
Used to prove the tap forwards bytes (CR, LF, everything) untouched."""

import sys

data = sys.stdin.buffer.read()
sys.stdout.buffer.write(data)
sys.stdout.buffer.flush()
