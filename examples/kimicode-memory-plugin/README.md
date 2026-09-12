# OpenViking Memory Plugin for Kimi Code CLI

Thin Kimi Code CLI adapter for OpenViking long-term memory. Reuses `memory-plugin-shared` — no memory logic is duplicated.

This follows the ZCode plugin layout ([PR #3678](https://github.com/volcengine/OpenViking/pull/3678)) but is **not** a rename. Kimi Code uses TOML `[[hooks]]`, `mcp.json`, `wire.jsonl` transcripts, and extra lifecycle events that ZCode does not have. See [DESIGN.md](./DESIGN.md).

> **Requires an OpenViking server with `viking://~` home-alias support.**

## What it does

- **SessionStart** — replay the offline pending queue (cannot inject; observation-only).
- **UserPromptSubmit** — inject profile once and recall relevant memories as **plain text**.
- **PreToolUse** (`Read|Glob|Grep`) — deny direct `viking://` reads; use MCP tools instead.
- **Stop / PreCompact / SessionEnd** — capture unseen `wire.jsonl` turns in a detached worker, then commit.
- **Interrupt** — same capture, synchronously (this event replaces Stop when the user hits Esc).

## Install

```bash
bash examples/memory-plugin-shared/install.sh --harness kimicode
```

The installer detects `kimi` on `PATH` or `~/.kimi-code/`, copies the runtime to `~/.openviking/agent-integrations/kimicode/`, merges an OpenViking `[[hooks]]` block into `~/.kimi-code/config.toml`, and upserts `mcpServers.openviking` in `~/.kimi-code/mcp.json`. Existing hooks (for example Herdr or Orca) are left in place.

Alternatively, from a Kimi Code session:

```
/plugins install /path/to/examples/kimicode-memory-plugin
```

Then `/reload` or `/new`.

## Tests

```bash
node --test scripts/*.test.mjs
```
