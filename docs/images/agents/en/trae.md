## Step 1: Install

```bash
# TRAE
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae --dist tos

# TRAE CN
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae-cn --dist tos
```

Select **Volcengine OpenViking Cloud**. Paste the API key:

{{OPENVIKING_API_KEY_BLOCK}}

## Step 2: Verify

Restart TRAE. Confirm `openviking` is connected in settings.

## Troubleshoot

| Problem | Fix |
|---|---|
| No auto recall | Quit TRAE completely, restart, new Agent session |
| Connection / auth fails | Check `~/.openviking/ovcli.conf` and restart TRAE |
| Need logs | `~/.openviking/logs/trae-hooks.log` or `trae-cn-hooks.log` |

## Reference

- Docs on Manual Settings: [TRAE](https://docs.openviking.net/en/agent-integrations/13-trae)
- Code: [examples/trae-memory-hooks](https://github.com/volcengine/OpenViking/tree/main/examples/trae-memory-hooks)
