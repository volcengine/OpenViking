# OpenViking Memory Plugin for Kimi Code CLI

Thin Kimi Code CLI adapter for OpenViking long-term memory. Reuses `memory-plugin-shared` — no memory logic is duplicated.

The Kimi-specific manifest, lifecycle wiring, and `wire.jsonl` transcript adapter are kept here; shared runtime modules are generated from `../memory-plugin-shared/lib/` by `sync.mjs`. Kimi Code has its own native plugin manifest, hook lifecycle, MCP declarations, and transcript format. See [DESIGN.md](./DESIGN.md).

> **Requires an OpenViking server with `viking://~` home-alias support.**
>
> **Tested with Kimi Code CLI 0.43.1. Older releases are not validated.**

## What it does

- **SessionStart** — replay the offline pending queue (cannot inject; observation-only).
- **UserPromptSubmit** — inject profile once and recall relevant memories as **plain text**.
- **PreToolUse** (`Read|Glob|Grep`) — deny direct `viking://` reads; use MCP tools instead.
- **Stop / PreCompact / SessionEnd** — capture unseen `wire.jsonl` turns in a detached worker; commit at compaction/session end or when the configured threshold is reached.
- **Interrupt** — same capture, synchronously (this event replaces Stop when the user hits Esc).

## Install

```bash
bash examples/memory-plugin-shared/install.sh --harness kimicode
```

The installer copies the native plugin into `$KIMI_CODE_HOME/plugins/managed/openviking-memory` and records it in `$KIMI_CODE_HOME/plugins/installed.json`. Existing `config.toml` and `mcp.json` entries are not modified.

Alternatively, from a Kimi Code session:

```
/plugins install /path/to/examples/kimicode-memory-plugin
```

Then `/reload` or `/new`.

## Tests

```bash
node --test scripts/*.test.mjs
```
