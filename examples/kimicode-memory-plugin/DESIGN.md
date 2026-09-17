# DESIGN: Kimi Code CLI Memory Plugin

Shared runtime modules are generated from the canonical `../memory-plugin-shared/lib/` source by `sync.mjs`; this directory is directly installable as a native Kimi plugin, and only its manifest, lifecycle wiring, and transcript adapter are Kimi-specific.

## Verified Kimi Code extension surface

Facts checked against Kimi Code CLI **0.43.1** (2026-09-17) and the official Kimi Code extension documentation.

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

The shared installer writes the native plugin to `$KIMI_CODE_HOME/plugins/managed/openviking-memory` and records it in `$KIMI_CODE_HOME/plugins/installed.json`. Kimi Code then reads `kimi.plugin.json` and owns the hook/MCP lifecycle; the user's `config.toml` and `mcp.json` remain unchanged. The interactive `/plugins install <path>` flow is the supported manual alternative.

## Adversarial checks

- Recall must never emit ZCode/Claude JSON wrappers — Kimi Code would inject the JSON as user-visible context.
- Empty `matcher` is omitted (Kimi Code matcher is optional regex).
- Uninstall removes the OpenViking record from `installed.json`; `--purge` also removes the exact managed copy created by this installer.
- Wire cursor uses host `turnId`; missing wire falls back to stdin + pendingPrompt.
