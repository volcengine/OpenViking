# OpenViking Memory Plugin for Kimi Code CLI

Thin Kimi Code CLI adapter for OpenViking long-term memory. Reuses `memory-plugin-shared` — no memory logic is duplicated.

This follows the ZCode plugin layout ([PR #3678](https://github.com/volcengine/OpenViking/pull/3678)) but is **not** a rename. Kimi Code has its own native plugin manifest, hook lifecycle, MCP declarations, and `wire.jsonl` transcripts. See [DESIGN.md](./DESIGN.md).

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

The installer registers this directory through `kimi plugin install`. Kimi then reads `kimi.plugin.json` and owns the hook and MCP lifecycle. Existing `config.toml` and `mcp.json` entries are not modified.

Alternatively, from a Kimi Code session:

```
/plugins install /path/to/examples/kimicode-memory-plugin
```

Then `/reload` or `/new`.

## Tests

```bash
node --test scripts/*.test.mjs
```
