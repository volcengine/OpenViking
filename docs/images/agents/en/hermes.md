[Hermes Agent](https://hermes-agent.nousresearch.com/) (Nous Research) includes OpenViking as a built-in memory provider. No plugin installation is required. Point Hermes to your OpenViking service to enable native memory storage, recall, and extraction.

## Step 1: Run the Hermes memory setup wizard

```bash
hermes memory setup
```

The wizard asks for:

- **OpenViking service URL** - A self-hosted server (default `http://127.0.0.1:1933`) or Volcengine OpenViking Cloud
- **API Key** - Leave empty for local development mode
- **Tenant account / user / agent ID** - Used for multi-tenant deployments

The configuration is saved to Hermes `config.yaml` and `.env` files.

## Step 2: Verify Hermes memory status

```bash
hermes memory status
```

After configuration, Hermes automatically uses OpenViking as long-term memory. Memory tools such as `viking_remember` and `viking_recall` are available immediately.

## Reference docs

- [Hermes - OpenViking memory provider documentation](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers#openviking) - Full configuration guide
- [Deployment Guide](../guides/03-deployment.md) - Set up the OpenViking service
- [Authentication](../guides/04-authentication.md) - API Key settings for remote access
