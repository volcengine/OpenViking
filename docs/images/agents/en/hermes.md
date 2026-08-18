## Install

```bash
hermes memory setup openviking
```

Keep **OpenViking Service (VolcEngine Cloud)**. Paste the API key from this page.

## Verify

```bash
hermes memory status
```

Expect `Provider: openviking` and `Status: available`. Start a new Hermes session.

## Troubleshoot

| Problem | Fix |
|---|---|
| Provider is not openviking | Re-run `hermes memory setup openviking` |
| Status is not available | Check the API key from this page |

## Reference

- Docs: [Hermes](https://docs.openviking.net/en/agent-integrations/05-hermes)
- Hermes provider: [OpenViking memory](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers#openviking)
