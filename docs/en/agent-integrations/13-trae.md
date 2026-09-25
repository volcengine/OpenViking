# TRAE, TRAE CN, and TraeCode CLI 2.0 Memory Integration

Give TRAE, TRAE CN, and TraeCode CLI 2.0 long-term memory across projects and sessions. OpenViking Hooks automatically load relevant context, capture each conversation turn, and commit it for memory extraction. MCP remains available for explicit memory search, reading, and management.

## Install

Prerequisites: macOS or Linux, Node.js 18+, and a TRAE/TRAE CN release that supports the `SessionStart`, `UserPromptSubmit`, `PreToolUse`, and `Stop` Hooks. TraeCode CLI 2.0 uses the Codex-compatible plugin format directly. The installer guides you through the OpenViking connection settings.

When prompted for the connection, Volcengine Cloud users should select **Volcengine OpenViking Cloud** and enter their API key. Select **Self-hosted / local** for a server on this machine (`http://127.0.0.1:1933`); for a remote self-hosted server, select **Custom URL / keep current** and enter its URL.

```bash
# TRAE
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness trae

# TRAE CN
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness trae-cn

# Both
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness trae,trae-cn

# TraeCode CLI 2.0
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness trae-cli
```

If GitHub is unavailable, use the TOS mirror:

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness trae,trae-cn --dist tos

# TraeCode CLI 2.0
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness trae-cli --dist tos
```

Quit and restart the corresponding client after installation.

### TraeCode CLI 2.0: trust the hooks on first launch

TraeCode CLI 2.0 uses the Codex-format plugin. Starting `trae-cli` shows a hook review prompt similar to the one below; the exact layout varies by version. Pick **Trust all and continue**; *Continue without trusting* leaves the hooks off.

```text
Hooks need review
6 hooks are new or changed.
Hooks can run outside the sandbox after you trust them.

  1. Review hooks
> 2. Trust all and continue
  3. Continue without trusting (hooks won't run)
```

You can also use `/hooks` to review the OpenViking commands and trust and enable the hooks you want to use. Confirm `openviking-memory` is enabled in `/plugins`. Modified definitions may need review again. MCP connectivity alone does not verify recall or capture; see the [Codex hook setup](04-codex.md#first-launch-trust-the-hooks).

TRAE and TRAE CN use the installed `hooks.json` and need no hook approval; restart the corresponding client after installation.

## What gets installed

- `SessionStart` loads your profile, current project memory, and an `<available-skills>` catalog of your OpenViking skills.
- `UserPromptSubmit` recalls and injects context for the current request, including your own skills and those shared with your account under `viking://agent/skills`.
- `PreToolUse` on TRAE and TRAE CN denies `Read`, `Glob`, and `Grep` calls whose path is a `viking://` URI and points to OpenViking MCP tools; a `Bash` or `RunCommand` command that carries a `viking://` URI still runs, with a notice suggesting those tools. TraeCode CLI 2.0 uses the Codex plugin, whose `PreToolUse` matches only `Bash`: it adds the same notice and never denies a call.
- TRAE/TRAE CN `Stop` captures and commits the completed turn. TraeCode CLI 2.0 follows the Codex plugin’s capture and lifecycle commit policy.
- The OpenViking MCP server transparently exposes the full server MCP tool set (16 tools): `find`, `search`, `read`, `list`, `tree`, `remember`, `write`, `edit`, `add_resource`, `add_skill`, `list_watches`, `cancel_watch`, `grep`, `glob`, `forget`, and `health`. `search` with `mode="context"` returns assembled context.
The skill catalog lists your own skills before those shared with your account, with each description cut to about 40 tokens. Its budget, `skillCatalogTokenBudget` (default `1200` tokens), is separate from the profile budget; when not every description fits, the catalog lists names only. Set `skillCatalog` to `false` or the budget to `0` to turn it off, either in the `plugin` section of `~/.openviking/ovcli.conf` ([Plugin Settings](../configuration/02-client.md#plugin-settings)) or through `OPENVIKING_SKILL_CATALOG` and `OPENVIKING_SKILL_CATALOG_TOKEN_BUDGET`.

## Verify

1. Restart TRAE, TRAE CN, or TraeCode CLI 2.0 and create a new Agent session.
2. Confirm that `openviking` is connected in the client's MCP settings.
3. Ask about an existing project or preference and confirm that the answer uses stored memory.
4. Tell the Agent a temporary preference, confirm capture and commit in the logs, then wait for memory extraction before asking about it in a new session in the same workspace.
5. For TraeCode CLI 2.0, run `trae-cli plugin list` to confirm that `openviking-memory` is enabled, then run `/hooks` in the session and confirm the OpenViking entries are trusted and switched on.

For Hook diagnostics, start the client with `OPENVIKING_DEBUG=1` and inspect:

- TRAE: `~/.openviking/logs/trae-hooks.log`
- TRAE CN: `~/.openviking/logs/trae-cn-hooks.log`
- TraeCode CLI 2.0: `~/.openviking/logs/codex-hooks.log`

## Upgrade and uninstall

Re-run the corresponding install command to upgrade. Use the original distribution channel for uninstall:

```bash
# GitHub, TRAE CN example
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness trae-cn --uninstall --yes

# TOS, TRAE CN example
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness trae-cn --uninstall --yes
```

Replace `trae-cn` with `trae` for TRAE. For TraeCode CLI 2.0, use `trae-cli plugin uninstall openviking-memory@openviking`. Running the installer with `--harness trae-cli --uninstall` only removes the deprecated standalone Hooks integration from older installations.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| Automatic recall does not run | Quit the client completely, restart it, and create a new Agent session. |
| TraeCode CLI 2.0 has the plugin, but nothing is recalled or captured | The startup hook-trust prompt was skipped or answered with *Continue without trusting*; an update that touches a hook asks for trust again. Trust and enable the OpenViking entries in `/hooks`, and confirm `openviking-memory` is enabled in `/plugins` — two independent switches, both have to be on. |
| MCP does not connect | Check the URL/API key in `~/.openviking/ovcli.conf`, then restart the client. |
| A new session cannot recall the previous turn | Inspect the Hook log and confirm that `Stop` ran without `/commit` connection or authentication errors. |
| The same content is captured more than once | Check user and project Hooks for older `trae-auto-recall.mjs` or `trae-auto-capture.mjs` entries. Re-running the installer removes OpenViking-managed legacy entries. |
| TraeCode CLI 2.0 does not list the plugin | Run `trae-cli plugin list`; if `openviking-memory` is absent, rerun the installer with `--harness trae-cli`. |

## See also

- [Capability Reference](./16-capability-reference.md)
- [Authentication](../guides/04-authentication.md)
