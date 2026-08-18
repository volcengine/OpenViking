# OpenViking Agent Plugins (Agent Plugins 1.0)

Portable [Agent Plugins 1.0](https://agent-plugins.org/specification) package for [OpenViking](https://github.com/volcengine/OpenViking): long-term semantic memory and context for coding agents.

Agent Plugins 1.0 is a vendor-neutral packaging format for extending AI coding agents, backed by Amazon, Cursor, Microsoft, OpenAI, and Vercel among others. A plugin is a plain directory with a `plugin.json` manifest, auto-discovered Agent Skills under `skills/`, and optional MCP server declarations in `mcp.json` — one package that any conforming client (Cursor, VS Code, Amazon- and OpenAI-side clients, ...) can load the same way. This directory is that package for OpenViking.

## What's inside

```
plugin.json                          # Agent Plugins 1.0 manifest
mcp.json                             # stdio MCP server: "openviking"
servers/mcp-proxy.mjs                # stdio -> streamable-HTTP proxy to the OV server's /mcp
servers/config.mjs, debug-log.mjs    # credential/config resolution (adapted from claude-code-memory-plugin)
servers/shared/                      # generated from examples/memory-plugin-shared/lib (do not edit)
skills/openviking-memory/SKILL.md    # teaches the model the recall + persist loop
plugin.test.mjs                      # node --test conformance checks
```

Zero npm dependencies; the proxy and tests run on the Node.js standard library (Node 18+ for global `fetch`).

## Install

1. Have an OpenViking server reachable (see the [quickstart](../docs/en/getting-started/02-quickstart.md)); default local endpoint is `http://127.0.0.1:1933`.
2. Point your Agent-Plugins-conforming client at this directory (each client has its own install command or plugin directory; consult its docs). The client will:
   - register the `openviking` MCP server from `mcp.json` — it runs `node <plugin>/servers/mcp-proxy.mjs` over stdio;
   - discover the `openviking-memory` skill from `skills/`.
3. Configure credentials (next section) and start a session. The model gains `find` / `search` / `recall` / `read` / `remember` / `write` and the other OpenViking MCP tools.

## Why a stdio proxy instead of a `streamable-http` entry

OpenViking already speaks streamable HTTP at `/mcp`, but a `streamable-http` entry in `mcp.json` cannot work portably: the server URL is per-deployment (localhost for one user, a remote endpoint for another), and the Agent Plugins spec forbids credentials in the static `headers` map. The stdio proxy solves both — it resolves the URL and API key at runtime from the same local sources as the `ov` CLI, injects them per request, and forwards JSON-RPC over streamable HTTP unchanged.

## Credential resolution

Highest to lowest priority (same chain as the `ov` CLI and the other OpenViking plugins):

1. Environment variables: `OPENVIKING_URL` (or `OPENVIKING_BASE_URL`), `OPENVIKING_API_KEY` (or `OPENVIKING_BEARER_TOKEN`), `OPENVIKING_ACCOUNT`, `OPENVIKING_USER`, `OPENVIKING_PEER_ID`
2. `~/.openviking/ovcli.conf` (`url`, `api_key`, `account`, `user`) — override the path with `OPENVIKING_CLI_CONFIG_FILE`
3. `~/.openviking/ov.conf` `server` section (`url` or `host`/`port`, `root_api_key`) — override the path with `OPENVIKING_CONFIG_FILE`
4. Defaults: `http://127.0.0.1:1933`, no auth (local mode)

Config file changes are picked up by the running proxy without a restart. Debugging: set `OPENVIKING_DEBUG=1` to log JSON lines to `~/.openviking/logs/agent-plugins.log` (path override: `OPENVIKING_DEBUG_LOG`).

## Scope: what this package does and doesn't do

This package is the portable recall + write surface: skills plus MCP tools, driven by the model. Agent Plugins 1.0 deliberately excludes hooks, commands, and agents, so **automatic conversation capture and automatic pre-prompt recall are out of scope here** — the `skills/openviking-memory` skill instead teaches the model to recall at task start and persist durable facts via `remember`/`write` itself.

**If your harness has a hook system, prefer the dedicated plugin** — hook-driven recall and capture cost no tool calls and don't depend on the model choosing to remember. One installer covers Claude Code, Codex, Cursor, TRAE / TRAE CN, ZCode, OpenCode, and pi; it prompts for language, harnesses, download source, and credentials, and is idempotent:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh)
# GitHub hard to reach? Same installer from the Volcengine TOS mirror:
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh)
```

- [claude-code-memory-plugin](../examples/claude-code-memory-plugin/) (Claude Code)
- [codex-memory-plugin](../examples/codex-memory-plugin/) (Codex)
- [opencode-plugin](../examples/opencode-plugin/) (OpenCode)
- [cursor-memory-plugin](../examples/cursor-memory-plugin/), [trae-memory-hooks](../examples/trae-memory-hooks/), [zcode-memory-plugin](../examples/zcode-memory-plugin/), ...

Use this Agent Plugins package for harnesses with no hooks, or when you want one package that loads across many clients.

Per the spec, client-specific integrations may later be embedded in this package under reverse-domain namespaced directories (e.g. `com.example.client/`) or the manifest's `extensions` object without breaking other clients.

## Development

```bash
node --test agent-plugins/plugin.test.mjs
```

`servers/shared/*.mjs` are generated, verbatim copies from `examples/memory-plugin-shared/lib` — do not edit them here. This directory is a target of the shared-lib sync script, so refresh them with:

```bash
node examples/memory-plugin-shared/sync.mjs
```

`examples/memory-plugin-shared/sync.test.mjs` fails if they drift. `servers/config.mjs`, `servers/debug-log.mjs`, and `servers/mcp-proxy.mjs` are adapted from `examples/claude-code-memory-plugin` (connection fields only — the hook-tuning knobs are dropped, since this spec has no hooks); when changing proxy behavior there, mirror it here.

Both test files run in CI via `.github/workflows/pr.yml`.
