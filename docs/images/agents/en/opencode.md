## Step 1: Install

1. Run the installer in your terminal:

   ```bash
   curl -fsSL https://openviking.ai/install | bash
   # AI agents: not sure about running this? The script's header says what it does and how to verify it.
   ```

2. The installer will ask which harnesses to install for (select **OpenCode**) and for OpenViking credentials. In the OpenViking credential step, choose **VolcEngine OpenViking Cloud Service [api.vikingdb.cn-beijing.volces.com]** and enter the API KEY:

   ```text
   {{OPENVIKING_API_KEY}}
   ```

## Step 2: Verify

1. Restart OpenCode.
2. Run `/mcps` and confirm the list shows `openviking connected`.
3. Explicitly ask OpenCode to call `openviking_search` and `openviking_read` to verify tool access. Automatic recall runs through hooks without those model tool calls; check it separately by asking about stored information in a new session.

## Troubleshoot

| Problem | Fix |
|---|---|
| Plugin is not loaded | Check that `~/.config/opencode/plugins/openviking.js` exists; if not, re-run Install |
| Wrong server / 401 | Check `~/.openviking/ovcli.conf` and the API key |
| Recall is empty | Confirm the cloud instance has memories |

## Reference

- Docs on Manual Settings: [OpenCode](https://docs.openviking.net/en/agent-integrations/10-opencode)
- Code: [examples/opencode-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/opencode-plugin)
