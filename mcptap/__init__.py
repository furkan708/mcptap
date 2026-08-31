"""mcptap — see what your MCP client and servers actually say to each other.

A zero-dependency wire tap for the Model Context Protocol: wrap any stdio
MCP server with one config line, record every JSON-RPC message to a local
JSONL session file, and get an honest report of what that traffic cost
(tokens, latency, errors, silent failures) — no SDK, no accounts, no SaaS.
"""

__version__ = "0.1.0"
