# DESIGN: Kimi Code CLI Memory Plugin

Template: [examples/zcode-memory-plugin](../zcode-memory-plugin) ([PR #3678](https://github.com/volcengine/OpenViking/pull/3678)). Shared runtime is vendored because this directory is directly installable as a native Kimi plugin; only the host adapter is new.

## Verified Kimi Code extension surface

Facts checked against Kimi Code CLI **0.41.0** (2026-09-04), official docs (`hooks.html`, `mcp.html`, `plugins.html`, `config-files.html`), and a live `~/.kimi-code` install.

| Aspect | Kimi Code CLI | ZCode (do not copy) |
|--------|---------------|---------------------|
| Plugin manifest | `kimi.plugin.json` (Kimi owns install and lifecycle) | `.zcode-plugin/plugin.json` |
| Hook rules | `[[hooks]]` array: only `event`, `matcher`, `command`, `timeout` | `hooks.events` JSON tree |
| MCP | `mcpServers` in `kimi.plugin.json` | `mcp.servers` inside config.json |
| Hook stdin | snake_case (`session_id`, `hook_event_name`, `cwd`, `tool_name`, `tool_input`) | camelCase / mixed |
| UserPromptSubmit output | **plain stdout text** is appended to context | strict JSON `hookSpecificOutput.additionalContext` |
| SessionStart / SessionEnd / PreCompact / Interrupt | observation-only (return values ignored) | SessionStart can inject; no SessionEnd / PreCompact |
| Stop | blockable; we pass through | blockable; pass through |
| PreToolUse deny | JSON `{hookSpecificOutput:{permissionDecision:"deny"}}` or exit 2 | JSON including `hookEventName` |
| Extra events | `SessionEnd`, `PreCompact`, `Interrupt`, `SubagentStart`/`Stop` | not present |
| Transcript | `session_index.jsonl` → `agents/main/wire.jsonl` | `~/.zcode/cli/rollout/model-io-*.jsonl` |
| Identical `command` strings | de-duplicated (run once) | n/a |

## Lifecycle mapping

| Host event | OpenViking action |
|------------|-------------------|
| `SessionStart` | Replay pending queue. **Do not print** — observation-only, cannot inject. |
| `UserPromptSubmit` | Inject profile once + recall as **plain text**. Stash `pendingPrompt`. |
| `PreToolUse` `Read\|Glob\|Grep` | Deny `viking://` reads; redirect to MCP tools. |
| `Stop` | Detached capture + commit (wire.jsonl cursor). |
| `PreCompact` | Same capture (ZCode has no compact event). |
| `SessionEnd` | Same capture (ZCode has no session-end event). |
| `Interrupt` | Capture synchronously (fires *instead of* Stop; no detach). |

Session ids are derived with the `kc-` prefix.

## Why not only `/plugins install`

The shared installer invokes `kimi plugin install` for this native plugin. Kimi Code reads `kimi.plugin.json`, owns the installed copy and hook/MCP lifecycle, and leaves the user's `config.toml` and `mcp.json` unchanged. The interactive `/plugins install <path>` flow is equivalent.

## Adversarial checks

- Recall must never emit ZCode/Claude JSON wrappers — Kimi Code would inject the JSON as user-visible context.
- Empty `matcher` is omitted (Kimi Code matcher is optional regex).
- Uninstall removes only the OpenViking comment-delimited block.
- Wire cursor uses host `turnId`; missing wire falls back to stdin + pendingPrompt.
