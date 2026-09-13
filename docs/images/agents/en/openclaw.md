## Step 1: Install

1. Install the OpenViking plugin:

   ```bash
   openclaw plugins install clawhub:@openviking/openclaw-plugin
   ```

2. Connect OpenClaw to the VolcEngine-hosted OpenViking service:

   ```bash
   openclaw openviking setup --base-url https://api.vikingdb.cn-beijing.volces.com/openviking --api-key <$OPENVIKING_API_KEY>
   ```

3. Configure `peer_role`: `peer_role` identifies the type of conversation participant. It is not a permission role. `assistant` represents agents, tools, or models; `person` represents human participants. After the setup above, `peer_role` defaults to `none`. To change `peer_role`, run:

   ```bash
   openclaw openviking setup --reconfigure
   ```

4. Restart the Gateway to apply the configuration:

   ```bash
   openclaw gateway restart
   ```

## Step 2: Verify

1. Check the integration status in your terminal:

   ```bash
   openclaw openviking status
   ```

2. A result similar to the following means the integration is successful:

   ```text
   🦣 OpenViking Plugin Status

     Status: Configured
     mode:      remote
     baseUrl:   `https://api.vikingdb.cn-beijing.volces.com/openviking`
     apiKey:    set
     peer_role: none
     accountId: not set
     userId:    not set
     slot:      active

     ✓ Server reachable (version: v0.x.xx.x)
   ```

## Troubleshoot

| Problem | Fix |
|---|---|
| Plugin not active | Re-run Install, then `openclaw gateway restart` |
| 401 / 403 | Refresh credentials |

## Reference

- Docs on Manual Settings: [OpenClaw](https://docs.openviking.net/en/agent-integrations/03-openclaw)
- Code: [examples/openclaw-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/openclaw-plugin)
