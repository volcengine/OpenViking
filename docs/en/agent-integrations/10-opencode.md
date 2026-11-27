# OpenCode

Give [OpenCode](https://opencode.ai/) cross-project and cross-session long-term memory. The plugin runs inside OpenCode: it recalls relevant memories for each prompt, captures each turn into an OpenViking session, and gives the model the OpenViking MCP tools.

Source: [examples/opencode-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-plugin)

## Install

Requirements: [OpenCode](https://opencode.ai/) 2.0.15+, Node.js 18+, and an OpenViking server (managed or self-hosted). For a local server, verify with `curl http://localhost:1933/health`. You also need an OpenViking API key when the server requires authentication.

> **Upgrading from OpenCode 1.x:** the plugin supports OpenCode 2 only and does nothing on OpenCode 1.x. Upgrade OpenCode, or pin `@openviking/opencode-plugin@2026.11.26`, the last release that supports OpenCode 1.x.

### One-line installer (recommended)

For a managed connection, select **OpenViking Managed Service**, then:

- **Volcengine:** enter your service API key. The installer supplies the URL.
- **BytePlus:** enter your API key. See [BytePlus setup](../guides/27-byteplus-setup.md).

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agents: not sure about running this? The script's header says what it does and how to verify it.
```

The installer copies the plugin into `~/.config/opencode/plugins/` as a local file plugin, writes the `openviking` MCP server entry into `~/.config/opencode/opencode.json`, and configures `~/.openviking/ovcli.conf`.

### Manual npm install

The published npm package is `@openviking/opencode-plugin`. For a first-time OpenCode config:

```bash
mkdir -p ~/.config/opencode
cat > ~/.config/opencode/opencode.json <<'JSON'
{
  "$schema": "https://opencode.ai/config.json",
  "plugin": ["@openviking/opencode-plugin"]
}
JSON
opencode
```

If `~/.config/opencode/opencode.json` already exists, do not overwrite it; only merge `"@openviking/opencode-plugin"` into the existing `plugin` array. OpenCode downloads the npm package at startup, and the plugin registers its MCP server automatically.

### Source install

```bash
git clone https://github.com/volcengine/OpenViking.git
cd OpenViking
node examples/memory-plugin-shared/sync.mjs
mkdir -p ~/.config/opencode/plugins/openviking
cp examples/opencode-plugin/wrappers/openviking.js ~/.config/opencode/plugins/openviking.js
cp examples/opencode-plugin/index.mjs examples/opencode-plugin/package.json ~/.config/opencode/plugins/openviking/
cp -r examples/opencode-plugin/lib ~/.config/opencode/plugins/openviking/
cp -r examples/opencode-plugin/servers ~/.config/opencode/plugins/openviking/
```

`sync.mjs` generates `lib/shared/`, which is not in git; run it before copying and again after every `git pull`.

Credentials are read from `OPENVIKING_*` environment variables or `~/.openviking/ovcli.conf`; the wizard `node examples/opencode-plugin/scripts/setup.mjs` writes the file. Settings under `plugin.opencode` in `ovcli.conf` override the shared `plugin` keys.

## Verify

Restart OpenCode and start a session. The `openviking` MCP server should be connected and its tools listed as `openviking_*`. Ask about something from an earlier session to confirm recall. The plugin log is `~/.config/opencode/openviking/openviking-memory.log`.

If something is wrong, run `node ~/.config/opencode/plugins/openviking/scripts/ov-memory-doctor.mjs`, or ask the agent to use the `ov-memory-doctor` skill.

## What is different on OpenCode

- **OpenCode 2 only**; OpenCode 1.x users pin `@openviking/opencode-plugin@2026.11.26`.
- **Profile at the first prompt:** the session-start block (profile and skill catalog) is delivered with the first prompt, not at session creation.
- **Tool names:** OpenCode prefixes MCP tools with the server name, so the tools are `openviking_find`, `openviking_read`, `openviking_search`, and so on. The plugin sets the MCP server to `codemode: false`, so the tools stay directly callable instead of being folded into Code Mode.
- **Subagents:** a subagent session is captured into its own OpenViking session `oc-<parent>__subagent-<child>`; `skipSubagentSessions` skips them.
- **Events:** capture at `execution.ended`, commit before compaction and at `session.deleted`. There is no transcript file; the plugin reads the session's messages from OpenCode.
- **Skills:** the plugin registers its bundled skills through OpenCode's `skill.transform`; they are left out when the OpenViking MCP server is disabled.

Recall, capture, commits, offline retries and the rest are shared with the other integrations: see the [Capability Reference](./16-capability-reference.md).

## See also

- [Capability Reference](./16-capability-reference.md)
- [Plugin README](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-plugin)
