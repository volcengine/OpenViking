# Hermes Agent

[Hermes Agent](https://hermes-agent.nousresearch.com/) by Nous Research loads OpenViking as a memory provider. The provider stores and recalls memory over HTTP, uploads each turn to an OpenViking session, and lets the server extract long-term memories.

There are two copies of the provider:

- **External plugin**, maintained in this repository at [`examples/hermes-plugin`](https://github.com/volcengine/OpenViking/tree/main/examples/hermes-plugin) (current release 3.0.0). This page describes it.
- **Bundled provider**, shipped inside Hermes releases that still include `plugins/memory/openviking/`. It exposes the older `viking_*` tools. While it is present it takes precedence, and the external plugin is not loaded.

## Keep the Python environments separate

Hermes connects to OpenViking over HTTP, so OpenViking does not need to be
installed in the Hermes Python environment. Run the OpenViking server in its
own virtual environment or container. Do not use `--force-reinstall` to add or
upgrade OpenViking in an existing Hermes environment: a Hermes release may pin
dependency versions that differ from OpenViking's supported, security-patched
versions. If you intentionally combine both applications in one environment,
resolve them together and run `python -m pip check` before starting either
service.

## Server versions

| OpenViking server | What works |
|---|---|
| 0.4.13 or newer | Automatic recall and turn capture |
| 0.4.14 or newer | The `openviking_*` tools, served from the server's `/mcp` endpoint |
| 0.4.22 or newer | The full tool set |

## Install and set up

Install the external plugin as described in its
[README](https://github.com/volcengine/OpenViking/tree/main/examples/hermes-plugin#install),
then run the setup wizard:

```bash
hermes memory setup openviking
```

- Cloud: keep **OpenViking Service (VolcEngine Cloud)**, paste the API key
- Custom: URL (default `http://127.0.0.1:1933`) and API key; leave the key empty for local dev
- Reuse an existing `ovcli.conf` profile if the wizard offers one
- Choose **Personal Agent** (recall common memory and the current sender's memory) or **Shared Agent** (recall all senders' memory under the same OpenViking user)

The tools need a user or account admin API key: the server's `/mcp` endpoint rejects the root key.

## What the plugin does

- **Recall**: before each turn, within one 7.5-second budget, the plugin injects a session-start profile block once per session and query recall. Shared and sender-scoped recall use the server's context mode; recall without a known sender uses list search.
- **Capture**: each turn is uploaded to the OpenViking session `hermes-<Hermes session id>`, including tool calls and results. Uploads that fail with a network or server error are kept in memory and resent before the next upload or commit. A commit runs when pending tokens reach 20,000 and at session end or switch.
- **Tools**: the server's MCP tools, registered as `openviking_*`. By default: `openviking_find`, `openviking_search`, `openviking_read`, `openviking_list`, `openviking_tree`, `openviking_grep`, `openviking_glob`, `openviking_remember`, `openviking_forget`, `openviking_add_resource` and `openviking_health`. `write`, `edit`, `add_skill`, `list_watches` and `cancel_watch` are added through the `extra_tools` setting.
- **Native memory mirror**: Hermes built-in `memory` additions, replacements and deletions are mirrored to OpenViking.

Upgrading from 2.x renames the tools without aliases and changes the OpenViking session ids of new uploads. See [Upgrading to 3.0.0](https://github.com/volcengine/OpenViking/tree/main/examples/hermes-plugin#upgrading-to-300).

## Verify

```bash
hermes memory status
```

`available` means that the provider is configured. It does not check server
connectivity or confirm memory extraction.

## See also

- [Hermes plugin README](https://github.com/volcengine/OpenViking/tree/main/examples/hermes-plugin) — configuration, recall routes, tools and upgrade notes
- [Capability Reference](./16-capability-reference.md)
- [Hermes — OpenViking memory provider docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers#openviking) — the bundled provider
- [Deployment Guide](../guides/03-deployment.md) — setting up your OpenViking server
- [Authentication](../guides/04-authentication.md) — API key setup for remote access
