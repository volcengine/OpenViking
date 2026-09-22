## Step 1: Install

1. Run the command that matches your client:

   **Trae International**

   ```bash
   bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae --dist tos
   ```

   **Trae China**

   ```bash
   bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae-cn --dist tos
   ```

   **TraeCode CLI 2.0**

   ```bash
   bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) --harness trae-cli --dist tos
   ```

2. The installer will ask for language (English / Chinese) and OpenViking credentials. In the OpenViking credential step, choose **VolcEngine OpenViking Cloud Service [api.vikingdb.cn-beijing.volces.com]** and enter the API KEY:

   ```text
   {{OPENVIKING_API_KEY}}
   ```

## Step 2: Verify

**TRAE / TRAE CN**: open **Settings → MCP → Configured MCP Servers** and confirm that the `openviking` entry is visible.

**TraeCode CLI 2.0**: hooks only run once you trust them. Start `trae-cli` and pick **Trust all and continue** at the prompt:

```text
Hooks need review
6 hooks are new or changed.
Hooks can run outside the sandbox after you trust them.

  1. Review hooks
> 2. Trust all and continue
  3. Continue without trusting (hooks won't run)
```

Then run `trae-cli plugin list` and confirm that `openviking-memory` is enabled. If you miss the prompt or pick the third option, the hooks never run: enter `/hooks` to trust and enable the entries, and check `/plugins` shows the plugin as enabled — two independent switches, both have to be on. A plugin update that touches a hook asks for trust again.

## Troubleshoot

| Problem | Fix |
|---|---|
| No auto recall | Quit TRAE completely, restart, new Agent session |
| TraeCode CLI 2.0 has the plugin but recalls nothing | The startup hook trust was skipped: trust and enable in `/hooks`, confirm the plugin is enabled in `/plugins` |
| Connection / auth fails | Check `~/.openviking/ovcli.conf` and restart the client |
| Need logs | `~/.openviking/logs/trae-hooks.log`, `trae-cn-hooks.log`, or `codex-hooks.log` (TraeCode CLI 2.0) |

## Reference

- Docs on Manual Settings: [TRAE](https://docs.openviking.net/en/agent-integrations/13-trae)
- Code: [examples/agent-hook-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/agent-hook-plugin) (TRAE / TRAE CN), [examples/codex-memory-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/codex-memory-plugin) (TraeCode CLI 2.0)
