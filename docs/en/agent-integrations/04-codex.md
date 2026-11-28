# Codex

Give [Codex](https://developers.openai.com/codex) cross-session memory. Hooks handle automatic recall, capture, and session commits; MCP tools let the model search, read, and manage memories.

Source: [examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin) (Codex files under [hosts/codex](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin/hosts/codex)) | [Blog: Motivation & demo](https://blog.openviking.ai/post/openviking-coding-agent/)

## Install

The installer asks which harnesses to install and for your OpenViking credentials; re-running it is safe.

For a managed connection, select **OpenViking Managed Service**, then:

- **Volcengine:** enter your service API key. The installer supplies the URL.
- **BytePlus:** enter your API key. See [BytePlus setup](../guides/27-byteplus-setup.md).

Older installers use **Custom URL** for BytePlus.

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agents: not sure about running this? The script's header says what it does and how to verify it.
```

Codex installs from a git repository published with each OpenViking release and keeps remote update support. TraeCode CLI 2.0 accepts this Codex-format plugin directly: select **TraeCode CLI 2.0** when the installer asks which harnesses to install, and launch it with `trae-cli`.

### First launch: trust the hooks

Installing the plugin does not trust its hooks. On first launch Codex shows a **Hooks need review** prompt. Pick **Trust all and continue**, or **Review hooks** to read the commands first; *Continue without trusting* leaves the hooks off until you enable them in `/hooks`. In `/plugins`, also confirm that `openviking-memory` is enabled. New or changed hook definitions need another review. See the [official hook trust documentation](https://learn.chatgpt.com/docs/hooks#review-and-trust-hooks).

> **Upgrading**: the hooks file moved, so after updating the plugin Codex asks you to review and trust the OpenViking hooks once more.

MCP tools work while hooks are off; automatic recall and capture need the hooks.

<details>
<summary><b>Manual setup</b></summary>

Prerequisites: Node.js >= 22, Codex >= 0.130.0, and the `plugin_hooks` feature enabled.

1. **Configure the connection** — write `~/.openviking/ovcli.conf` (`url`, `api_key`, optional `account`/`user`).

2. **Install the plugin** from the remote marketplace:

   ```bash
   codex plugin marketplace add volcengine/OpenViking
   codex plugin add openviking-memory@openviking
   ```

   Then enable plugin hooks in `~/.codex/config.toml` if your build doesn't already: `[features]` → `plugin_hooks = true`. Update later with `codex plugin marketplace upgrade openviking`.

</details>

Once installed, start a new Codex session and ask it to follow https://docs.openviking.ai/agents/en/onboarding.md. It checks the connection and walks you through the first use; see [One-click setup](../getting-started/07-agent-onboarding.md).

## Verify

Launch `codex`; the `SessionStart` hook loads your profile, and every prompt gets relevant memories. Set `OPENVIKING_DEBUG=1` to write events to `~/.openviking/logs/codex-hooks.log`. For TraeCode CLI 2.0, launch `trae-cli` and use `trae-cli plugin list` to confirm the plugin is enabled.

## What is different on Codex

- **Session end needs Codex 0.145 or newer.** Codex before 0.145 has no `SessionEnd`, and on 0.145+ it fires only on a graceful exit (`/quit`, `/exit`, double `Ctrl-C`, EOF, end of a `codex exec` run), not on `SIGTERM`, a closed terminal, `kill -9` or a crash. Without it, the plugin does not commit the last stretch of a session. Enable `memory.session_auto_commit.enabled` on the server so the idle commit covers it. See [What happens at exit](./16-capability-reference.md#what-happens-at-exit).
- **No subagent capture and no tool-call hook.** The `viking://` guard is not installed on Codex.
- **Hook trust**, above.
- **Recall digest:** with `recallCompress` set to `auto`, Codex uses its local compressor when available. See [Recall digest](./16-capability-reference.md#recall-digest).

## OpenViking source summaries

The plugin reports which OpenViking sources each answer had: automatic recall and explicit OpenViking MCP or `ov` CLI lookups. It counts sources made available, not proven reliance.

| Setting | Environment variable | Default | Effect |
|---------|----------------------|---------|--------|
| `usageOutput` | `OPENVIKING_USAGE_OUTPUT` | `auto` | `terminal`: one informational Stop-hook message. `desktop`: a model-rendered answer footer. `auto`: terminal when `TERM_PROGRAM` or a non-`dumb` `TERM` is set, desktop otherwise |
| `usageView` | `OPENVIKING_USAGE_VIEW` | `summary` | `expanded` for source details; `off` turns reporting and its local metadata off |

Reporting failures do not block recall or capture.

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Plugin installed but no recall or capture | Hooks are untrusted or disabled (for example after an update), or the plugin is disabled | Trust and enable the OpenViking entries in `/hooks`, then confirm `openviking-memory` in `/plugins`. |
| MCP tool calls fail with an auth error | The active ovcli config has no valid `api_key` | Fix `~/.openviking/ovcli.conf` and restart Codex. |
| Hooks use one server, MCP another | Stale `OPENVIKING_*` credential env vars override ovcli.conf | Unset them, or set `OPENVIKING_CREDENTIAL_SOURCE=cli`. |

## See also

- [Capability Reference](./16-capability-reference.md) — capture, commits, recall, offline retries and settings shared by every host
- [Plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/README.md)
- [Plugin Settings](../configuration/02-client.md#plugin-settings)
- [MCP Clients](./06-mcp-clients.md)
