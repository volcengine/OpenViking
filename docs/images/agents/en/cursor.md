## Install the Cursor integration

Run the following command:

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness cursor --dist tos
```

## Verify

1. Restart Cursor and start a new Agent session.
2. In **Cursor Settings → Hooks**, confirm that `cursor-hook.mjs` ran.
3. In **Cursor Settings → Tools & MCPs**, confirm that `openviking` is connected.

See the complete [Cursor integration guide](https://github.com/volcengine/OpenViking/blob/main/docs/en/agent-integrations/12-cursor.md).

## Troubleshooting

| Problem | Suggested fix |
|---|---|
| Hooks do not run after installation | Quit Cursor completely, restart it, and create a new Agent session. |
| The Plugins page shows `Get` | No action is required. Use the Hooks and Tools & MCPs checks above. |
| Recall runs more than once | Re-run the install command and restart Cursor. |
| Connection/authentication fails | Check `~/.openviking/ovcli.conf` and restart Cursor. |
| Hook cannot find Node.js | Ensure `node` is available in Cursor's process `PATH`. |
