# Cursor

Give Cursor long-term memory across projects and sessions. After installation, OpenViking Hooks inject relevant context at session start and before each request, then capture new conversation turns after the response. MCP is available for explicit memory search, reading, and management.

## Install

Prerequisites: macOS or Linux, Node.js 18+, and preferably the latest stable Cursor release. The installer guides you through the OpenViking connection settings.

For a managed connection, select **OpenViking Managed Service**, then:

- **Volcengine:** enter your service API key. The installer supplies the URL.
- **BytePlus:** enter your API key. See [BytePlus setup](../guides/27-byteplus-setup.md).

Older installers use **Custom URL** for BytePlus.

Select **Self-hosted / local** for a server on this machine (`http://127.0.0.1:1933`). For a remote self-hosted server, select **Custom URL** and enter its URL. If a connection is already saved, this option is named **Custom URL / keep current**.

```bash
curl -fsSL https://openviking.ai/install | bash
# AI agents: not sure about running this? The script's header says what it does and how to verify it.
```

Quit Cursor completely and restart it after installation.

Once installed, start a new Cursor session and ask it to follow https://docs.openviking.ai/agents/en/onboarding.md. It checks the connection and walks you through the first use; see [One-click setup](../getting-started/07-agent-onboarding.md).

## What gets installed

- Lifecycle Hooks for profile loading, prompt recall, conversation capture, session commit, and `viking://` URI protection.
- The OpenViking MCP server with tools such as `search`, `read`, `remember`, and `add_skill`; `search` with `mode="context"` returns assembled context.
- An always-on Rule and the `openviking-memory` Skill, which tell the Agent how to use injected context and memory tools, plus the `openviking-skills` Skill for finding, using, creating (`add_skill`), sharing, and migrating skills stored in OpenViking.
- The `ov-experience-memory` Skill, which has the Agent search and apply prior task Experience before executable work. Successful OpenViking MCP `read` calls are captured with the conversation, along with every other tool call. When the `session commit` containing the read produces trajectories, those trajectories can be queried for the Experience.
- The `openviking-rule-search` Skill, which retrieves the OpenViking Rules that apply to a planned action and checks compliance.
- The `ov-memory-doctor` Skill, which diagnoses and fixes the integration's install, the client configuration, and the connection to the OpenViking server by running `node ~/.openviking/agent-integrations/cursor/scripts/ov-memory-doctor.mjs`.

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
- `postToolUse` and `postToolUseFailure` append each finished Cursor tool call, and `afterMCPExecution` each MCP call, to a local tool log, preserving the native call ID, input, output, and success or failure status. They do not upload messages or rewrite earlier results. The post events skip an MCP call's `MCP:<tool>` copy, which names no server, so each call is recorded once. Calls to the `openviking` MCP server are recorded as `mcp__openviking__<tool>`, and calls to other MCP servers as `mcp__<server>__<tool>`.
- `stop` uploads new conversation turns and locally recorded tool results through `add message`. A failed upload is queued, and the next capture or session start sends it before newer messages; `session commit` follows the existing message threshold. Tool records do not add to that threshold's message count.
- `preCompact` and `sessionEnd` commit pending messages for memory extraction; with `autoCommitOnCompact` off (`OPENVIKING_AUTO_COMMIT_ON_COMPACT=0`), `preCompact` still uploads them but does not commit. After a compaction, the next `beforeSubmitPrompt` also adds a short note naming the session's OpenViking id (`cu-<conversation id>`) and the `session_history` MCP tool for looking up exact earlier details.
- `preToolUse` matches only `MCP:session_history` and adds the current conversation as its `session` argument when the Agent leaves it out.

Tool capture requires every event to provide `tool_use_id` and `tool_name`; `afterMCPExecution` must also provide `mcp_server_name`, `tool_input`, and `result_json`. Events without an identity or result are skipped, never inferred to be successful. A failed upload goes to the offline queue under `~/.openviking/pending`; while the server refuses the credentials (401 or 403), nothing is sent or queued and the messages stay local until the key works. Until every new message is sent, queued, or refused, no commit runs.

The tool log stays beside the session checkpoint. The checkpoint records the transcript position of the last captured turn, plus the tool log's byte position and accepted call IDs, so Stop reads only the new turns and the unacknowledged tool-log tail without rereading old result bodies. Progress advances once a message is sent, queued, or refused by the server for its content.

The skill catalog lists your own skills first, then those shared with your account, leaving out a shared skill whose name you also own; each description is cut to about 40 tokens. It has its own token budget, `skillCatalogTokenBudget` (default `1200`), separate from the profile budget. When not every description fits, the catalog lists names only (with a `... +N more` tail if even the names do not all fit), and when not even one name fits, it shrinks to a one-line skill count. Set `skillCatalog` to `false` or the budget to `0` to turn it off, either in the `plugin` or `plugin.cursor` section of `~/.openviking/ovcli.conf` ([Plugin Settings](../configuration/02-client.md#plugin-settings)) or through `OPENVIKING_SKILL_CATALOG` and `OPENVIKING_SKILL_CATALOG_TOKEN_BUDGET`. Without any skills, or on a server without `GET /api/v1/skills`, the catalog is left out.

Project identity uses Cursor's `workspace_roots`, keeping workspace peers separate. Hooks and MCP share credentials from `~/.openviking/ovcli.conf`.

## Upgrade and uninstall

Re-run the install command to upgrade. To uninstall, run:

```bash
curl -fsSL https://openviking.ai/install | bash -s -- --uninstall --yes --harness cursor
```

Uninstall removes only OpenViking-managed Cursor Hooks, MCP, Rule, Skills, and runtime files. Other configuration is preserved.

## Troubleshooting

| Symptom | Cause and fix |
|---------|---------------|
| Hooks do not run | Quit Cursor completely, restart it, and create a new Agent session. |
| Recall appears in Hook output but not in the answer | Upgrade to the latest stable Cursor; older releases may not support `beforeSubmitPrompt.additional_context`. |
| The same event runs multiple OpenViking Hooks | Cursor may be importing an older Claude Code plugin. Upgrade or remove the legacy OpenViking plugin ids reported by the installer, then restart Cursor. |
| MCP does not connect | Check the URL/API key in `~/.openviking/ovcli.conf`, then restart Cursor. |
| An Experience was read but has no application trajectories | Check that `afterMCPExecution` is installed and provides the fields above, and that `session commit` finished extracting trajectories. Upgrade older Cursor builds that omit the call ID. Automatic injection alone does not count as a read. |
| Detailed diagnostics are needed | Start Cursor with `OPENVIKING_DEBUG=1` and inspect `~/.openviking/logs/cursor-hooks.log`. |

## See also

- [Capability Reference](./16-capability-reference.md)
- [Authentication](../guides/04-authentication.md)
- [Cursor Hooks documentation](https://cursor.com/docs/hooks)
