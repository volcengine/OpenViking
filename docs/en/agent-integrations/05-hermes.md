# Hermes Agent

[Hermes Agent](https://hermes-agent.nousresearch.com/) by Nous Research has a first-class OpenViking memory provider built in. No plugin to install — just point Hermes at your OpenViking server and it handles memory storage, recall, and extraction natively.

## Keep the Python environments separate

Hermes connects to OpenViking over HTTP, so OpenViking does not need to be
installed in the Hermes Python environment. Run the OpenViking server in its
own virtual environment or container. Do not use `--force-reinstall` to add or
upgrade OpenViking in an existing Hermes environment: a Hermes release may pin
dependency versions that differ from OpenViking's supported, security-patched
versions. If you intentionally combine both applications in one environment,
resolve them together and run `python -m pip check` before starting either
service.

## Setup

```bash
hermes memory setup openviking
```

Passing `openviking` skips the provider picker. Bare `hermes memory setup` still works, then choose **openviking**.

If Hermes finds an existing `~/.openviking/ovcli.conf`, reuse that profile. Otherwise the wizard asks:

- **OpenViking connection** — **OpenViking Service (VolcEngine Cloud)** (default) or **Custom** (local / VPS / self-hosted)
- **Cloud** — the managed endpoint is filled in; paste your API key
- **Custom** — server URL (default `http://127.0.0.1:1933`), then a user API key, a root API key, or no key for local dev
- **Hermes peer ID** — default `hermes`

Account / user IDs are only needed with a root API key. A user API key is enough for Volcengine Cloud. Legacy `agent_id` settings map to the request actor peer during migration.

Configuration is saved to Hermes `config.yaml` and `.env`. The wizard can also mirror it into `~/.openviking/ovcli.conf.<name>`.

## Verify

```bash
hermes memory status
```

Once configured, Hermes uses the OpenViking memory provider to inject context, prefetch relevant memories, and sync and extract memories after sessions. Available tools include `viking_search`, `viking_read`, `viking_browse`, `viking_remember`, `viking_forget`, and `viking_add_resource`.

## See also

- [Hermes — OpenViking memory provider docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers#openviking) — full setup guide and configuration options
- [Deployment Guide](../guides/03-deployment.md) — setting up your OpenViking server
- [Authentication](../guides/04-authentication.md) — API key setup for remote access
