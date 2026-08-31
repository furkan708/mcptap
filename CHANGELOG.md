# Changelog

## 0.5.0 — resources & prompts, battery to 10, unicode tokens, Windows

- The report sees the whole MCP surface now: `resources:` (listed count,
  list-response token price, reads) and `prompts:` (listed, tokens, gets)
  join the tool surface — list responses for them get injected into
  context exactly like tools/list.
- Battery grew from 5 to 10 probes. The five new ones — notification
  flood (300 in a burst), final line without a trailing newline, garbage
  non-JSON lines, CRLF line endings, out-of-order responses — all passed
  on first contact (the 0.4.0 hardening held); they are pinned now, and
  out-of-order matching is pinned by latency, not just by count.
- Token estimate is unicode-aware: ASCII stays chars/4, non-ASCII counts
  ≈1 token per char. Plain chars/4 silently lowballed CJK and other
  non-English content by up to 4×.
- CI gained a Windows job (windows-latest) — Claude Desktop's other home.

## 0.4.0 — adversarial battery: batches, byte-exact passthrough, 0600

Found by probing the tap with what the real wire can do (5-probe
battery, 3 broke, all fixed, all mutation-witnessed):

- **Batches are first-class.** A JSON-RPC batch (one line, an array of
  requests/responses) is unpacked for analysis — calls count, responses
  match, no fake "unanswered" noise.
- **Passthrough is byte-exact.** The tap now runs on binary pipes; a
  server emitting invalid UTF-8 reaches the client bit-for-bit. Bytes
  are sacred — previously they were sacred except when they weren't
  (text-mode pipes silently replaced undecodable bytes).
- **Undecodable lines are preserved base64-exact** in the session file
  and replayed bit for bit.
- **Session files are 0600, the sessions directory 0700.** Sessions
  carry full payloads — arguments may hold secrets; other local users
  are not invited.
- Battery probes for pipelined requests and 1 MB single lines pass
  unchanged (they always did — now it's pinned).

## 0.3.0 — structuredContent layer 0, composition pinned

- Error taxonomy has a new most-trusted layer: a result's
  `structuredContent.error_category` is believed verbatim (mcpify ≥ 1.18
  states it), ahead of leading tokens and keyword heuristics. Proven with
  a fixture whose structured field contradicts both its `retryable:`
  prefix and the keywords — the structured field wins. Per-call
  `http_status` from structuredContent is now recorded too.
- Composition with mcpify is pinned as a test: client → mcptap wrap →
  mcpify serve → fixture HTTP API. Asserts mcpify's own retryable/
  forbidden classification and http statuses survive the tap, and a clean
  session reports clean. Runs in CI as its own job (`composition-mcpify`,
  installs mcpify-openapi from PyPI); skips when mcpify is absent.
- demo/ gains the deterministic fixture API + OpenAPI spec used by the
  composition proof (measured through the tap: 5 tools ≈ 403 tokens,
  p95 401.7 ms, replay: no differences).

## 0.2.0 — watch, diff, replay, real-server smoke

- `mcptap watch` — live-refreshing report of the newest (or given) session,
  with the last few wire lines; `--once` prints a single frame.
- `mcptap diff A B` — structured comparison of two sessions: tools
  added/removed, per-tool token drift, surface total, error-category
  flips per tool, server identity, exit codes. Upgrade triage on a wire
  level.
- `mcptap replay SESSION -- cmd` — re-sends the recorded client script to
  a fresh server and diffs the wire. Paced: after each request it waits
  for the response before sending the next line (real anyio servers such
  as mcp-server-fetch exit on stdin EOF before draining a fire-hose, which
  would fake a regression). Exit code 1 when differences are found —
  usable as a regression gate in scripts.
- Real-server smoke tests against the official `mcp-server-fetch`
  (skipped automatically when not installed; separate CI job installs it).
- Argparse `--` handling is done by hand: REMAINDER swallowed legitimate
  options after the first positional (`replay session --out X -- cmd`).
- `latest_session` tie-breaks on the filename (mtimes can collide).
- CI: lint (ruff + mypy), test matrix on Python 3.10–3.13, optional
  real-server smoke job.

## 0.1.0 — first cut

- `mcptap wrap -- cmd` — stdio passthrough tap: both directions recorded
  to JSONL in `~/.mcptap/sessions/`, bytes forwarded verbatim, exit codes
  propagate, server stderr stays on the client console.
- `mcptap report` — tool surface token price (chars/4), unused tools,
  per-tool p50/p95 latency, layered error taxonomy (leading tokens
  honoured over keywords, JSON-RPC codes, then honest keyword heuristics),
  unanswered requests as the wire-level silent-failure signal, crash
  detection, imperative-description (prompt-injection hygiene) suspects.
- Zero dependencies. Python ≥ 3.10. 11 end-to-end tests including a
  mid-session crasher, ruff + mypy clean.
