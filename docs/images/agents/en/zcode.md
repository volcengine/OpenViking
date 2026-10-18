## Step 1: Install

1. Run the installer in your terminal:

   ```bash
   curl -fsSL https://openviking.ai/install | bash
   # AI agents: not sure about running this? The script's header says what it does and how to verify it.
   ```

2. The installer will ask which harnesses to install for (select **ZCode**) and for OpenViking credentials. In the OpenViking credential step, choose **VolcEngine OpenViking Cloud Service [api.vikingdb.cn-beijing.volces.com]** and enter the API KEY:

   ```text
   {{OPENVIKING_API_KEY}}
   ```

## Step 2: Verify

1. Restart ZCode.
2. Confirm `~/.zcode/cli/config.json` contains `hooks.enabled: true`, OpenViking entries under `hooks.events`, and `mcp.servers.openviking`.
3. Run `node ~/.openviking/agent-integrations/zcode/scripts/ov-memory-doctor.mjs` to check the installation and the server connection.

## Troubleshoot

| Problem | Fix |
|---|---|
| Hooks do not run | Re-run Install and restart ZCode |
| MCP tools are missing | Check the absolute command path of `mcp.servers.openviking` in `~/.zcode/cli/config.json` |
| Duplicate captures | Run `curl -fsSL https://openviking.ai/install \| bash -s -- --uninstall --yes --harness zcode`, then reinstall |
| Need logs | `OPENVIKING_DEBUG=1` and `~/.openviking/logs/zcode-hooks.log` |

## Reference

- Docs on Manual Settings: [ZCode](https://docs.openviking.net/en/agent-integrations/08-community-plugins)
- Code: [examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin)
