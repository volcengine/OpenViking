## Install the Cursor integration

Requires macOS/Linux and Node.js 18+. The command installs Hooks, MCP, Rule, and Skill together:

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness cursor --dist tos
```

When asked where to connect, select **Volcengine OpenViking Cloud** and enter your API key. Choose **Self-hosted / local** only for a locally running OpenViking server.

## Verify

1. Restart Cursor and start a new Agent session.
2. In **Cursor Settings → Hooks**, confirm that the lifecycle Hooks ran `cursor-hook.mjs`, the URI protection Hooks ran `uri-guard.mjs`, and the prompt Hook returned `additional_context`.
3. In **Cursor Settings → Tools & MCPs**, confirm that `openviking` is connected.

See the complete [Cursor integration guide](https://github.com/volcengine/OpenViking/blob/main/docs/en/agent-integrations/12-cursor.md).

## Troubleshooting

| Problem | Suggested fix |
|---|---|
| Hooks do not run after installation | Quit Cursor completely, restart it, and create a new Agent session. |
| Recall runs more than once | Check the Execution Log for an imported legacy Claude OpenViking Hook, then upgrade or remove the legacy plugin reported by the installer. |
| Connection/authentication fails | Check `~/.openviking/ovcli.conf` and restart Cursor. |
