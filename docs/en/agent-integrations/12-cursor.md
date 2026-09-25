# Cursor Memory Integration

Give Cursor long-term memory across projects and sessions. After installation, OpenViking Hooks inject relevant context at session start and before each request, then capture new conversation turns after the response. MCP is available for explicit memory search, reading, and management.

## Install

Prerequisites: macOS or Linux, Node.js 18+, and preferably the latest stable Cursor release. The installer guides you through the OpenViking connection settings.

When prompted for the connection, Volcengine Cloud users should select **Volcengine OpenViking Cloud** and enter their API key. Select **Self-hosted / local** for a server on this machine (`http://127.0.0.1:1933`); for a remote self-hosted server, select **Custom URL / keep current** and enter its URL.

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness cursor
```

If GitHub is unavailable, use the TOS mirror:

```bash
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness cursor --dist tos
```

Quit Cursor completely and restart it after installation.

## What gets installed

- Lifecycle Hooks for profile loading, prompt recall, conversation capture, session commit, and `viking://` URI protection.
- The OpenViking MCP server with tools such as `search`, `read`, `remember`, and `add_skill`; `search` with `mode="context"` returns assembled context.
- An always-on Rule and the `openviking-memory` Skill, which tell the Agent how to use injected context and memory tools, plus the `openviking-skills` Skill for finding, using, creating (`add_skill`), sharing, and migrating skills stored in OpenViking.

## Verify

1. Restart Cursor and create a new Agent session.
2. Open **Cursor Settings → Hooks** and confirm that the OpenViking lifecycle Hooks execute `scripts/hook.mjs` and its URI protection Hook executes `scripts/uri-guard.mjs`.
3. Check that the `beforeSubmitPrompt` output contains `additional_context`. This confirms that recall reaches the Agent without requiring an MCP call first.
4. Open **Cursor Settings → Tools & MCPs** and confirm that `openviking` is connected.
5. Tell Cursor a test preference, end the session normally, and confirm capture and commit in the Hook log. After memory extraction completes, start a new session in the same workspace and ask about it.

## How it works

- `sessionStart` loads your profile, the current project's memory index, and an `<available-skills>` catalog of your OpenViking skills.
- `beforeSubmitPrompt` recalls context for the current request, including your own skills and those shared with your account under `viking://agent/skills`, and injects it through `additional_context`.
- `beforeReadFile` denies reading a `viking://` path as a local file and points the Agent to OpenViking MCP tools. Shell commands are not checked.
- `stop` incrementally captures new user and assistant messages.
- `preCompact` and `sessionEnd` commit pending messages for memory extraction.

The skill catalog lists your own skills first, then those shared with your account, leaving out a shared skill whose name you also own; each description is cut to about 40 tokens. It has its own token budget, `skillCatalogTokenBudget` (default `1200`), separate from the profile budget. When not every description fits, the catalog lists names only (with a `... +N more` tail if even the names do not all fit), and when not even one name fits, it shrinks to a one-line skill count. Set `skillCatalog` to `false` or the budget to `0` to turn it off, either in the `plugin` or `plugin.cursor` section of `~/.openviking/ovcli.conf` ([Plugin Settings](../configuration/02-client.md#plugin-settings)) or through `OPENVIKING_SKILL_CATALOG` and `OPENVIKING_SKILL_CATALOG_TOKEN_BUDGET`. Without any skills, or on a server without `GET /api/v1/skills`, the catalog is left out.

Project identity uses Cursor's `workspace_roots`, keeping workspace peers separate. Hooks and MCP share credentials from `~/.openviking/ovcli.conf`.

## Upgrade and uninstall

Re-run the install command from the same distribution channel to upgrade. Use the same channel for uninstall:

```bash
# GitHub
bash <(curl -fsSL https://raw.githubusercontent.com/volcengine/OpenViking/main/examples/memory-plugin-shared/install.sh) \
  --harness cursor --uninstall --yes

# TOS
bash <(curl -fsSL https://ovrelease.tos-cn-beijing.volces.com/memory-plugin-shared/install.sh) \
  --harness cursor --uninstall --yes
```

Uninstall removes only OpenViking-managed Cursor Hooks, MCP, Rule, Skills, and runtime files. Other configuration is preserved.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| Hooks do not run | Quit Cursor completely, restart it, and create a new Agent session. |
| Recall appears in Hook output but not in the answer | Upgrade to the latest stable Cursor; older releases may not support `beforeSubmitPrompt.additional_context`. |
| The same event runs multiple OpenViking Hooks | Cursor may be importing an older Claude Code plugin. Upgrade or remove the legacy OpenViking plugin ids reported by the installer, then restart Cursor. |
| MCP does not connect | Check the URL/API key in `~/.openviking/ovcli.conf`, then restart Cursor. |
| Detailed diagnostics are needed | Start Cursor with `OPENVIKING_DEBUG=1` and inspect `~/.openviking/logs/cursor-hooks.log`. |

## See also

- [Capability Reference](./16-capability-reference.md)
- [Authentication](../guides/04-authentication.md)
- [Cursor Hooks documentation](https://cursor.com/docs/hooks)
