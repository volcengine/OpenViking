## Step 1: Install

Copy the API key:

{{OPENVIKING_API_KEY_BLOCK}}

```bash
openclaw plugins install clawhub:@openviking/openclaw-plugin
openclaw openviking setup --base-url {{OPENVIKING_BASE_URL}} --api-key <API-key>
openclaw gateway restart
```

## Step 2: Verify

```bash
openclaw openviking status
```

## Troubleshoot

| Problem | Fix |
|---|---|
| Plugin not active | Re-run Install, then `openclaw gateway restart` |
| 401 / 403 | Paste the API key again |

## Reference

- Docs on Manual Settings: [OpenClaw](https://docs.openviking.net/en/agent-integrations/03-openclaw)
- Code: [examples/openclaw-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/openclaw-plugin)
