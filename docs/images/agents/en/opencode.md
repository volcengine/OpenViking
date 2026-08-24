## Step 1: Install

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness opencode --dist tos
```

Select **Volcengine OpenViking Cloud**. Paste the API key:

## Step 2: Verify

Restart OpenCode. Ask it to search OpenViking memory. Tools look like `openviking_search`, `openviking_read`, `openviking_remember`.

## Troubleshoot

| Problem | Fix |
|---|---|
| Plugin is not loaded | Check `~/.config/opencode/opencode.json` includes `@openviking/opencode-plugin` |
| Wrong server / 401 | Check `~/.openviking/ovcli.conf` and the API key |
| Recall is empty | Confirm the cloud instance has memories |

## Reference

- Docs on Manual Settings: [OpenCode](https://docs.openviking.net/en/agent-integrations/10-opencode)
- Code: [examples/opencode-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-plugin)
