# DESIGN: the ZCode host

The ZCode adapter is the one host in this plugin whose extension surface had to be established by inspection rather than from documentation. What was verified, and what the adapter decided because of it, is recorded here.

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
| Hook payload identity | Every event carries `sessionId`/`session_id` and the prompt's `turnId`, shared by all events and model calls of that prompt (observed, CLI 0.18.0 in ZCode 4.0.0) |
| `PostToolUse` / `PostToolUseFailure` | `toolCallId`/`tool_use_id`, `toolName`, `toolInput`, `toolResponse` (the tool's raw object) or `error` (a string) — not registered: the rollout already holds every result (observed) |
| Rollout file | `~/.zcode/cli/rollout/model-io-<sessionId>.jsonl`, one `model_io` line per model call (observed) |

## Key design decisions

### 1. Assemble the shared runtime at install time (no vendored copy)

ZCode imports the shared runtime across the plugin boundary, the way Cursor and TRAE do. The installer copies the modules `lib/MANIFEST` names to `~/.openviking/agent-integrations/memory-plugin-shared/lib`, which is exactly where `../../memory-plugin-shared/lib` resolves from an installed `hosts/` or `scripts/` file — so the same relative path works in this repository and on a user's machine, and no generated copy has to be kept in git.

### 2. Config-file hooks (not plugin-manifest hooks)

`install_zcode()` writes hooks and MCP config into `~/.zcode/cli/config.json` (the config-file scope), not via plugin marketplace registration. This mirrors the Cursor/TRAE install pattern.

`__OPENVIKING_PLUGIN_ROOT__` in the source `hosts/zcode/hooks.json` is replaced by absolute paths at install time by `renderHookCommand()` in `install.sh`, so the config-file "no template expansion" limitation does not apply.

**Provenance**: Adversarial review R4 — config-file hooks require `hooks.enabled: true`; the merge script sets this automatically.

### 3. Four events only (ZCode-supported subset)

ZCode supports 7 events but NOT `PreCompact`/`SessionEnd`/`SubagentStart`/`SubagentStop`. The host wires 4 events. The commit-on-`Stop` strategy compensates for the absence of `PreCompact`/`SessionEnd`: a Stop commits whatever it and earlier Stops left uncommitted; the Stop parent detaches before reading stdin so network writes do not block ZCode.

**Provenance**: Adversarial review R1 — confirmed all 4 event names valid; R4 — unsupported events silently dropped.

### 4. Output schema: ZCode-canonical keys only

ZCode's strict JSON schema rejects unrecognized keys. The adapter's envelope emits ONLY `{ hookSpecificOutput: { hookEventName, additionalContext } }` for context injection, `{ hookSpecificOutput: { hookEventName: "PreToolUse", permissionDecision: "deny", permissionDecisionReason } }` when the URI guard denies a file tool, and `{ hookSpecificOutput: { hookEventName: "PreToolUse", additionalContext } }` when it notices a `viking://` URI in a shell command. No `decision: "approve"` (Claude-Code-ism).

The shared guard produces that notice for any shell tool, but the `PreToolUse` matcher names only `Read|Glob|Grep`, so ZCode never sends one. Whether its schema accepts `additionalContext` on `PreToolUse` is unverified; add a shell tool to the matcher only after checking that against a live session.

**Provenance**: Adversarial review R1-F1, R4-V1 — the #1 silent-failure mode.

### 5. No ZCode host plugin manifest

ZCode probes `.zcode-plugin/plugin.json`, then `.claude-plugin/plugin.json`, then `.codex-plugin/plugin.json` — but nothing here is registered with its plugin system. The installer writes hooks and MCP config straight into `~/.zcode/cli/config.json`, so the host-consumed manifest is `hosts/zcode/openviking.integration.json`, which the installer reads.

The root `plugin.json` is host-neutral package metadata. The installer copies it
with the integration, the doctor reads its version, and repository checks keep
that version aligned with each host's `openviking.integration.json`. No host
probes or loads it as a native plugin manifest.

### 6. Tool calls come from the rollout file

Each rollout line records one model call: `request.messages` (the conversation sent, marked `messagesKind` `full`, `tail` or `delta` with a `messageOffset`, so a line may hold only what is new) and `response` (`text` and `toolCalls: [{ id, name, input }]`). A tool's result is a later line's `{ role: "tool", content, toolCallId, toolName, isError }` message; the assistant message that made the call keeps it in `toolCalls`, and ZCode's own reminders and hook context are separate user messages. Lines whose `querySource` is not `main_turn` (compaction, subagents, web fetch and search, memory recall) and failed attempts (`error`, retried on a line of their own) are not the conversation.

`zcode-turns.mjs` therefore builds a turn from what each line of a `turnId` adds — the tool results answering calls made earlier in the turn, then the response's text and calls — and takes the prompt from the turn's first line: the newest user message with text left after the injected blocks are stripped, never one past an assistant or tool message. Every model call of one prompt shares its `turnId`, so the turn's calls, results and reply go out as one assistant message, in host order, next to the prompt. Since the rollout holds every result with its id, the `PostToolUse` hooks are not registered.

Observed on a live session: `full` lines only; `tail` and `delta` and the 64 MB file reset (`modelIOReset`) are read from the CLI bundle. After a reset the cursor's `turnId` is gone from the file, so the turns are read again and the dedup keys keep them from being sent twice.

## Primary unknowns

1. **Hook stdin field names**: The Stop hook carries `responseText`/`responsePreview`/`last_assistant_message` and `toolCallCount`, but not the prompt or the tool calls; the parser reads the rollout file and uses stdin only when that cannot be read.
2. **Output schema acceptance**: `UserPromptSubmit`'s `hookSpecificOutput.additionalContext` reaches the model, as a user message of its own (`<system-reminder>UserPromptSubmit hook additional context: …`) placed before the prompt (observed). `SessionStart` and `PreToolUse` output was not exercised live.
3. **MCP tool name format**: a server registered in `config.json` → `mcp.servers` as `echo` exposed its tool as `mcp__echo__echo_upper` (observed), so the OpenViking server's tools are `mcp__openviking__<tool>`.
4. **Turn identity**: Rollout entries carry a monotonic `turnId`. The rollout is the authoritative incremental source whenever it is readable; stdin is a compatibility fallback only. The adapter sends this identity as OpenViking's `turn_id`, records both role-specific dedup keys only after messages are sent or durably queued, and advances `lastTurnId` only through complete acknowledged rollout entries.

## Adversarial review incorporation

The focused regression suite covers rollout-first recovery, acknowledgement and cursor state, duplicate Stop delivery, detached slow writes, and installation from the same marketplace staging script used by the TOS release workflow.
