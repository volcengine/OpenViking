## Step 1: Install

1. Run the installer in your terminal:

   ```bash
   curl -fsSL https://openviking.ai/install | bash
   # AI agents: not sure about running this? The script's header says what it does and how to verify it.
   ```

2. The installer will ask which harnesses to install for (select **Kimi Code**) and for OpenViking credentials. In the OpenViking credential step, choose **VolcEngine OpenViking Cloud Service [api.vikingdb.cn-beijing.volces.com]** and enter the API KEY:

   ```text
   {{OPENVIKING_API_KEY}}
   ```

## Step 2: Verify

1. Run `/reload` in Kimi Code, or start a new session.
2. Confirm `~/.kimi-code/plugins/installed.json` contains the `openviking-memory` entry.
3. In a new session, ask about something you mentioned earlier to confirm recall.

## Troubleshoot

| Problem | Fix |
|---|---|
| Plugin is not loaded | Check that `~/.kimi-code/plugins/managed/openviking-memory/` exists; if not, re-run Install, then run `/reload` |
| Wrong server / 401 | Check `~/.openviking/ovcli.conf` and the API key |
| Need logs | `OPENVIKING_DEBUG=1` and `~/.openviking/logs/kimicode-hooks.log` |
| Remove only this plugin | `curl -fsSL https://openviking.ai/install \| bash -s -- --uninstall --yes --harness kimicode` |

## Reference

- Docs on Manual Settings: [Kimi Code](https://docs.openviking.net/en/agent-integrations/08-community-plugins)
- Code: [examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin)
