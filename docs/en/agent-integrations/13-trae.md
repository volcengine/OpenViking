# TRAE, TRAE CN, and TraeCode CLI 2.0 Memory Integration

Give TRAE, TRAE CN, and TraeCode CLI 2.0 long-term memory across projects and sessions. OpenViking Hooks automatically load relevant context, capture each conversation turn, and commit it for memory extraction. MCP remains available for explicit memory search, reading, and management.

## Install

Prerequisites: macOS or Linux, Node.js 18+, and a TRAE/TRAE CN release that supports the `SessionStart`, `UserPromptSubmit`, `PreToolUse`, and `Stop` Hooks. TraeCode CLI 2.0 uses the Codex-compatible plugin format directly. The installer guides you through the OpenViking connection settings.

When prompted for the connection, Volcengine Cloud users should select **Volcengine OpenViking Cloud** and enter their API key. Select **Self-hosted / local** only when an OpenViking server is running locally.

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

TraeCode CLI 2.0 hooks only run once you trust them. Starting `trae-cli` stops on this prompt — pick **Trust all and continue**, or Review hooks first if you want to read the commands:

```text
Hooks need review
6 hooks are new or changed.
Hooks can run outside the sandbox after you trust them.

  1. Review hooks
> 2. Trust all and continue
  3. Continue without trusting (hooks won't run)
```

A fresh install lists all 6 hooks the plugin registers. After that, any plugin update that touches a hook stops startup again, with the count of what changed this time (`1 hook is new or changed`, say) — pick Trust all and continue there too.

Pick the third option, or skip past the prompt, and the hooks never run: MCP tools still work, but automatic recall and capture are both dead. Recovering means switching on two independent things:

- `/hooks` — hook trust and on/off state. Trust and enable the entries marked *New hook - review required* or *Modified since last trusted*.
- `/plugins` — the plugin's own enabled state. Confirm `openviking-memory` is enabled.

TRAE and TRAE CN read `hooks.json` directly and need no such approval — restarting the client is enough.

## What gets installed

- `SessionStart` loads your profile, current project memory, and an `<available-skills>` catalog of your OpenViking skills.
- `UserPromptSubmit` recalls and injects context for the current request, including your own skills and those shared with your account under `viking://agent/skills`.
- `PreToolUse` on TRAE and TRAE CN denies `Read`, `Glob`, and `Grep` calls whose path is a `viking://` URI and points to OpenViking MCP tools; a `Bash` or `RunCommand` command that carries a `viking://` URI still runs, with a notice suggesting those tools. TraeCode CLI 2.0 uses the Codex plugin, whose `PreToolUse` matches only `Bash`: it adds the same notice and never denies a call.
- `Stop` captures and immediately commits the completed turn, including short sessions.
- The OpenViking MCP server transparently exposes the full server MCP tool set (16 tools): `find`, `search`, `read`, `list`, `tree`, `remember`, `write`, `edit`, `add_resource`, `add_skill`, `list_watches`, `cancel_watch`, `grep`, `glob`, `forget`, and `health`. `search` with `mode="context"` returns assembled context.
The skill catalog lists your own skills before those shared with your account, with each description cut to about 40 tokens. Its budget, `skillCatalogTokenBudget` (default `1200` tokens), is separate from the profile budget; when not every description fits, the catalog lists names only. Set `skillCatalog` to `false` or the budget to `0` to turn it off, either in the `plugin` section of `~/.openviking/ovcli.conf` ([Plugin Settings](../configuration/02-client.md#plugin-settings)) or through `OPENVIKING_SKILL_CATALOG` and `OPENVIKING_SKILL_CATALOG_TOKEN_BUDGET`.

## Verify

1. Restart TRAE, TRAE CN, or TraeCode CLI 2.0 and create a new Agent session.
2. Confirm that `openviking` is connected in the client's MCP settings.
3. Ask about an existing project or preference and confirm that the answer uses stored memory.
4. Tell the Agent a temporary preference, wait for the response to finish, then create a new session and ask for it again to verify capture, commit, and cross-session recall.
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
