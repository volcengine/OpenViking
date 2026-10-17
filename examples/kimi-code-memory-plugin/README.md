# OpenViking Memory — Kimi Code plugin

Long-term semantic memory for [Kimi Code CLI](https://github.com/MoonshotAI/kimi-code),
powered by [OpenViking](https://github.com/volcengine/OpenViking).

Recall on every prompt, incremental capture of the conversation (user text,
assistant text, tool calls and results) into a long-lived OpenViking session,
and OpenViking's MCP tools for active retrieval — installed as a native Kimi
plugin, no bash installer required.

## Install

Requires: Kimi Code CLI ≥ 2.1.1, Node.js ≥ 18, and a reachable OpenViking
server (a local `127.0.0.1:1933` needs no credentials).

```text
/plugins install <path-to-this-directory-or-a-git-url>
```

Then `/reload` (or start a new session). Verify with:

```bash
node scripts/ov-memory-doctor.mjs kimicode --offline
```

## What it does

| Hook | Behavior |
|---|---|
| `UserPromptSubmit` | searches OpenViking for memories related to your prompt and injects them as context (raw stdout text — the only channel Kimi reliably appends) |
| `Stop` / `PreCompact` / `SessionEnd` / `Interrupt` | decodes the session's `wire.jsonl` incrementally and appends new user/assistant/tool messages to `kc-<session-id>`; commits on session end, before compaction, or at the token threshold |
| `SessionStart` | replays a bounded batch of anything that failed to send earlier |
| `PreToolUse` | denies `Read`/`Glob`/`Grep` on `viking://` URIs and points the model at the MCP tools |

MCP tools (`find`, `search`, `grep`, `read`, `remember`, `add_resource`,
`add_skill`, …) are proxied to the server over stdio, and the bundled skills
(`openviking-memory`, `openviking-skills`, `ov-experience-memory`) teach the
model when to use them.

All hooks run synchronously — no detached background workers — so behavior is
identical on Windows, macOS and Linux.

## Configuration

Every knob lives in the shared schema; resolve order: `OPENVIKING_*` env →
workspace `.openviking/config*.json` → machine registry → `ovcli.conf`
`plugin.kimicode` → `plugin` → defaults. Connection and credentials come from
env vars or the active `ovcli.conf` (`ov config switch` moves hooks and MCP
together). See `DESIGN.md` for the full host contract and design record.

## Development

`scripts/shared/` and `skills/` are generated — do not edit:

```bash
node ../memory-plugin-shared/sync.mjs   # after changing shared sources
node --test scripts/*.test.mjs
```

Layout, boundaries and the acceptance checklist follow
[the plugin development spec](../../docs/zh/agent-integrations/18-plugin-development.md).

## Status

Beta. Verified against Kimi Code CLI 2.2.0 on Windows. Known gaps (subagent
content capture, doctor install checks) are listed in `DESIGN.md`.
