# Agent Plugins 1.0 Package

[Agent Plugins 1.0](https://agent-plugins.org/specification) is a vendor-neutral packaging format for extending AI coding agents. A plugin is a plain directory with a `plugin.json` manifest, Agent Skills auto-discovered under `skills/`, and optional MCP server declarations in `mcp.json` — one package that every conforming client loads the same way, instead of one bespoke integration per client.

OpenViking ships that package at [`agent-plugins/`](https://github.com/volcengine/OpenViking/tree/main/agent-plugins) in the repository.

## What's inside

```
agent-plugins/
├── plugin.json                          # Agent Plugins 1.0 manifest (name: openviking)
├── mcp.json                             # one stdio MCP server: "openviking"
├── servers/
│   ├── mcp-proxy.mjs                    # stdio -> streamable-HTTP proxy to the server's /mcp
│   ├── config.mjs, debug-log.mjs        # credential / config resolution
│   └── shared/                          # generated from examples/memory-plugin-shared/lib
├── skills/openviking-memory/SKILL.md    # teaches the model the recall + persist loop
└── plugin.test.mjs                      # node --test conformance checks
```

Zero npm dependencies — the proxy and the tests run on the Node.js standard library (Node 18+ for global `fetch`).

## Install

1. Have an OpenViking server reachable. If you don't, follow the [Quickstart](../getting-started/02-quickstart.md); the default local endpoint is `http://127.0.0.1:1933`.
2. Point your Agent-Plugins-conforming client at the `agent-plugins/` directory. Each client has its own install command or plugin directory — consult its docs. On load the client will:
   - register the `openviking` MCP server from `mcp.json`, running `node <plugin>/servers/mcp-proxy.mjs` over stdio;
   - discover the `openviking-memory` skill from `skills/`.
3. Configure credentials (below) and start a session. The model gains `find` / `search` / `recall` / `read` / `list` / `grep` / `glob` / `remember` / `add_resource` / `forget` / `health`, plus `tree` / `write` / `edit` on recent servers.

## Why a stdio proxy instead of a `streamable-http` entry

OpenViking already speaks streamable HTTP at `/mcp`, but a `streamable-http` entry in `mcp.json` cannot work portably: the server URL is per-deployment (localhost for one user, a remote endpoint for another), and the spec forbids credentials in the static `headers` map. The stdio proxy resolves both at runtime — it reads the URL and API key from the same local sources as the `ov` CLI, injects them per request, and forwards JSON-RPC over streamable HTTP unchanged.

## Credential resolution

Highest to lowest priority — the same chain as the `ov` CLI and the other OpenViking plugins:

1. Environment variables: `OPENVIKING_URL` (or `OPENVIKING_BASE_URL`), `OPENVIKING_API_KEY` (or `OPENVIKING_BEARER_TOKEN`), `OPENVIKING_ACCOUNT`, `OPENVIKING_USER`, `OPENVIKING_PEER_ID`
2. `~/.openviking/ovcli.conf` (`url`, `api_key`, `account`, `user`) — override the path with `OPENVIKING_CLI_CONFIG_FILE`
3. `~/.openviking/ov.conf`, `server` section (`url`, or `host` / `port`, and `root_api_key`) — override the path with `OPENVIKING_CONFIG_FILE`
4. Defaults: `http://127.0.0.1:1933`, no auth (local mode)

```json
// ~/.openviking/ovcli.conf
{
  "url": "https://openviking.example.com",
  "api_key": "your-api-key"
}
```

Config file changes are picked up by the running proxy without a restart.

Debugging: set `OPENVIKING_DEBUG=1` to write JSON lines to `~/.openviking/logs/agent-plugins.log` (override the path with `OPENVIKING_DEBUG_LOG`). Set `OPENVIKING_TIMEOUT_MS` to change the 15s per-request timeout.

## Scope: hooks are intentionally absent

Agent Plugins 1.0 covers skills and MCP servers only — hooks, commands, and agents are deliberately outside the version, because their semantics differ too much between clients. So this package is the **portable recall + write surface**, driven by the model rather than by lifecycle events: automatic conversation capture and automatic pre-prompt recall are out of scope here.

The bundled `openviking-memory` skill compensates by teaching the model the full loop itself — recall at task start with `find` / `search` / `recall` + `read`, then persist durable facts with `remember` / `write` / `edit`, with priority and safety rules for using retrieved memory.

**If your harness has its own hook system, prefer the dedicated plugin.** Hook-driven recall and capture happen without the model spending tool calls or deciding to remember, which is both cheaper and more reliable than the skill-driven loop. Use this Agent Plugins package for harnesses that have no hooks, or when you want one package that works across many clients.

One installer covers Claude Code, Codex, Cursor, TRAE / TRAE CN, ZCode, OpenCode, and pi. It asks for your language, which harnesses to install, the download source, and your OpenViking credentials, and every step is idempotent:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh)
```

In regions where GitHub is hard to reach, run the same installer from the Volcengine TOS mirror:

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh)
```

| Harness | Dedicated integration |
|---------|-----------------------|
| Claude Code | [Claude Code Memory Plugin](./02-claude-code.md) |
| Codex | [Codex Memory Plugin](./04-codex.md) |
| OpenCode | [OpenCode Plugin](./10-opencode.md) |
| Cursor | [Cursor Memory Integration](./12-cursor.md) |
| TRAE / TRAE CN | [TRAE Memory Integration](./13-trae.md) |
| pi | [pi Coding Agent Extension](./11-pi.md) |
| OpenClaw | [OpenClaw Plugin](./03-openclaw.md) — separate install flow |
| ZCode | [Community Integrations](./08-community-plugins.md) |

Per the spec, client-specific integrations can later be embedded in this same package under reverse-domain namespaced directories (e.g. `com.example.client/`) or the manifest's `extensions` field, without breaking other clients.

## Development

```bash
node --test agent-plugins/plugin.test.mjs
```

`plugin.test.mjs` checks manifest schema URLs and matching spec versions, the plugin name rules, the closed manifest root, semver, that every `skills/*` child ships a `SKILL.md` with `name` + `description` frontmatter matching its directory, that `mcp.json` entries reference files that exist and stay inside the plugin root, and that `node --check` passes on every `.mjs` in the package.

`servers/shared/*.mjs` are generated copies of `examples/memory-plugin-shared/lib` — edit the shared lib and re-run `node examples/memory-plugin-shared/sync.mjs`; `examples/memory-plugin-shared/sync.test.mjs` fails if they drift. Both test files run in CI.
