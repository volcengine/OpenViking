## Step 1: Install

1. Run the command that matches your TRAE version:

   **Trae International**

   ```bash
   bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae --dist tos
   ```

   **Trae China**

   ```bash
   bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae-cn --dist tos
   ```

2. The installer will ask for language (English / Chinese) and OpenViking credentials. In the OpenViking credential step, choose **VolcEngine OpenViking Cloud Service [api.vikingdb.cn-beijing.volces.com]** and enter the API KEY:

   ```text
   {{OPENVIKING_API_KEY}}
   ```

## Step 2: Verify

Open **Settings → MCP → Configured MCP Servers** and confirm that the `openviking` entry is visible.

## Troubleshoot

| Problem | Fix |
|---|---|
| No auto recall | Quit TRAE completely, restart, new Agent session |
| Connection / auth fails | Check `~/.openviking/ovcli.conf` and restart TRAE |
| Need logs | `~/.openviking/logs/trae-hooks.log` or `trae-cn-hooks.log` |

## Reference

- Docs on Manual Settings: [TRAE](https://docs.openviking.net/en/agent-integrations/13-trae)
- Code: [examples/trae-memory-hooks](https://github.com/volcengine/OpenViking/tree/main/examples/trae-memory-hooks)
