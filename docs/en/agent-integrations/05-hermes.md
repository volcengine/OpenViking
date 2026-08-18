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

- Cloud: keep **OpenViking Service (VolcEngine Cloud)**, paste the API key
- Custom: URL (default `http://127.0.0.1:1933`) and API key; leave the key empty for local dev
- Reuse an existing `ovcli.conf` profile if the wizard offers one

## Verify

```bash
hermes memory status
```

## See also

- [Capability Reference](./16-capability-reference.md)
- [Hermes — OpenViking memory provider docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers#openviking) — full setup guide and configuration options
- [Deployment Guide](../guides/03-deployment.md) — setting up your OpenViking server
- [Authentication](../guides/04-authentication.md) — API key setup for remote access
