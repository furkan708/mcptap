# mcptap

A zero-dependency wire tap for MCP stdio servers.

You wrap any MCP server with one config line. mcptap records every JSON-RPC
message between your client and the server to a local JSONL file, and tells
you what that traffic actually did: token price of the tool surface, which
tools you pay for and never call, per-tool latency, errors with a taxonomy,
servers that crash mid-session, and tool descriptions that read like
instructions to your model.

No SDK. No accounts. No SaaS. stdout stays a clean protocol channel; the tap
talks on stderr and writes to `~/.mcptap/sessions/`.

## Turn it on

One line. Instead of running the server directly, run it through the tap:

```json
{
  "mcpServers": {
    "fetch": {
      "command": "mcptap",
      "args": ["wrap", "--", "uvx", "mcp-server-fetch"]
    }
  }
}
```

Works with any stdio client — Claude Desktop, Cursor, Claude Code, anything
that speaks MCP over stdio — and any stdio server, in any language. The tap
doesn't parse your protocol to forward it; it only reads it to measure.

Install (or run without installing: `uvx --from mcptap mcptap wrap -- …`):

```console
$ pip install mcptap
```

## What you get

Every session is a file. Point the report at it:

```console
$ mcptap report ~/.mcptap/sessions/20260831-101500-uvx.jsonl
mcptap report — 20260831-101500-uvx.jsonl
  server: fake-math 9.9.9
  session: 0.32s, 8→ client msgs, 7← server msgs, init 14.9ms

tool surface: 4 tools ≈ 163 tokens (1× tools/list)
       47  send_email  ⚠ imperative description
       43  slow_mul
       38  add
       35  delete_everything
  unused (paid for, never called): delete_everything

tool calls: 5 total, 3 errors
  1× forbidden
  1× invalid_request
  1× retryable
  latency: p50 315.4ms, p95 315.5ms
  ✗ send_email [retryable] (315.4ms): retryable: upstream SMTP connection timed out after 5000ms
  ✗ boom [forbidden] (315.4ms): 401 Unauthorized: invalid API key
  ✗ lying_label [invalid_request] (315.5ms): invalid_request: connection reset by peer

prompt-injection suspects (imperative tool descriptions): send_email

lifecycle: clean exit (code=0)
```

`--json` gives you the same report as a machine-readable document.

### Why the numbers matter

A `tools/list` response is injected into your model's context at every
surface refresh. Measured on the wire with real servers: a GitHub MCP
server alone advertises a surface worth ~290,000 tokens (chars/4
heuristic); a lazy-loading variant of the same surface: ~291. A tool you
never call is not free — you pay rent on its description on every turn.
mcptap shows you the invoice you were already paying.

### Error taxonomy

Errors are categorized in layers, most-trusted first:

1. **Leading tokens** — errors that start with `retryable:` /
   `invalid_request:` / `forbidden:` (mcptap-aware servers, mcpify ≥ 1.19)
   are honoured exactly, even when keywords say otherwise.
2. **JSON-RPC error codes** — `-32700/-32600/-32601/-32602` →
   invalid_request, `-32603` → retryable.
3. **Keyword heuristics** — 401/403/unauthorized → forbidden;
   timeout/reset/429/5xx → retryable; 400/404/22/invalid → invalid_request;
   anything else is honestly labelled `unknown`.

### Silent failures

A server that dies mid-session doesn't announce itself on stdout — the
client just stops getting answers. mcptap flags **unanswered requests**
(client messages with an id that never got a response), the exit code, and
whether stdout closed before the session ended. A crash can't hide behind a
quiet client.

### Prompt-injection suspects

A tool *description* that starts with "you must", "always", "ignore",
"before calling", "important:" is phrased as an instruction to the model,
not a description for it. Most tool-poisoning payloads look like this.
mcptap flags them as suspects — a hygiene signal to look at, never a
verdict.

## Roadmap

- `mcptap watch` — live tail of the current session (tap running, report
  refreshing).
- `mcptap diff` — compare two sessions' tool surfaces: what got more
  expensive after a server upgrade.
- `mcptap replay` — feed a recorded client script back to a server;
  regression tests for your MCP wiring with zero test infrastructure.

## Design rules

- **Zero dependencies.** stdlib only; `pip install mcptap` brings nothing
  else. Python ≥ 3.10.
- **Bytes are sacred.** Protocol lines are forwarded verbatim (only the
  trailing newline is normalized). Unparseable lines are recorded as raw
  text — a broken peer never breaks the tap.
- **Local-first.** Sessions stay in `~/.mcptap/sessions/`. Nothing leaves
  your machine, ever.
- **stderr talks, stdout forwards.** The client sees exactly the protocol
  the server sent; tap notices go to stderr.
- **Exit codes propagate.** If the server crashes with code 3, the client
  sees code 3 — the tap is glass, not a cushion.

## Development

```console
$ pip install -e . pytest
$ python -m pytest
```

Tests run the real end-to-end path: `python -m mcptap wrap` around fixture
servers, including one that crashes mid-session, then assert on both the
forwarded protocol and the recorded session.

## License

MIT
