# Community Plugins

Community-maintained integrations for various agent runtimes. Each differs in target platform, integration depth, and maintenance status — check the linked README before adopting.

## ZCode memory integration

Source: [examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin)

The ZCode community integration adds cross-project, cross-session memory through config-driven lifecycle hooks and an OpenViking MCP server:

- **SessionStart** injects the user profile.
- **UserPromptSubmit** recalls relevant memories.
- **PreToolUse** redirects direct `viking://` reads to MCP tools.
- **Stop** captures unseen rollout turns in a detached worker, then commits the OpenViking session.

ZCode does not expose `PreCompact`, `SessionEnd`, or subagent lifecycle hooks. The adapter therefore commits on `Stop`, uses ZCode rollout files as the authoritative incremental transcript, and falls back to hook stdin only when no rollout file is available.

### Install

Prerequisites: Node.js 18+, a running OpenViking server, and ZCode.

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agents: not sure about running this? The script's header says what it does and how to verify it.
```

The installer detects `~/.zcode/` or the `zcode` binary, installs the runtime under `~/.openviking/agent-integrations/zcode/`, and merges hooks and MCP configuration into `~/.zcode/cli/config.json`.

After restarting ZCode, verify that:

- `~/.zcode/cli/config.json` contains `hooks.enabled: true`, OpenViking entries under `hooks.events`, and `mcp.servers.openviking`.
- `OPENVIKING_DEBUG=1` produces diagnostics in `~/.openviking/logs/zcode-hooks.log`.

| Symptom | Cause | Fix |
|---------|-------|-----|
| Hooks not firing | Hook configuration is disabled or stale | Re-run the installer and restart ZCode |
| Recall returns nothing | OpenViking is unavailable or has not extracted memories yet | Check `curl http://127.0.0.1:1933/health` and wait for extraction |
| MCP tools not appearing | The MCP proxy failed to start | Check the absolute `mcp.servers.openviking` command in `~/.zcode/cli/config.json` |
| Duplicate captures | An older installation left duplicate hook entries | Run `curl -fsSL https://openviking.ai/install \| bash -s -- --uninstall --yes --harness zcode`, then reinstall |

Implementation details and currently verified ZCode assumptions are documented in the plugin's [README](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin) and [DESIGN.md](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/DESIGN.md).

## Kimi Code memory integration

Source: [examples/kimi-code-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/kimi-code-memory-plugin) | Docs: [README](https://github.com/volcengine/OpenViking/blob/main/examples/kimi-code-memory-plugin/README.md), [DESIGN.md](https://github.com/volcengine/OpenViking/blob/main/examples/kimi-code-memory-plugin/DESIGN.md)

The Kimi Code integration is a community implementation shipped as a native managed plugin, not an official plugin of
Moonshot AI or Kimi Code. It reuses the shared OpenViking hook runtime and adds only Kimi-specific event mapping, wire
transcript decoding, output formatting, and commit policy:

- **UserPromptSubmit** recalls memory and prints raw context text for Kimi to inject; the profile and skill list ride the same event on the first prompt, because Kimi discards `SessionStart` stdout.
- **PreToolUse** blocks direct Read/Glob/Grep access to `viking://` URIs and points the model at the MCP tools.
- **Stop**, **PreCompact**, and **SessionEnd** capture new `wire.jsonl` turns; **Interrupt** runs the same capture synchronously within one two-second OpenViking request budget.
- **SessionStart** replays pending messages that failed to send earlier.
- The plugin manifest exposes the OpenViking MCP server without editing Kimi's legacy config files.

Known gap: subagent transcripts (`agents/<subagent>/wire.jsonl`) are not captured yet.

### Install (native plugin)

Prerequisites: Kimi Code CLI ≥ 2.1.1 (verified on 2.2.0), Node.js 18+, and a running OpenViking server. In the Kimi TUI:

```text
/plugins install <absolute path to examples/kimi-code-memory-plugin in this repo | zip URL>
```

The plugin sits in a monorepo subdirectory, and Kimi's GitHub URL install only accepts repositories whose manifest is at
the repository root, so use a local directory or a zip with the manifest at its root. `/reload` or a new session
activates it; the same command upgrades, and `/plugins remove openviking-memory` uninstalls. Settings and the full
switch list are in the plugin README.

### Verify and troubleshoot

`node scripts/ov-memory-doctor.mjs --offline` checks the install record, configuration and connection, and recent
activity; with `OPENVIKING_DEBUG=1`, event logs go to `~/.openviking/logs/kimicode-hooks.log`.

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Installed but no recall or capture | Plugin disabled, or the session was not reloaded | Confirm `openviking-memory` is enabled in `/plugins`, then `/reload` or start a new session |
| Source edits have no effect | The CLI always runs the managed copy, not your source directory | Reinstall with `/plugins install <same source>` and verify again |
| MCP tool calls fail authentication | Credentials missing or expired | Fix `~/.openviking/ovcli.conf` or the `OPENVIKING_*` credential env vars, then restart the session |
| MCP tool calls fail to connect | Server unreachable or the URL is wrong | `curl "$(jq -r '.url' ~/.openviking/ovcli.conf)/health"` |
| Not sure what is wrong | — | Run the doctor above; set `OPENVIKING_DEBUG=1` and read the log |

### Install (legacy / fallback)

New users should pick the native plugin above instead. These instructions stay for existing users of the curl installer.

Prerequisites: Node.js 18+, a running OpenViking server, and Kimi Code CLI.

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agents: not sure about running this? The script's header says what it does and how to verify it.
```

The installer assembles a self-contained runtime under
`$KIMI_CODE_HOME/plugins/managed/openviking-memory/` (default Kimi home:
`~/.kimi-code/`), then updates only the `openviking-memory` entry in
`plugins/installed.json`. Existing plugin records are preserved. Re-run the
same command to upgrade. To remove only this plugin:

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --uninstall --yes --harness kimicode
```

The verified host contract and version are recorded in
[`hosts/kimicode/DESIGN.md`](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/hosts/kimicode/DESIGN.md).

## AstrBot plugin

[AstrBot](https://github.com/AstrBotDevs/AstrBot) is a multi-platform IM bot framework supporting QQ, Telegram, Discord, Lark, and 20+ other platforms.

Source: [astrbot_plugin_openviking_memory](https://github.com/t0saki/astrbot_plugin_openviking_memory)

Provides auto-capture of group/DM conversations, semantic recall before each LLM request, and configurable venue memory isolation.

**Install**: In AstrBot WebUI, search **OpenViking Memory** in the Plugin Marketplace; or install from URL: `https://github.com/t0saki/astrbot_plugin_openviking_memory.git`

The current plugin requires AstrBot 4.23.1+ and a compatible OpenViking server. Use a User key for `global`; `venue` needs an Admin key to create users. Check the linked plugin README before migrating an older configuration.

**Key features**:

- Auto-recall and auto-capture via hooks — the model doesn't need to invoke tools
- `self_scope=global` shares one bot identity; `venue` creates a separate user per group/DM. People are represented as peers. Legacy `isolation_mode` values are mapped to these scopes.
- Automatic commit by message count, token threshold, or idle timeout
- Backfills platform message history on first venue encounter

## Open WebUI tool server

[Open WebUI](https://github.com/open-webui/open-webui) is a self-hosted AI chat interface.

Source: [examples/openwebui-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/openwebui-plugin)

A standalone FastAPI server that exposes a curated subset of OpenViking endpoints as OpenAPI tools, so Open WebUI can call them as native tools. Setup and endpoint details are in the README.

## More examples

The [examples/](https://github.com/volcengine/OpenViking/tree/main/examples) directory also contains deployment and integration samples beyond agent plugins — Grafana dashboards, Kubernetes Helm charts, multi-tenant setups, snapshot workflows, and SDK snippets.

## See also

- [Capability Reference](./16-capability-reference.md)
