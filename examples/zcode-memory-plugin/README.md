# OpenViking Memory Plugin for ZCode

This package provides a ZCode lifecycle adapter for OpenViking long-term memory. It reuses the shared `memory-plugin-shared` runtime — no memory logic is duplicated. Only a thin ZCode adapter is new.

## What it does

- **SessionStart** — injects user profile and preferences/entities into context.
- **UserPromptSubmit** — searches OpenViking for relevant memories and injects them.
- **PreToolUse** (`Read|Glob|Grep`) — denies direct access to `viking://` URIs, redirects to MCP tools.
- **Stop** — captures incremental user/assistant turns and commits the OpenViking session.

ZCode does not support `PreCompact`/`SessionEnd`/`SubagentStart`/`SubagentStop`, so the commit-on-`Stop` strategy (mirroring TRAE and Codex) compensates for the absence of compact/end-of-session signals.

## Install

Use the shared installer:

```bash
bash examples/memory-plugin-shared/install.sh --harness zcode
```

The installer detects ZCode via `~/.zcode/` or a `zcode` binary, merges hooks and MCP config into `~/.zcode/cli/config.json`, and writes OpenViking credentials to `~/.openviking/ovcli.conf`.

## Architecture

The plugin vendors the shared runtime into `scripts/shared/` via `sync.mjs`. The dispatcher (`zcode-hook.mjs`) branches on event name; four thin shim scripts set an environment variable and import the dispatcher. All memory logic (recall, capture, commit, dedup, pending queue, credential resolution, MCP proxy) is provided by the shared runtime.

See [DESIGN.md](./DESIGN.md) for verified ZCode extension-surface facts and decision provenance.

## Tests

```bash
node --test scripts/zcode-turns.test.mjs scripts/zcode-hooks.test.mjs
```
