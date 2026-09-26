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
│   └── shared/                          # generated from examples/memory-plugin-shared/lib
├── skills/openviking-memory/SKILL.md    # teaches the model the recall + persist loop
├── skills/ov-experience-memory/SKILL.md # retrieve and apply prior task Experience
├── skills/ov-memory-troubleshoot/SKILL.md # trace memory issues to session evidence
├── skills/openviking-skills/SKILL.md    # find, use, create, and share OpenViking skills
└── plugin.test.mjs                      # node --test conformance checks
```

Zero npm dependencies — the proxy and the tests run on the Node.js standard library (Node 18+ for global `fetch`).

## Install

1. Have an OpenViking server reachable. If you don't, follow the [Quickstart](../getting-started/02-quickstart.md); the default local endpoint is `http://127.0.0.1:1933`.
2. Point your Agent-Plugins-conforming client at the `agent-plugins/` directory. Each client has its own install command or plugin directory — consult its docs. On load the client will:
   - register the `openviking` MCP server from `mcp.json`, running `node <plugin>/servers/mcp-proxy.mjs` over stdio;
   - discover the `openviking-memory`, `ov-experience-memory`, `ov-memory-troubleshoot`, and `openviking-skills` skills from `skills/`.
3. Configure credentials (below) and start a session. The model gains `find` / `search` / `read` / `list` / `grep` / `glob` / `remember` / `add_resource` / `forget` / `health`, plus `tree` / `write` / `edit` / `add_skill` on recent servers.

## Why a stdio proxy instead of a `streamable-http` entry

OpenViking already speaks streamable HTTP at `/mcp`, but a `streamable-http` entry in `mcp.json` cannot work portably: the server URL is per-deployment (localhost for one user, a remote endpoint for another), and the spec forbids credentials in the static `headers` map. The stdio proxy resolves both at runtime — it reads the URL and API key from the same local sources as the `ov` CLI, injects them per request, and forwards JSON-RPC over streamable HTTP unchanged.

## Credential resolution

This package resolves credentials from the following sources, highest to lowest priority. It shares credential files with the CLI; plugin-specific fallbacks are listed explicitly:

1. Environment variables: `OPENVIKING_URL` (or `OPENVIKING_BASE_URL`), `OPENVIKING_MCP_URL`, `OPENVIKING_API_KEY` (or `OPENVIKING_BEARER_TOKEN`), `OPENVIKING_ACCOUNT`, `OPENVIKING_USER`, `OPENVIKING_PEER_ID`, `OPENVIKING_AUTH_MODE`
2. `~/.openviking/ovcli.conf` (`url`, `api_key`, `account` / `account_id`, `user` / `user_id`, `actor_peer_id` / `peer_id`), then its `plugin.agent_plugins` and shared `plugin` keys (`apiKey`, `accountId`, `userId`, `authMode`) — override the path with `OPENVIKING_CLI_CONFIG_FILE`
3. `~/.openviking/ov.conf`, `agent_plugins` section (`apiKey`, `accountId`, `userId`, `peerId`, `authMode`) — override the path with `OPENVIKING_CONFIG_FILE`
4. `~/.openviking/ov.conf`, `server` section (`url`, or `host` / `port`, and `root_api_key`)
5. Defaults: `http://127.0.0.1:1933`, no auth (local mode)

`OPENVIKING_MCP_URL` replaces the derived `<url>/mcp` endpoint, not the base URL.

`OPENVIKING_CREDENTIAL_SOURCE` (or `OPENVIKING_CREDENTIALS_SOURCE`) pins the chain to one end: `env` for the environment, `cli` (also `ovcli` / `file` / `config`) for ovcli.conf. The default `auto` pins it to ovcli.conf when that file carries credentials and none of the variables above is set, and runs the whole chain otherwise. While the chain is pinned to ovcli.conf, the environment's credentials and `OPENVIKING_MCP_URL` are skipped. A key still falls through the `plugin` keys, the `agent_plugins` section and finally `server.root_api_key`, so an install that names only a `url` there keeps the key it has always used; the account and user stop at the `plugin` keys. `env` reads neither file.

`~/.openviking/ovcli.conf`:

```json
{
  "url": "https://openviking.example.com",
  "api_key": "your-api-key"
}
```

Use a User/Admin key for `/mcp`. The legacy `server.root_api_key` fallback does not make a root key valid for MCP access.

Config file changes are picked up by the running proxy without a restart.

Debugging: set `OPENVIKING_DEBUG=1` to write JSON lines to `~/.openviking/logs/agent-plugins.log` (override the path with `OPENVIKING_DEBUG_LOG`). Set `OPENVIKING_TIMEOUT_MS` to change the 15s per-request timeout.

## Scope: hooks are intentionally absent

Agent Plugins 1.0 covers skills and MCP servers only — hooks, commands, and agents are deliberately outside the version, because their semantics differ too much between clients. So this package is the **portable recall + write surface**, driven by the model rather than by lifecycle events: automatic conversation capture and automatic pre-prompt recall are out of scope here.

The bundled `openviking-memory` skill compensates by teaching the model the full loop itself — recall at task start with `find` / `search` + `read` (using `search` with `mode="context"` when assembled context is useful), then persist durable facts with `remember` / `write` / `edit`, with priority and safety rules for using retrieved memory.

The bundled `ov-experience-memory` skill has the model search `viking://~/memories/experiences` before executable work and read the Experience files that apply. Here it is retrieval-only: with no session capture, its reads are not linked back to the Experience they used and produce no new trajectories. The Experience it finds comes from harnesses that do capture sessions.

The bundled `openviking-skills` skill covers the skills stored in OpenViking itself: finding one with `find(context_type="skill")`, reading and following its `SKILL.md`, creating or replacing one with `add_skill`, installing one from Git or a local folder, sharing one with the account, and moving local skill folders into OpenViking. Without a session-start hook there is no `<available-skills>` catalog here, so the skill has the model search for a skill rather than read it off a list.

**If your harness has its own hook system, prefer the dedicated plugin.** Hook-driven recall and capture happen without the model spending tool calls or deciding to remember, so they use fewer tokens than the skill-driven loop and do not depend on the model's judgment. Use this Agent Plugins package for harnesses that have no hooks, or when you want one package that works across many clients.

One installer covers Claude Code, Codex, Cursor, TRAE / TRAE CN, ZCode, OpenCode, and pi. It asks for your language, which harnesses to install, the download source, and your OpenViking credentials, and the installation steps support repeated runs:

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
| pi | [pi Coding Agent Extension](./11-pi.md) — uses the official MCP client and registers the server's tools natively |
| OpenClaw | [OpenClaw Plugin](./03-openclaw.md) — separate install flow |
| ZCode | [Community Integrations](./08-community-plugins.md) |

Per the spec, client-specific integrations can later be embedded in this same package under reverse-domain namespaced directories (e.g. `com.example.client/`) or the manifest's `extensions` field, without breaking other clients.

## Development

```bash
node --test agent-plugins/plugin.test.mjs
```

`plugin.test.mjs` checks manifest schema URLs and matching spec versions, the plugin name rules, the closed manifest root, semver, that every `skills/*` child ships a `SKILL.md` with `name` + `description` frontmatter matching its directory, that `mcp.json` entries reference files that exist and stay inside the plugin root, and that `node --check` passes on every `.mjs` in the package.

`servers/shared/*.mjs` are generated copies of `examples/memory-plugin-shared/lib` — edit the shared lib and re-run `node examples/memory-plugin-shared/sync.mjs`; `examples/memory-plugin-shared/sync.test.mjs` fails if they drift. Both test files run in CI.

## See also

- [Capability Reference](./16-capability-reference.md)
