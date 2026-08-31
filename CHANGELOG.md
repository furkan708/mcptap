# Changelog

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
