# OpenViking Memory — Kimi Code plugin

Long-term semantic memory for [Kimi Code CLI](https://github.com/MoonshotAI/kimi-code),
powered by [OpenViking](https://github.com/volcengine/OpenViking).

Recall on every prompt, incremental capture of the conversation (user text,
assistant text, tool calls and results) into a long-lived OpenViking session,
and OpenViking's MCP tools for active retrieval — installed as a native Kimi
plugin, no bash installer required.

This is a community implementation, not an official Kimi Code / Moonshot AI
plugin.

## Install

Requires: Kimi Code CLI ≥ 2.1.1 (verified on 2.2.0), Node.js ≥ 18, and a
reachable OpenViking server (a local `127.0.0.1:1933` needs no credentials).
Plugin version: **0.2.4** (`kimi.plugin.json`).

```text
/plugins install <absolute-path-to-this-directory-or-a-zip-url>
```

The plugin lives in a monorepo subdirectory, so the GitHub-URL form does not
apply (kimi-code installs from the repo root only); use the local directory
or a zip with the manifest at the archive root.

Do not install this host with the shared `install.sh`: it writes a different
plugin under the same id, which would replace this copy. Install and upgrade
through `/plugins install`.

Then `/reload` (or start a new session). Verify with:

```bash
node scripts/ov-memory-doctor.mjs --offline
```

## Upgrade and uninstall

The CLI always runs the managed copy under
`$KIMI_CODE_HOME/plugins/managed/openviking-memory/`, never this source
directory — editing the source has no effect until reinstalled.

```text
/plugins install <same source again>   # upgrade: replaces the managed copy
/plugins remove openviking-memory      # uninstall: hooks, MCP and skills go away
```

Reinstalling and removing never touch your OpenViking server, memories,
credentials or `~/.openviking/kimicode-plugin-state`; only the plugin files
and Kimi's registration of them change.

## What it does

| Hook | Behavior |
|---|---|
| `UserPromptSubmit` | searches OpenViking for memories related to your prompt and injects them as context (raw stdout text — the only channel Kimi reliably appends) |
| `Stop` / `PreCompact` / `SessionEnd` / `Interrupt` | decodes the session's `wire.jsonl` incrementally and appends new user/assistant/tool messages to `kc-<session-id>`; commits on session end, before compaction, or once enough turns were captured (`commitTurnThreshold`) |
| `SessionStart` | replays a bounded batch of anything that failed to send earlier |
| `PreToolUse` | denies `Read`/`Glob`/`Grep` on `viking://` URIs and points the model at the MCP tools |

MCP tools (`find`, `search`, `grep`, `read`, `remember`, `add_resource`,
`add_skill`, …) are proxied to the server over stdio, and the bundled skills
(`openviking-memory`, `openviking-skills`, `ov-experience-memory`) teach the
model when to use them.

All hooks run synchronously — no detached background workers — so behavior is
identical on Windows, macOS and Linux.

## Configuration

Knobs are declared once in the shared schema; resolve order: `OPENVIKING_*`
env → workspace `.openviking/config*.json` → machine registry → `ovcli.conf`
`plugin.kimicode` → `plugin` → defaults. Connection and credentials come from
env vars or the active `ovcli.conf` (`ov config switch` moves hooks and MCP
together). The ones you are most likely to touch:

| Knob (env) | Default | Effect |
|---|---|---|
| `OPENVIKING_AUTO_RECALL` | on | recall + profile injection on each prompt |
| `OPENVIKING_AUTO_CAPTURE` | on | incremental capture on Stop/PreCompact/SessionEnd/Interrupt |
| `OPENVIKING_COMMIT_TURN_THRESHOLD` | 8 | turns captured between threshold commits |
| `OPENVIKING_AUTO_COMMIT_ON_COMPACT` | on | commit before compaction |
| `OPENVIKING_RECALL_LIMIT` / `OPENVIKING_SCORE_THRESHOLD` | 10 / 0.35 | recall breadth and cutoff |
| `OPENVIKING_RECALL_PEER_SCOPE` | `all` | `actor` narrows recall to this workspace's peer |
| `OPENVIKING_PENDING_REPLAY_LIMIT` | 2 (default in code; the manifest cannot carry it) | pending entries replayed per SessionStart |

The full knob list is in
[`memory-plugin-shared`](https://github.com/volcengine/OpenViking/tree/main/examples/memory-plugin-shared)
(`lib/config-schema.mjs`). Kimi-specific state lives in
`~/.openviking/kimicode-plugin-state` (`OPENVIKING_KIMICODE_STATE_DIR` to move
it). See `DESIGN.md` for the full host contract and design record.

## Development

`scripts/shared/` and `skills/` are generated. The plugin is registered in
`sync.mjs` (`TARGETS` and `SKILL_TARGETS`), so change a shared source and run
the generator instead of editing the copies:

```bash
node ../memory-plugin-shared/sync.mjs   # after changing shared sources
node --test scripts/*.test.mjs
```

Host-specific behaviour the shared library does not provide — `turn_id` on
shaped turns, the host envelope rules, the length cap, prefix-based send
confirmation, reserved-tag neutralisation and the flat-manifest budget check —
lives in `capture-shape.mjs`, `recall-sanitize.mjs`, `ov-session.mjs` and
`ov-memory-doctor.mjs`.

Layout, boundaries and the acceptance checklist follow
[the plugin development spec](../../docs/zh/agent-integrations/18-plugin-development.md).

## Status

Beta. Plugin version 0.2.4, verified against Kimi Code CLI 2.2.0 on Windows
(macOS/Linux expected but unverified). Known gap: subagent conversations
(`agents/<subagent>/wire.jsonl`) are not captured yet (v2); see `DESIGN.md`.
