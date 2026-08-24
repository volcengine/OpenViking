## Step 1: Install

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness cursor --dist tos
```

Select **Volcengine OpenViking Cloud**. Paste the API key:

{{OPENVIKING_API_KEY_BLOCK}}

## Step 2: Verify

1. Restart Cursor and start a new Agent session.
2. **Cursor Settings → Hooks**: lifecycle hooks run `cursor-hook.mjs`.
3. **Cursor Settings → Tools & MCPs**: `openviking` is connected.

## Troubleshoot

| Problem | Fix |
|---|---|
| Hooks do not run | Quit Cursor completely, restart, new Agent session |
| Connection / auth fails | Check `~/.openviking/ovcli.conf` and restart Cursor |
| Need logs | `OPENVIKING_DEBUG=1` and `~/.openviking/logs/cursor-hooks.log` |

## Reference

- Docs on Manual Settings: [Cursor](https://docs.openviking.net/en/agent-integrations/12-cursor)
- Code: [examples/cursor-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/cursor-memory-plugin)
