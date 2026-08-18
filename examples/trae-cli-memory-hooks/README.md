# OpenViking Memory Hooks for TRAE CLI

This directory provides the TRAE CLI lifecycle adapter for OpenViking memory:
three lifecycle hooks, a `PreToolUse` URI guard, and the `openviking-memory`
MCP server.

## Installation

Use the shared installer:

```bash
bash examples/memory-plugin-shared/install.sh --harness trae-cli
```

The installer:

- assembles the shared runtime under
  `$OPENVIKING_HOME/agent-integrations/memory-plugin-shared/lib`;
- installs this adapter under
  `$OPENVIKING_HOME/agent-integrations/trae-cli`;
- merges hooks into `${TRAECLI_HOME:-${TRAE_HOME:-~/.trae}/cli}/hooks.json`;
- registers `openviking-memory` in `${TRAE_HOME:-~/.trae}/traecli.toml`.

Uninstall with:

```bash
bash examples/memory-plugin-shared/install.sh --harness trae-cli --uninstall --yes
```

## Hook and MCP Surface

The draft registers four hook events:

| Event | Entry | Reuse assessment |
| --- | --- | --- |
| `SessionStart` | `scripts/session-start.mjs` | Reuses the shared thin-harness profile injection path. Requires TRAE CLI to provide a stable session id or equivalent cwd fallback. |
| `UserPromptSubmit` | `scripts/auto-recall.mjs` | Reuses the shared recall path. Requires TRAE CLI prompt input to be exposed as `prompt`, `user_prompt`, `message`, or `text`. |
| `Stop` | `scripts/auto-capture.mjs` | Reuses shared session append and commit helpers. Requires TRAE CLI stop input to expose the assistant response as `last_assistant_message`, `assistant_message`, `response`, `output`, or `text_content`. |
| `PreToolUse` | `scripts/uri-guard.mjs` | Follows the Codex hook output style for `permissionDecision: "deny"` and reuses the shared `agent-uri-guard` evaluator. |

TRAE CLI lifecycle hooks do not use TRAE / TRAE CN's `decision: "approve"`
output. No-op lifecycle hooks emit `{}`; context injection emits only
`hookSpecificOutput.hookEventName` plus `hookSpecificOutput.additionalContext`.
Tool-call allow/deny decisions belong to `PreToolUse` via
`hookSpecificOutput.permissionDecision`, and permission approval belongs to
`PermissionRequest` via `hookSpecificOutput.decision.behavior`.

The MCP server is named `openviking-memory`, matching the Codex memory plugin
name. The package keeps the same `.mcp.json` source shape as the other native
hook integrations; the shared installer writes the equivalent Node proxy entry
into TRAE CLI's configured `traecli.toml`:

```json
{
  "mcpServers": {
    "openviking-memory": {
      "command": "node",
      "args": ["servers/mcp-proxy.mjs"],
      "cwd": ".",
      "startup_timeout_sec": 30
    }
  }
}
```

## Runtime Boundary

This package follows the same source layout as `examples/trae-memory-hooks`:
the repository directory contains only TRAE CLI-specific adapters. The shared
runtime is assembled by the installer at install time.

- `examples/memory-plugin-shared/lib/agent-hook-runtime.mjs` handles profile
  injection, recall, capture, commit, session state, locking, credential
  loading, and pending retry replay.
- `examples/memory-plugin-shared/lib/mcp-proxy-core.mjs` handles the stdio to
  OpenViking `/mcp` proxy.
- `examples/memory-plugin-shared/lib/agent-uri-guard.mjs` handles `PreToolUse`
  blocking when local file or shell tools receive `viking://` virtual paths.

The source package intentionally does not carry a vendored `lib/` directory.
The installer should copy `examples/trae-cli-memory-hooks` into
`$OV_HOME/agent-integrations/trae-cli` and assemble the shared runtime into
`$OV_HOME/agent-integrations/memory-plugin-shared/lib`, matching the existing
TRAE installation model.

## What Is Not Reused From Codex

- Codex plugin marketplace metadata and install commands.
- Codex-specific `${PLUGIN_ROOT}` substitution.
- Codex-specific `PreCompact` commit flow.
- Codex transcript JSONL parsing and `cx-<session_id>` session prefix.
- Codex local compressor startup detection.

## Current Compatibility Notes

The three hook entries are intended to be reusable if TRAE CLI sends hook input
JSON close to the existing thin harness conventions:

- Session identity: `conversation_id`, `session_id`, `sessionId`, or
  `generation_id`.
- Workspace: `cwd`, `workspace_roots`, or `workspaceRoots`.
- User prompt: `prompt`, `user_prompt`, `userPrompt`, `message`, or `text`.
- Assistant response on stop: `last_assistant_message`,
  `lastAssistantMessage`, `assistant_message`, `assistantMessage`, `response`,
  `output`, or `text_content`.
- Tool call: `tool_name`, `toolName`, `name`, or `tool`; input under
  `tool_input`, `toolInput`, `input`, or `arguments`.

The three lifecycle wrappers enter `scripts/trae-cli-hook.mjs`, a CLI-only
adapter that uses the fixed `trae-cli` client id and `trcli-` OpenViking
session prefix. It intentionally does not carry the TRAE / TRAE CN `tr-` and
`trcn-` branches.

If TRAE CLI uses different field names, adapt `scripts/trae-cli-hook.mjs` or
the local text cleanup / turn parsing in `scripts/trae-cli-turns.mjs`. The
shared OpenViking runtime and MCP proxy can remain unchanged.

## User-Level Install Shape

An installer should render `hooks/hooks.json` by replacing
`__OPENVIKING_TRAE_CLI_ROOT__` with this directory's absolute path, then merge
the rendered hooks into the current `TRAECLI_HOME/hooks.json`. In the common
local setup this is:

```text
~/.trae/cli/hooks.json
```

TRAE CLI also supports hooks in the active `traecli.toml` under `[hooks]`, but
the source shown by the TUI `/hooks` command is the source of truth. Prefer the
user-level hooks file when installing this integration so the setup is not tied
to one workspace.

MCP should be added to the active `traecli.toml` under
`[mcp_servers."openviking-memory"]`. Project-level MCP files such as
`<workspace>/.trae/.mcp.json` or `<workspace>/.trae/mcp.json` are supported by
TRAE CLI, but they are not the recommended target for this integration. Confirm
the effective MCP source with `/mcp` or `traecli mcp list`.

If an existing OpenViking TRAE hook set is already installed, replace or disable
the old OpenViking entries rather than adding this draft beside them. Running
both will duplicate recall and capture.

The installer validates the installed hook entrypoints and MCP configuration.
Use `/hooks`, `/mcp`, or `traecli mcp list` to inspect the effective runtime
configuration.
