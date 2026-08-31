# mcptap

A zero-dependency wire tap for MCP stdio servers.

**Türkçe? → [README.tr.md](README.tr.md)**

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
  session: 0.32s, 13→ client msgs, 12← server msgs, init 19.3ms

tool surface: 4 tools ≈ 163 tokens (1× tools/list)
       47  send_email  ⚠ imperative description
       43  slow_mul
       38  add
       35  delete_everything
  unused (paid for, never called): delete_everything

resources: 1 listed ≈ 44 tokens (1× list, 1× read)
prompts: 1 listed ≈ 27 tokens (1× list, 1× get)

tool calls: 6 total, 4 errors
  2× forbidden
  1× invalid_request
  1× retryable
  latency: p50 319.7ms, p95 319.8ms
  ✗ send_email [retryable] (319.7ms): retryable: upstream SMTP connection timed out after 5000ms
  ✗ boom [forbidden] (319.8ms): 401 Unauthorized: invalid API key
  ✗ lying_label [invalid_request] (319.8ms): invalid_request: connection reset by peer
  ✗ structured_liar [forbidden] (319.8ms): retryable: connection reset (structuredContent wins)

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

0. **structuredContent** — a result that states `error_category` in its
   `structuredContent` (mcpify ≥ 1.18 does) is believed verbatim; its
   `http_status` is recorded with the call.
1. **Leading tokens** — errors that start with `retryable:` /
   `invalid_request:` / `forbidden:` (mcptap-aware servers, mcpify ≥ 1.19)
   are honoured exactly, even when keywords say otherwise.
2. **JSON-RPC error codes** — `-32700/-32600/-32601/-32602` →
   invalid_request, `-32603` → retryable.
3. **Keyword heuristics** — 401/403/unauthorized → forbidden;
   timeout/reset/429/5xx → retryable; 400/404/422/invalid → invalid_request;
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

- public launch once the tool earns it
- `mcptap doctor` — sanity-check a client config's server list the way
  `mcpify doctor` does for HTTP servers
- tapping Streamable HTTP transports, not just stdio
- a unicode-aware token estimate is in (ASCII chars/4, non-ASCII ≈ 1
  token/char); a real tokenizer would be sharper — zero-dep rules it out

## The other commands

**`mcptap watch`** — live-refreshing report of the newest session (or a
given one), plus the last few wire lines. Run it next to your client and
watch the session file grow:

```console
$ mcptap watch              # newest session in ~/.mcptap/sessions
$ mcptap watch --once       # one frame, no loop (for scripts)
```

**`mcptap diff OLD NEW`** — what changed on the wire between two sessions.
The classic use is the same server before/after an upgrade:

```console
$ mcptap diff old.jsonl new.jsonl
mcptap diff — old.jsonl → new.jsonl
  server: fake-math 9.9.9 → fake-math 9.10.0
  + search (54 tokens)
  - delete_everything (35 tokens)
  ~ add: 38 → 52 tokens (+14)
  tool surface total: 163 → 234 tokens (+71)
  ~ send_email errors: {'retryable': 1} → {'forbidden': 1}
```

**`mcptap replay SESSION -- cmd`** — a recorded session becomes a
regression harness: the client script is re-sent to a fresh server and
the wire is diffed against the recording. Exit code 1 on differences, so
it slots into scripts and CI as a gate.

Replays are *paced*: after each request the replay waits for its response
before sending the next line. That is not politeness — real servers
(anyio-based ones like `mcp-server-fetch`) exit on stdin EOF before
draining a fire-hose, which would kill them mid-script and fake a
regression. We found this the honest way, against the real server.

## Proven against a real server

The test suite includes smoke tests around the official `mcp-server-fetch`
(they skip automatically when it isn't installed). Measured through the
tap: `mcp-fetch 1.29.1`, one `fetch` tool worth 290 tokens of surface,
initialize at ~1.7 s, clean exit 0 — report, diff and replay all verified
against it.

It is also pinned in composition with **mcpify**: client → mcptap wrap →
`mcpify serve` → fixture HTTP API, as a CI job of its own. mcpify's own
error taxonomy (`retryable:` / `forbidden:` tokens and
`structuredContent.error_category`) reads correctly through the tap, and a
replay of the recorded session reports no differences. A server that
self-describes its errors and a tap that honours them are two layers, not
competitors.

## Design rules

- **Zero dependencies.** stdlib only; `pip install mcptap` brings nothing
  else. Python ≥ 3.10.
- **Bytes are sacred.** Binary pipes end to end: protocol lines are
  forwarded byte-exact (only a missing trailing newline is added).
  Undecodable lines are preserved base64-exact in the session file and
  replayed bit for bit. JSON-RPC batches and pipelined requests are
  first-class in analysis.
- **Local-first.** Sessions stay in `~/.mcptap/sessions/` — written
  owner-only (files 0600, directory 0700), because payloads may hold
  secrets. Nothing leaves your machine, ever.
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
