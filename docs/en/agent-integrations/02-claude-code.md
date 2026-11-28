# Claude Code

Give [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) cross-project and cross-session long-term memory. Once installed, every conversation automatically recalls relevant memories and captures new content without requiring the model to make any tool calls.

Source: [examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin) (Claude Code files under [hosts/claude-code](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin/hosts/claude-code)) | [Blog: motivation & demo](https://blog.openviking.ai/post/openviking-coding-agent/)

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

> **Plugin updates**: on Claude Code 2.1.224 or later, the installer registers a marketplace served by the OpenViking documentation site with auto-update turned on, so new plugin versions download in the background and load after a restart. Older Claude Code gets a local directory marketplace, which cannot auto-update — re-run the installer to update.

<details>
<summary><b>Manual setup</b></summary>

1. **Configure the connection** — write `~/.openviking/ovcli.conf` (`url`, `api_key`, optional `account`/`user`), or run the bundled wizard `node <plugin-dir>/hosts/claude-code/setup.mjs` after installing. For a local server without authentication (`http://127.0.0.1:1933`), skip this step.

2. **Install the plugin** from the remote marketplace:

   ```bash
   claude plugin marketplace add https://raw.githubusercontent.com/volcengine/OpenViking/main/.claude-plugin/marketplace.json
   claude plugin install openviking-memory@openviking
   ```

3. **Start Claude Code** and run `/mcp` to verify that the OpenViking entry is connected.

The installer skips Claude Code older than 2.0 and asks you to upgrade.

</details>

Once installed, start a new Claude Code session and ask it to follow https://docs.openviking.ai/agents/en/onboarding.md. It checks the connection and walks you through the first use; see [One-click setup](../getting-started/07-agent-onboarding.md).

## Verify

Launch `claude`, then:

- `/plugins` → **openviking-memory** is listed under "Installed", with the **openviking** MCP connected below it.
- `/mcp` → the OpenViking entry shows your server URL with valid authentication.
- `/openviking-memory:ov` → server, identity, last injection and recall, and toggles.

If the plugin does not seem to activate, set `OPENVIKING_DEBUG=1` and check `~/.openviking/logs/cc-hooks.log`.

## What is different on Claude Code

Claude Code has every event the plugin uses: session start, prompt, turn end, before compaction, session end, subagent stop and tool calls. Capture, commits, recall and the offline queue work as described in the [Capability Reference](./16-capability-reference.md).

- **Subagents:** each subagent is captured into its own session, `cc-<id>__subagent-<agent_id>`, when it stops. See [Subagents](./16-capability-reference.md#subagents).
- **Status command:** `/openviking-memory:ov` shows the plugin's status.
- **Statusline:** an OpenViking indicator beneath the input box shows connection, recall, capture and session state. See [STATUSLINE.md](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/hosts/claude-code/STATUSLINE.md).
- **Source cards** and **seal secrets**, below.

## Source cards

Under each answer, the plugin adds a one-line card listing the OpenViking sources that answer drew on: what auto-recall added to the prompt, and what Claude searched for and read on its own. For example: `OV · 8 sources · 4 past events · 3 work memories · 1 team doc · 1 read in full  [Expand]`. Expand a card with its button, or expand and collapse every card with `/openviking-usage expand` and `/openviking-usage collapse`. Cards are collapsed by default.

The cards need Claude Code 2.1.286 or newer with plugin hooks modules enabled. On older versions, or with modules off, no cards are shown, and recall, capture and the other hooks work as before.

## Seal secrets {#seal-secrets}

With `sealSecrets` on, the plugin replaces the secrets your OpenViking server knows with <code v-pre>{{ov_privacy:…}}</code> references in what Claude reads, and gives tools the real values back. It is off by default and needs OpenViking server 0.5.7 or newer. It runs on your machine.

| Setting | Environment variable | Default | Effect |
|---------|----------------------|---------|--------|
| `sealSecrets` | `OPENVIKING_SEAL_SECRETS` | `false` | Turns sealing on |
| `enrollToolSecrets` | `OPENVIKING_ENROLL_TOOL_SECRETS` | `false` | Also stores new secrets found in tool output |

Set them in `ovcli.conf` or in the environment; a changed setting takes effect in a new session. Sealing runs in the plugin's hooks module, so it needs what [Source cards](#source-cards) need; where the module is not loaded, nothing is sealed.

- **Sealed:** your prompt, tool results, and the files, reminders and hook context Claude Code attaches. New secrets are stored from your prompt, and from tool results only with `enrollToolSecrets`. A reference Claude writes in a tool call becomes the real value before the tool runs. The local transcript stores prompts and tool results with references; for attached files and reminders it keeps Claude Code's own record, with the original text.
- **When sealing cannot be done** (a server older than 0.5.7, or the server's version, seal or resolve call cannot be read), it stops: your prompt is not sent, a tool result is withheld, and a tool call whose references cannot be resolved does not run. The message says why. Upgrade the server or restore the connection, or turn `sealSecrets` off.
- **Not covered:** the system prompt, slash-command arguments, skill bodies, and history written before sealing was turned on.

## Workspace peer

Memories are filed under a peer derived from the repository you work in; see [Workspace peer](../configuration/02-client.md#workspace-peer). To give a directory that is not a repository its own memory, create `.openviking/config.json` in it containing `{"version": 1, "peer": {"id": "my-project"}}`.

## Troubleshooting

| Issue | Cause | Solution |
|---------|-------|-----|
| Plugin is not activating | Missing `ov.conf` or `ovcli.conf` | Run the [installer](#install), or set `OPENVIKING_MEMORY_ENABLED=1` along with the URL/API_KEY environment variables |
| MCP tools hit `127.0.0.1` instead of the remote server | `~/.openviking/ovcli.conf` has no `url` | Fix `ovcli.conf`, then restart Claude Code |
| Remote auth 401 / 403 | Invalid credentials or insufficient permission | Check the active key in `ovcli.conf`; the MCP proxy re-reads it after auth failures |

## See also

- [Capability Reference](./16-capability-reference.md) — capture, commits, recall, offline retries and settings shared by every host
- [Plugin README](https://github.com/volcengine/OpenViking/blob/main/examples/agent-hook-plugin/hosts/claude-code/README.md)
- [Plugin Settings](../configuration/02-client.md#plugin-settings)
- [MCP Clients](./06-mcp-clients.md)
