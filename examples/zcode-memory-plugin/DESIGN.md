# DESIGN: ZCode Memory Plugin

## Verified ZCode extension surface

These facts were verified against a live ZCode installation (built-in `zcode-guide` plugin docs + actual `~/.zcode/cli/config.json` + real installed plugins with hooks).

| Aspect | Verified fact |
|--------|--------------|
| Supported hook events | `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PostToolUseFailure`, `Stop` (exactly 7) |
| Unsupported events | `PreCompact`, `SessionEnd`, `Notification`, `SubagentStart`, `SubagentStop` |
| Manifest probe order | `.zcode-plugin/plugin.json` → `.claude-plugin/plugin.json` → `.codex-plugin/plugin.json` |
| Template vars (plugin hooks) | `${CLAUDE_PLUGIN_ROOT}`, `${ZCODE_PLUGIN_ROOT}`, `${CLAUDE_PROJECT_DIR}`, `${ZCODE_PROJECT_DIR}`, `${CLAUDE_SESSION_ID}` |
| Template vars (config hooks) | None — config-file hooks do NOT expand templates |
| Hook output schema | Strict JSON — any extra key fails validation, output discarded |
| MCP config location | `~/.zcode/cli/config.json` → `mcp.servers` (user scope) |
| Plugin MCP namespacing | `plugin:<plugin>:<server>` |
| MCP auto-connect | All scopes auto-connect at session start |
| Hook runner enablement | Auto-enabled when any plugin contributes a hook |
| Timeout units | `command` type: `timeout` in **seconds**; `process` type: `timeoutMs` in milliseconds |
| `async` field | No runtime effect — hooks always run inline |

## Key design decisions

### 1. Vendor shared runtime (not relative path)

Unlike TRAE/Cursor (which import shared lib via cross-directory relative paths), the ZCode plugin **vendors** the shared runtime into `scripts/shared/` — the same pattern as Claude Code and Codex. This makes the plugin self-contained and relocatable.

**Provenance**: Adversarial review B1 — vendor and relative-path are mutually inconsistent; pure vendor was chosen because ZCode's config-driven install model copies files to `~/.openviking/agent-integrations/`.

### 2. Config-file hooks (not plugin-manifest hooks)

`install_zcode()` writes hooks and MCP config into `~/.zcode/cli/config.json` (the config-file scope), not via plugin marketplace registration. This mirrors the Cursor/TRAE install pattern.

`${ZCODE_PLUGIN_ROOT}` in the source `hooks/hooks.json` is replaced by absolute paths at install time by `renderHookCommand()` in `install.sh`, so the config-file "no template expansion" limitation does not apply.

**Provenance**: Adversarial review R4 — config-file hooks require `hooks.enabled: true`; the merge script sets this automatically.

### 3. Four events only (ZCode-supported subset)

ZCode supports 7 events but NOT `PreCompact`/`SessionEnd`/`SubagentStart`/`SubagentStop`. The plugin wires 4 events. The commit-on-`Stop` strategy compensates for the absence of `PreCompact`/`SessionEnd`; the Stop parent detaches before reading stdin so network writes do not block ZCode.

**Provenance**: Adversarial review R1 — confirmed all 4 event names valid; R4 — unsupported events silently dropped.

### 4. Output schema: ZCode-canonical keys only

ZCode's strict JSON schema rejects unrecognized keys. The dispatcher emits ONLY `{ hookSpecificOutput: { hookEventName, additionalContext } }` for context injection, and `{ hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason } }` for URI guard. No `decision: "approve"` (Claude-Code-ism).

**Provenance**: Adversarial review R1-F1, R4-V1 — the #1 silent-failure mode.

### 5. `.zcode-plugin/plugin.json` (preferred manifest form)

**Provenance**: Adversarial review R1 — `.claude-plugin/` works but `.zcode-plugin/` is the documented preferred location.

## Primary unknowns

1. **Hook stdin field names**: Verified via ZCode source reverse-engineering (#3127 by @quinn-zenith). The Stop hook exposes `responseText`/`responsePreview` for assistant content. User content is NOT in stdin — the parser falls back to ZCode's rollout file (`~/.zcode/cli/rollout/model-io-<sessionId>.jsonl`) which contains the complete conversation per line: `{ sessionId, turnId, request: { messages: [...] }, response: { text } }`.
2. **Output schema acceptance**: Whether `hookSpecificOutput` wrapper is accepted as-is. Must be tested against a live ZCode session.
3. **MCP tool name format**: Namespaced as `plugin:openviking:openviking` — verify tool names match expectations.
4. **Turn identity**: Rollout entries carry a monotonic `turnId`. The rollout is the authoritative incremental source whenever it is readable; stdin is a compatibility fallback only. The adapter sends this identity as OpenViking's `turn_id`, records both role-specific dedup keys only after messages are sent or durably queued, and advances `lastTurnId` only through complete acknowledged rollout entries.

## Adversarial review incorporation

The focused regression suite covers rollout-first recovery, acknowledgement and cursor state, duplicate Stop delivery, detached slow writes, and installation from the same marketplace staging script used by the TOS release workflow.
