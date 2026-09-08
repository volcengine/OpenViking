## Step 1: Install

1. Run the installer in your terminal:

   ```bash
   bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness opencode --dist tos
   ```

2. The installer will ask for language (English / Chinese) and OpenViking credentials. In the OpenViking credential step, choose **VolcEngine OpenViking Cloud Service [api.vikingdb.cn-beijing.volces.com]** and enter the API KEY:

   ```text
   {{OPENVIKING_API_KEY}}
   ```

## Step 2: Verify

1. Restart OpenCode.
2. Run `/mcps` and confirm the list shows `openviking connected`.
3. Ask OpenCode to recall related memories in a conversation, and verify that it can automatically call tools such as `openviking_search`, `openviking_read`, and `openviking_remember`.

## Troubleshoot

| Problem | Fix |
|---|---|
| Plugin is not loaded | Check `~/.config/opencode/opencode.json` includes `@openviking/opencode-plugin` |
| Wrong server / 401 | Check `~/.openviking/ovcli.conf` and the API key |
| Recall is empty | Confirm the cloud instance has memories |

## Reference

- Docs on Manual Settings: [OpenCode](https://docs.openviking.net/en/agent-integrations/10-opencode)
- Code: [examples/opencode-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-plugin)
