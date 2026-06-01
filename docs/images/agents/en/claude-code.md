# Claude Code Memory Plugin

Add cross-project, cross-session long-term memory to [Claude Code](https://docs.claude.com/en/docs/claude-code/overview). Install once; recall and capture happen automatically on every conversation.

## Install

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/claude-code-memory-plugin/setup-helper/install.sh)
```

The installer checks dependencies, configures the OpenViking connection, and installs the plugin.

## Verify

```bash
type claude
```

Expected output: `claude is a shell function`.

In Claude Code:

- `/plugins`: find `openviking-memory` in Installed
- `/mcp`: confirm the OpenViking MCP endpoint is connected
- `/openviking-memory:ov`: view server status, identity, recall, and capture state

Set `OPENVIKING_DEBUG=1` to write debug logs to `~/.openviking/logs/cc-hooks.log`.
