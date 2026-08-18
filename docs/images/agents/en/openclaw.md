## Install

```bash
openclaw plugins install clawhub:@openviking/openclaw-plugin
openclaw openviking setup --base-url https://api.vikingdb.cn-beijing.volces.com/openviking --api-key <API-key-from-this-page>
openclaw gateway restart
```

## Verify

```bash
openclaw openviking status
```

## Troubleshoot

| Problem | Fix |
|---|---|
| Plugin not active | Re-run Install, then `openclaw gateway restart` |
| 401 / 403 | Paste the API key from this page again |

## Reference

- Docs on Manual Settings: [OpenClaw](https://docs.openviking.net/en/agent-integrations/03-openclaw)
- Code: [examples/openclaw-plugin](https://github.com/volcengine/OpenViking/tree/main/examples/openclaw-plugin)
