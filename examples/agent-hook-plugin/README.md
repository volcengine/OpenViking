# OpenViking Memory for hook hosts

This directory is the one OpenViking memory plugin for every host that runs hook scripts: Claude Code, Codex, Cursor, TRAE / TRAE CN, Antigravity, ZCode, Kimi Code, CodeBuddy Code and Qoder CLI. Every host runs the same flow from `lib/flow/`: recall on each prompt, profile and skill catalog at session start, capture at turn end, commit at a compaction and at session end, with an offline queue for writes the server did not take. A host contributes only a small module that describes its events, payloads and output. Shared behaviour and settings are documented in the [capability reference](../../docs/en/agent-integrations/16-capability-reference.md).

```bash
curl -fsSL https://openviking.ai/install | bash
```

The installer asks which harnesses to install and for your OpenViking credentials. To install from a repository checkout instead, run `bash examples/memory-plugin-shared/install.sh --harness <cursor|trae|trae-cn|zcode|kimicode|codebuddy|qoder|antigravity>` from its root.

## Layout

```text
agent-hook-plugin/
├── .claude-plugin/plugin.json   Claude Code manifest
├── hooks/hooks.json             Claude Code hooks
├── .mcp.json                    Claude Code MCP server
├── mods/  commands/             Claude Code mods and slash commands
├── .codex-plugin/plugin.json    Codex manifest (points at hosts/codex/)
├── lib/                         shared library
│   └── flow/                    the flow every host runs
├── scripts/
│   ├── hook.mjs                 hook entry: hook.mjs <event> <host>
│   ├── uri-guard.mjs            viking:// guard: uri-guard.mjs <host>
│   ├── statusline.mjs           Claude Code statusline
│   └── ov-memory-doctor.mjs     doctor
├── servers/mcp-proxy.mjs        MCP proxy
├── skills/                      the skills
└── hosts/<host>/                host.mjs, decode.mjs, registration files, fixtures/
```

## Hosts

| Host | Docs |
|------|------|
| Claude Code | [02-claude-code.md](../../docs/en/agent-integrations/02-claude-code.md) |
| Codex | [04-codex.md](../../docs/en/agent-integrations/04-codex.md) |
| Cursor | [12-cursor.md](../../docs/en/agent-integrations/12-cursor.md) |
| TRAE / TRAE CN | [13-trae.md](../../docs/en/agent-integrations/13-trae.md) |
| CodeBuddy Code | [20-codebuddy.md](../../docs/en/agent-integrations/20-codebuddy.md) |
| Qoder CLI | [21-qoder.md](../../docs/en/agent-integrations/21-qoder.md) |
| ZCode, Kimi Code, Antigravity | [08-community-plugins.md](../../docs/en/agent-integrations/08-community-plugins.md) |

Host events per host are listed in the capability reference; how to add a host is in [18-plugin-development.md](../../docs/en/agent-integrations/18-plugin-development.md).

## Diagnostics

```bash
node ~/.openviking/agent-integrations/<client>/scripts/ov-memory-doctor.mjs --offline
```

`<client>` is `cursor`, `trae`, `trae-cn`, `zcode` or `antigravity`. Kimi Code, CodeBuddy Code and Qoder CLI run the copy inside their installed plugin:

```bash
node "${KIMI_CODE_HOME:-$HOME/.kimi-code}/plugins/managed/openviking-memory/agent-integrations/kimicode/scripts/ov-memory-doctor.mjs" --offline
node ~/.openviking/agent-integrations/codebuddy/marketplace/openviking-memory/agent-integrations/codebuddy/scripts/ov-memory-doctor.mjs --offline
node ~/.openviking/agent-integrations/qoder/marketplace/openviking-memory/agent-integrations/qoder/scripts/ov-memory-doctor.mjs --offline
```

Drop `--offline` to probe the server as well, and add `--json` for a machine-readable report. In a session, the `ov-memory-doctor` skill runs the doctor for the host it runs in. For Claude Code and Codex, `scripts/ov-memory-doctor.mjs` in the installed plugin hands over to `hosts/claude-code/ov-memory-doctor.mjs` or `hosts/codex/ov-memory-doctor.mjs`, taking the host from its argument (`claude-code`, `codex`) or the environment; see their docs pages.

## Tests

```bash
node --test examples/memory-plugin-shared/flow.test.mjs examples/memory-plugin-shared/hosts.test.mjs examples/memory-plugin-shared/hook-entry.test.mjs
```
