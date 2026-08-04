# Install the OpenViking Memory Hooks for ZCode

This installs ZCode lifecycle hooks (auto-recall, turn capture, `viking://` URI guard), an OpenViking stdio MCP proxy, and the credential glue so ZCode can use [OpenViking](https://github.com/volcengine/OpenViking) as its long-term memory backend.

It mirrors the structure of the TRAE and Cursor integrations and reuses the same shared runtime.

## Prerequisites

- ZCode (the ByteDance AI coding agent)
- [OpenViking](https://github.com/volcengine/OpenViking) HTTP server running locally or remotely
- Node.js 18+
- An OpenViking API key if your server requires authentication

Start OpenViking first:

```bash
openviking-server --config ~/.openviking/ov.conf
curl http://localhost:1933/health
```

## Install

From a checkout of this repository:

```bash
bash examples/zcode-memory-plugin/setup-helper/install.sh
```

The installer:

1. Renders `hooks/hooks.json` with the absolute plugin path substituted in for `__OPENVIKING_ZCODE_ROOT__`, and writes it to the ZCode config directory (default `~/.zcode/hooks.json`).
2. Adds an `openviking` entry to the ZCode MCP config (default `~/.zcode/mcp.json`) pointing at `servers/mcp-proxy.mjs`. Any other `mcpServers` entries you already have are preserved.
3. Sets up `~/.openviking/ovcli.conf` if it does not already exist (prompts for URL and API key, or uses `OPENVIKING_URL` / `OPENVIKING_API_KEY` in non-interactive mode).
4. Runs the plugin's hook tests as a smoke test.

### Non-interactive install

```bash
OPENVIKING_URL=https://your-openviking.example.com \
OPENVIKING_API_KEY=sk-... \
bash examples/zcode-memory-plugin/setup-helper/install.sh --yes
```

### Path overrides

| Env var | Default | Purpose |
|---|---|---|
| `ZCODE_CONFIG_DIR` | `~/.zcode` | ZCode config directory |
| `ZCODE_HOOKS_FILE` | `hooks.json` | hooks file name inside the config dir |
| `ZCODE_MCP_FILE` | `mcp.json` | MCP config file name |
| `OPENVIKING_HOME` | `~/.openviking` | OpenViking home directory |
| `OPENVIKING_CLI_CONFIG_FILE` | `~/.openviking/ovcli.conf` | OpenViking CLI config path |

## Configuration

Connection and identity are resolved by the shared runtime. Both the hooks and the MCP proxy read the same source, so they always target the same server.

1. **Default**: active `~/.openviking/ovcli.conf` wins — use `ov config switch <name>` to change it for hooks, MCP, and any `ov` command run inside ZCode together.
2. **Env-forced**: set `OPENVIKING_CREDENTIAL_SOURCE=env` to force `OPENVIKING_URL` / `OPENVIKING_API_KEY` / `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` / `OPENVIKING_PEER_ID`.
3. **Fallback**: `~/.openviking/ov.conf` legacy fields, then `http://127.0.0.1:1933` unauthenticated.

Auth is sent as `Authorization: Bearer <api_key>` to both the REST API (used by hooks) and the `/mcp` endpoint (used by the model). The actor peer is derived from the current workspace path using Claude's project-directory naming rule unless overridden — see the [shared library README](../memory-plugin-shared/README.md) for the full rule and how to disable it.

## Verify

1. Quit ZCode completely and restart it.
2. In a new ZCode session, confirm the OpenViking MCP server is connected (per ZCode's MCP UI).
3. Tell ZCode a temporary preference, wait for the response to finish, then create a new session and ask for that preference back to verify capture and cross-session recall.
4. For hook diagnostics, start ZCode with `OPENVIKING_DEBUG=1` and inspect `~/.openviking/logs/zcode-hooks.log`.

## Available MCP Tools

The stdio proxy forwards the server's real `tools/list` response. Current OpenViking servers expose:

- `recall`, `search`, `find` — semantic retrieval
- `remember`, `forget`, `add_resource` — memory / resource management
- `read`, `list`, `grep`, `glob` — `viking://` filesystem
- `code_search`, `code_outline`, `code_expand` — indexed code navigation
- `list_watches`, `cancel_watch`, `health`

Tool names are namespaced by ZCode per its MCP client convention.

## Upgrade and uninstall

Re-run the installer from the same checkout to upgrade. Uninstall removes only OpenViking-managed files:

```bash
rm ~/.zcode/hooks.json          # if ZCode owns it; otherwise edit out the OpenViking block
# then remove the `openviking` entry from ~/.zcode/mcp.json
```

Restart ZCode afterwards.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| Hooks do not run | Quit ZCode completely, restart it, and create a new Agent session. Confirm `~/.zcode/hooks.json` references the absolute plugin path. |
| MCP does not connect | Check the URL/API key in `~/.openviking/ovcli.conf`, then restart ZCode. |
| A new session cannot recall the previous turn | Inspect `~/.openviking/logs/zcode-hooks.log` with `OPENVIKING_DEBUG=1` and confirm `Stop` ran without `/commit` connection or authentication errors. |
| Hook event names differ | ZCode's extension surface is not yet publicly documented. If ZCode uses different event names, rename the keys in `hooks/hooks.json`; the scripts themselves are event-name-agnostic. See [DESIGN.md](./DESIGN.md). |

## See also

- [DESIGN.md](./DESIGN.md) — assumptions about ZCode's extension surface and what needs confirmation.
- [Authentication](../../docs/en/guides/04-authentication.md)
- [MCP Integration Guide](../../docs/en/guides/06-mcp-integration.md)
