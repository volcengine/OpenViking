## Step 1: Install

```bash
hermes memory setup openviking
```

Keep **OpenViking Service (VolcEngine Cloud)**. Paste the API key:

{{OPENVIKING_API_KEY_BLOCK}}

## Step 2: Verify

```bash
hermes memory status
```

Expect `Provider: openviking` and `Status: available`. Start a new Hermes session.

## Troubleshoot

| Problem | Fix |
|---|---|
| Provider is not openviking | Re-run `hermes memory setup openviking` |
| Status is not available | Check the API key |

## Reference

- Docs on Manual Settings: [Hermes](https://docs.openviking.net/en/agent-integrations/05-hermes)
- Blog about how it works: [OpenViking memory provider](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers#openviking)
