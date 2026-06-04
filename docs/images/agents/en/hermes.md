[Hermes Agent](https://hermes-agent.nousresearch.com/) (Nous Research) includes OpenViking as a built-in memory provider. No plugin installation is required. Point Hermes to your OpenViking service to enable native memory storage, recall, and extraction.

## Step 1: Run the Hermes memory setup wizard

```bash
hermes memory setup
```

## Step 2: Copy the Base URL and API Key

After running the setup command, Hermes prompts for the Base URL and API Key. Copy them and paste them into Hermes:

- Base URL: Copy the following Base URL into Hermes:
```text
https://api.vikingdb.cn-beijing.volces.com/openviking
```
- API Key: Copy the API Key shown on the page into your Hermes terminal
- Tenant account / user / agent ID: Used for multi-tenant deployments

The configuration is saved to Hermes `config.yaml` and `.env` files.

## Step 3: Verify Hermes memory status

```bash
hermes memory status
```

After configuration, Hermes automatically uses OpenViking as long-term memory. Memory tools such as `viking_remember` and `viking_recall` are available immediately.

## Reference docs

- [Hermes - OpenViking memory provider documentation](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers#openviking) - Full configuration guide
- [Deployment Guide](../guides/03-deployment.md) - Set up the OpenViking service
- [Authentication](../guides/04-authentication.md) - API Key settings for remote access
