# Hermes Agent

[Hermes Agent](https://hermes-agent.nousresearch.com/) includes OpenViking as a native memory provider. No plugin installation is required.

## Configure memory

Run the Hermes memory setup wizard:

```bash
hermes memory setup
```

The wizard asks for:

- OpenViking service URL, such as `https://api.vikingdb.cn-beijing.volces.com/openviking`
- API Key
- Tenant account, user, and agent ID for multi-tenant deployments

Hermes stores the configuration in `config.yaml` and `.env`.

## Verify

```bash
hermes memory status
```

After setup, Hermes uses OpenViking for long-term memory storage, recall, and extraction.
