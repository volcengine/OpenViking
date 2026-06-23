# Hermes Agent

[Hermes Agent](https://hermes-agent.nousresearch.com/) by Nous Research has a first-class OpenViking memory provider built in. No plugin to install — just point Hermes at your OpenViking server and it handles memory storage, recall, and extraction natively.

## Setup

Run the Hermes memory setup wizard:

```bash
hermes memory setup
```

The wizard prompts for:

- **OpenViking server URL** — your self-hosted server (default `http://127.0.0.1:1933`) or OpenViking Service (VolcEngine Cloud)
- **API key** — leave blank for local dev mode
- **Tenant account / user / peer IDs** — for multi-tenant deployments. Legacy `agent_id` settings map to the request actor peer during migration.

Configuration is saved to Hermes's `config.yaml` and `.env` files.

## Verify

```bash
hermes memory status
```

Once configured, Hermes automatically uses OpenViking for long-term memory — `viking_remember`, `viking_recall`, and other memory tools become available to the agent.

## See also

- [Hermes — OpenViking memory provider docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers#openviking) — full setup guide and configuration options
- [Deployment Guide](../guides/03-deployment.md) — setting up your OpenViking server
- [Authentication](../guides/04-authentication.md) — API key setup for remote access
