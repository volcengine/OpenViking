# Install the OpenViking Memory Plugin for GitHub Copilot

This wires [OpenViking](https://github.com/volcengine/OpenViking) into GitHub Copilot as a long-term memory backend. It supports three Copilot surfaces:

| Surface | Status | Config target | Top-level key |
|---|---|---|---|
| VSCode Copilot Chat / agent mode | GA | `.vscode/mcp.json` (workspace) or user profile | `servers` |
| `gh copilot` CLI | GA | `~/.copilot/mcp-config.json` | `mcpServers` |
| GitHub.com Copilot cloud agent + code review | public preview | Repo Settings → Copilot → MCP servers (UI paste) | `mcpServers` |

> **Read this first**: GitHub Copilot does **not** have lifecycle hooks. After install, the model calls the OpenViking MCP tools itself — guided by the bundled Agent Skill — rather than receiving auto-injected recall. See [DESIGN.md](./DESIGN.md) for the full honest scope.

## Prerequisites

- A supported Copilot surface (VSCode with Copilot Chat / agent mode, `gh copilot` CLI, or a GitHub.com repo with Copilot enabled)
- [OpenViking](https://github.com/volcengine/OpenViking) HTTP server running locally or remotely
- Node.js 18+ (only needed for the optional stdio proxy and the config-builder tests)
- An OpenViking API key if your server requires authentication

Start OpenViking first:

```bash
openviking-server --config ~/.openviking/ov.conf
curl http://localhost:1933/health
```

## Install — pick your surface

### `gh copilot` CLI (default)

```bash
bash examples/copilot-plugin/setup-helper/install.sh --cli --with-skill
```

Writes `~/.copilot/mcp-config.json` (top-level `mcpServers`, `type: "http"`) and installs the `openviking-memory` Agent Skill under `~/.copilot/skills/`.

You can also use the official CLI directly (equivalent to what the installer writes):

```bash
copilot mcp add --transport http \
  --header "Authorization: Bearer YOUR_OPENVIKING_API_KEY" \
  openviking https://your-openviking.example.com/mcp
```

### VSCode Copilot Chat / agent mode

```bash
bash examples/copilot-plugin/setup-helper/install.sh --vscode
```

Writes `.vscode/mcp.json` in the current workspace (top-level `servers`, `type: "http"`). Commit it to share with your team; or use the `MCP: Open User Configuration` command to install it in your user profile instead.

> **Shortcut**: if you already have OpenViking wired into Claude Desktop, VSCode can auto-discover that config — enable `chat.mcp.discovery.enabled` in VSCode settings.

### GitHub.com repo-level (Copilot cloud agent + code review)

```bash
bash examples/copilot-plugin/setup-helper/install.sh --repo
```

Prints a JSON snippet. Paste it into the repo's Settings → Copilot → MCP servers page. **Create an Agents secret named `COPILOT_MCP_OPENVIKING_API_KEY` first** — the snippet references the key as `${COPILOT_MCP_OPENVIKING_API_KEY}` so the real key is never stored in the config.

Defaults to a **read-only tool allowlist** (`recall, search, find, read, list, grep, glob`) because the cloud agent invokes enabled tools autonomously without approval. Add `remember` to the `tools` array only if you want the cloud agent to be able to persist memories.

## Non-interactive install

```bash
OPENVIKING_URL=https://your-openviking.example.com \
OPENVIKING_API_KEY=sk-... \
bash examples/copilot-plugin/setup-helper/install.sh --cli --with-skill --yes
```

## Optional: stdio MCP proxy instead of direct HTTP

Most users should use direct HTTP (above). Use the stdio proxy only if you want credentials auto-resolved from `~/.openviking/ovcli.conf` (handy when you rotate keys) or you run OpenViking locally without an API key.

The proxy is at [`servers/mcp-proxy.mjs`](./servers/mcp-proxy.mjs) and reuses the same credential resolution as the ZCode / Codex / Cursor plugins. Reference it from your MCP config:

```json
{
  "mcpServers": {
    "openviking": {
      "type": "local",
      "command": "node",
      "args": ["/abs/path/to/examples/copilot-plugin/servers/mcp-proxy.mjs"],
      "tools": ["*"]
    }
  }
}
```

(VSCode `.vscode/mcp.json` uses the same shape under `servers`, but omit the `type` field.)

## Verify

1. **Restart the target Copilot surface** so it picks up the new MCP config (VSCode: reload window; CLI: start a new session).
2. Confirm the OpenViking MCP server is connected:
   - VSCode: open the Chat view and check the MCP panel / Configure Tools.
   - CLI: `copilot mcp list`.
3. Test recall: in a fresh session, ask the agent "use OpenViking to recall what we decided about X" or simply ask a question whose answer depends on prior OpenViking memory.
4. Test remember: tell the agent "remember that I prefer tabs over spaces", then in a new session ask "what indentation do I prefer?".
5. If recall seems absent, install the Agent Skill (`--with-skill`) and retry; the skill's job is exactly to make the model call `recall` proactively.

## Path overrides

| Env var | Default | Purpose |
|---|---|---|
| `COPILOT_CLI_CONFIG_DIR` | `~/.copilot` | Copilot CLI config directory |
| `COPILOT_VSCODE_DIR` | `./.vscode` | VSCode workspace `.vscode` dir |
| `COPILOT_SKILLS_DIR` | `~/.copilot/skills` | Where the Agent Skill is installed |
| `OPENVIKING_HOME` | `~/.openviking` | OpenViking home |
| `OPENVIKING_CLI_CONFIG_FILE` | `~/.openviking/ovcli.conf` | OpenViking CLI config path |

## Configuration

Connection and identity are resolved by the shared runtime (same as the other plugins):

1. **Default**: active `~/.openviking/ovcli.conf` wins — use `ov config switch <name>` to change it for MCP and any `ov` command run inside Copilot together.
2. **Env-forced**: `OPENVIKING_CREDENTIAL_SOURCE=env` forces `OPENVIKING_URL` / `OPENVIKING_API_KEY` / `OPENVIKING_ACCOUNT` / `OPENVIKING_USER` / `OPENVIKING_PEER_ID`.
3. **Fallback**: `~/.openviking/ov.conf` legacy fields, then `http://127.0.0.1:1933` unauthenticated.

Auth is `Authorization: Bearer <api_key>`. A workspace peer is derived from the current workspace path unless overridden — see the [shared library README](../memory-plugin-shared/README.md).

## Upgrade and uninstall

Re-run the installer to upgrade. Uninstall by removing the OpenViking-managed block from your target surface:

```bash
# CLI
copilot mcp remove openviking
rm -rf ~/.copilot/skills/openviking-memory    # if --with-skill was used

# VSCode
# edit .vscode/mcp.json and remove the "openviking" entry under "servers"

# GitHub.com repo
# repo Settings → Copilot → MCP servers → remove the openviking block
```

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| MCP does not connect | Check the URL/API key in `~/.openviking/ovcli.conf`, then restart the Copilot surface. |
| Tools not appearing (CLI) | Run `copilot mcp list` and check the `tools` allowlist in `~/.copilot/mcp-config.json`. |
| Tools not appearing (VSCode) | Open Configure Tools in the chat input and toggle the OpenViking tools on. |
| Enterprise users see "MCP disabled" | The org/enterprise "MCP servers in Copilot" policy is off by default; ask your admin. (Does not affect Copilot Free/Pro/Pro+/Max.) |
| Recall does not happen automatically | Install the Agent Skill (`--with-skill`). Without it, the model has no policy telling it to call `recall` proactively. Even with it, recall is best-effort — Copilot has no hooks. |
| Repo-level cloud agent shows the MCP server but tools do nothing | Public-preview caveat: cloud agent supports tools only (no resources/prompts) and no OAuth. Use API-key auth. |

## See also

- [DESIGN.md](./DESIGN.md) — the honest scope, assumptions, and decision rationale.
- [RESEARCH.md](./RESEARCH.md) — full source links for every Copilot extension-surface claim.
- [Authentication](../../docs/en/guides/04-authentication.md)
- [MCP Integration Guide](../../docs/en/guides/06-mcp-integration.md)
