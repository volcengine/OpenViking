# Spec: ZCode Memory Plugin for OpenViking

> **Status**: Draft — pending local verification on a live ZCode install.
> **Related**: Closes [#3127](https://github.com/volcengine/OpenViking/issues/3127), [#3442](https://github.com/volcengine/OpenViking/issues/3442); supersedes draft PR [#3544](https://github.com/volcengine/OpenViking/pull/3544) with verified ZCode extension-surface facts.
> **Authored against**: upstream `main` @ `c4d2b27c` (2026-07-31).

---

## Problem Statement

ZCode is an AI coding agent (the one shipped with the GLM model family) that has no OpenViking memory integration. Users who run OpenViking as their context database get automatic recall and capture in Claude Code, Codex, Cursor, TRAE, OpenCode, and pi — but not in ZCode. Every ZCode conversation starts with zero memory, and every session's content evaporates when the session ends. A user who switches between ZCode and Claude Code has a fractured memory: half their context lives in OpenViking, half doesn't.

## Solution

A `zcode-memory-plugin` under `examples/` that reuses the existing `memory-plugin-shared` runtime (no memory logic is duplicated). The plugin is a thin adapter that wires four ZCode lifecycle events (`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `Stop`) into the shared recall/capture/commit pipeline. It ships as a self-contained plugin directory discoverable by ZCode's plugin marketplace, and is installable via the shared `install.sh` one-liner.

## User Stories

### Installation & lifecycle

1. As a ZCode user, I want to install the OpenViking memory plugin with a single command, so that I don't have to manually edit configuration files.
2. As a ZCode user, I want the installer to detect whether ZCode is installed on my machine, so that the `--harness zcode` option only appears when relevant.
3. As a ZCode user, I want the plugin to be distributed via ZCode's plugin marketplace, so that I can install and update it through the standard mechanism.
4. As a ZCode user, I want the plugin to connect to my local OpenViking server automatically (reading `~/.openviking/ovcli.conf`), so that I don't have to configure credentials manually for a local setup.
5. As a ZCode user who also uses Claude Code or Codex, I want the same OpenViking server and data store to serve all my agents, so that memory is shared across tools — not siloed per agent.

### Recall (reading memory)

6. As a ZCode user, when I start a new session, I want the plugin to inject my user profile and relevant preferences/entities into the context, so that the agent knows my preferences from prior sessions.
7. As a ZCode user, when I type a prompt, I want the plugin to search OpenViking for relevant memories and inject them as context, so that the agent can recall facts from past conversations.
8. As a ZCode user, I want recall to be fast (under 8 seconds), so that it doesn't noticeably delay my prompt processing.
9. As a ZCode user, I want recall to degrade gracefully when the OpenViking server is offline, so that my session isn't blocked by a memory system that's down.
10. As a ZCode user, I want recall to skip empty or too-short prompts, so that trivial inputs don't waste server resources.

### Capture (writing memory)

11. As a ZCode user, when a conversation turn completes (Stop event), I want the plugin to capture the user and assistant messages into an OpenViking session, so that the content is available for future recall.
12. As a ZCode user, I want capture to happen asynchronously (without blocking the session), so that I don't experience latency from memory writes.
13. As a ZCode user, I want the plugin to avoid re-capturing turns that were already captured (deduplication), so that the memory store doesn't fill with duplicates.
14. As a ZCode user, I want the plugin to commit the OpenViking session periodically (when enough turns accumulate), so that the memory extractor can produce archived long-term memories.
15. As a ZCode user, I want capture to enqueue writes to a persistent pending queue when the server is unreachable, so that no conversation content is lost during outages.
16. As a ZCode user, I want the plugin to strip its own injected `<openviking-context>` blocks from captured text, so that recall output doesn't pollute the memory store in a self-referential loop.

### URI guard (safety)

17. As a ZCode user, when the agent attempts to `Read`, `Glob`, or `Grep` a `viking://` URI directly, I want the plugin to deny the operation and redirect the agent to the OpenViking MCP tools, so that virtual filesystem paths are never passed to local file/shell tools.
18. As a ZCode user, I want the deny message to include the correct MCP tool invocation example, so that the agent knows exactly how to access the content properly.

### MCP tools

19. As a ZCode user, I want the OpenViking MCP server to connect automatically at session start, so that I can use `find`, `search`, `recall`, `read`, `remember` tools without manual setup.
20. As a ZCode user, I want to see the MCP server status (connected, tool count > 0) in Settings → MCP, so that I can verify the integration is healthy.
21. As a ZCode user, I want the MCP tool names to follow the `mcp__plugin:openviking:openviking__*` namespace (ZCode's plugin MCP naming), so that tool calls resolve correctly.

### Configuration & debugging

22. As a ZCode user, I want to enable debug logging via `ov.conf` (`claude_code.debug: true` or an equivalent zcode section), so that I can troubleshoot hook behavior without relying on shell environment variables that don't propagate to desktop-spawned processes.
23. As a ZCode user, I want the debug log to be written to `~/.openviking/logs/zcode-hooks.log`, so that I can find ZCode-specific hook traces separate from other agents.
24. As a ZCode user, I want to be able to disable the plugin via environment variable (`OPENVIKING_MEMORY_ENABLED=0`) or config, so that I can temporarily turn off memory without uninstalling.
25. As a ZCode user, I want to bypass memory for specific sessions via glob patterns (`OPENVIKING_BYPASS_SESSION_PATTERNS`), so that scratch or sensitive sessions don't contaminate the shared store.

## Implementation Decisions

### Architecture: thin adapter over shared runtime

The ZCode adapter mirrors the TRAE plugin's architecture (the closest analogue — another IDE-style coding agent without `PreCompact`/`SessionEnd` events). A single dispatcher script (`zcode-hook.mjs`) branches on event name; four thin shim scripts (3 lines each) set an environment variable and import the dispatcher. All memory logic (recall, capture, commit, dedup, pending queue, credential resolution, MCP proxy) is provided by the shared runtime — zero duplication.

### Shared runtime reference: vendored copy (not relative path)

Unlike TRAE/Cursor (which import the shared lib via cross-directory relative paths resolved at install time by `assemble_agent_integration()`), the ZCode plugin **vendors** the shared runtime into `scripts/shared/` — the same pattern used by Claude Code and Codex. Rationale:

- ZCode plugins are self-contained directories distributed via marketplace; cross-directory relative imports are not guaranteed to resolve after installation.
- The vendor mechanism (`examples/memory-plugin-shared/sync.mjs`) is mature and already used by four consumers.
- A vendored copy makes the plugin relocatable and marketplace-distributable.

The `TARGETS` array in `sync.mjs` gains one entry:
```
{ dir: join(ROOT, "examples", "zcode-memory-plugin", "scripts", "shared"), files: OPENCODE_SHARED_FILES }
```

The dispatcher imports from the vendored path (`./shared/agent-hook-runtime.mjs`), not the source path (`../../memory-plugin-shared/lib/...`).

> **Decision provenance**: Adversarial review finding B1 — the original plan mixed vendor + TRAE-style relative imports, which are mutually inconsistent. Pure vendor (Claude Code pattern) was chosen because ZCode's marketplace distribution model requires self-contained plugin directories.

### Manifest: `.zcode-plugin/plugin.json` (preferred form)

ZCode probes manifest locations in order: `.zcode-plugin/` (preferred) → `.claude-plugin/` → `.codex-plugin/`. Although `.claude-plugin/` is recognized (confirmed by the OMC plugin running under ZCode), the plugin uses `.zcode-plugin/plugin.json` as the first-class ZCode citizen form. The manifest is minimal: `name`, `version`, `description`, and `mcpServers` pointing to `./.mcp.json`.

> **Decision provenance**: Adversarial review R1 — `.claude-plugin/` works but is a compatibility name that may be deprecated; `.zcode-plugin/` is the documented preferred location.

### Hook events: exactly four (the ZCode-supported subset)

ZCode supports exactly seven lifecycle events: `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PermissionRequest`, `PostToolUse`, `PostToolUseFailure`, `Stop`. It does **not** support `PreCompact`, `SessionEnd`, `SubagentStart`, or `SubagentStop`. The plugin wires four events:

| Event | Script | Purpose |
|-------|--------|---------|
| `SessionStart` | `session-start.mjs` | Inject user profile + preferences/entities; replay pending queue |
| `UserPromptSubmit` | `auto-recall.mjs` | Search OpenViking, inject `<openviking-context>` recall block |
| `PreToolUse` (matcher `Read\|Glob\|Grep`) | `uri-guard.mjs` | Deny direct access to `viking://` URIs, redirect to MCP tools |
| `Stop` | `auto-capture.mjs` | Capture incremental turns + commit session |

The commit-on-`Stop` strategy (committing after every capture, not waiting for `PreCompact`/`SessionEnd`) mirrors the TRAE and Codex patterns. This is necessary because ZCode offers no compact/end-of-session lifecycle signal.

> **Decision provenance**: Adversarial review R1 — confirmed all four event names are valid; R4 — confirmed `PreCompact`/`SessionEnd`/`SubagentStart`/`SubagentStop` are silently dropped by ZCode, so including them creates dead hooks (as seen in the OMC plugin's `hooks.json`).

### Hook output schema: ZCode-canonical keys only

ZCode parses hook stdout as JSON with a **strict schema — any extra key fails validation and the entire output is silently discarded**. The Claude Code plugin emits `{ "decision": "approve", "hookSpecificOutput": { ... } }`, which contains Claude-specific keys. The ZCode adapter must emit only ZCode-recognized output:

- **Context injection** (`SessionStart`, `UserPromptSubmit`): `{ "hookSpecificOutput": { "hookEventName": "<Event>", "additionalContext": "..." } }` — or verify whether ZCode accepts a top-level `additionalContext` without the `decision` wrapper. This is the single highest-risk unknown and must be resolved by manual testing before any live verification.
- **URI guard deny** (`PreToolUse`): `{ "hookSpecificOutput": { "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "..." } }` — ZCode documents `allow`/`ask`/`deny` as valid permission decisions.
- **Pass-through** (all events when no action needed): empty output + exit code 0.

The `approve()` helper in the dispatcher must NOT emit `decision: "approve"` (a Claude-Code-ism). If ZCode requires a decision field, use `exit 0` with no JSON output as the pass-through path.

> **Decision provenance**: Adversarial review R1 (F1) and R4 (V1) — the #1 silent-failure mode. A hook that "fires successfully" and logs `injection_built` but whose output is discarded by strict validation produces zero visible effect.

### Template variables: `${CLAUDE_PLUGIN_ROOT}` (braced form)

ZCode expands `${CLAUDE_PLUGIN_ROOT}` and `${ZCODE_PLUGIN_ROOT}` for plugin hooks (confirmed by docs and by real plugins: OMC, android-emulator, ios-simulator). The braced form `${CLAUDE_PLUGIN_ROOT}` is used (not the bare `$CLAUDE_PLUGIN_ROOT`, which breaks on paths with spaces). Template expansion is plugin-only — configuration-file hooks do not expand templates, so this is only safe in the plugin's `hooks/hooks.json`.

### MCP: single `.mcp.json`, no manifest-inline duplication

The plugin provides a single `.mcp.json` at the plugin root (referenced from the manifest via `"mcpServers": "./.mcp.json"`). The manifest does NOT inline an `mcpServers` object — only one source of truth, to avoid the precedence ambiguity seen in the android-emulator plugin (which ships both and they disagree). The MCP server is namespaced by ZCode as `plugin:openviking:openviking`, producing tool names like `mcp__plugin:openviking:openviking__find`.

### Timeout units: seconds (not milliseconds)

For `type: "command"` hooks, ZCode's `timeout` field is in **seconds** (not ms). The plan uses conservative values: `SessionStart: 30s`, `UserPromptSubmit: 20s`, `PreToolUse: 5s`, `Stop: 30s`. These match TRAE's timeouts. No `async: true` field is used (it has no runtime effect in ZCode — hooks always run inline).

> **Decision provenance**: Adversarial review R1 (F2, F10) — timeout unit confusion and useless `async` are common pitfalls.

### Session ID derivation

The adapter derives a stable OpenViking session ID from the ZCode session identifier, prefixed `zc-` (analogous to TRAE's `tr-` and Codex's prefix). The shared runtime's `deriveAgentSessionId("zc-", input)` handles this. The native session ID is resolved from whichever field ZCode provides (`session_id`, `conversation_id`, `generation_id`, or derived from `transcript_path`).

### Debug logging path

The adapter configures the logger with `clientId: "zcode"`, which writes to `~/.openviking/logs/zcode-hooks.log` (not `cc-hooks.log`). Debug is activated via the shared runtime's `OPENVIKING_DEBUG` env var, but since desktop-spawned hook processes may not inherit shell env vars, the plugin should also support activation via an `ov.conf` flag.

### Shared installer wiring (`install.sh`)

The `install.sh` shared installer gains `zcode` as a recognized harness:

- `validate_selected_harnesses()`: add `zcode` to the case whitelist.
- `refresh_available_harnesses()`: detect ZCode via `~/.zcode/cli/` directory existence or a `zcode` CLI command.
- TUI variables: add `HAVE_ZCODE`, `SEL_ZCODE`.
- A `install_zcode()` function (or `install_zcode_variant()` analogous to `install_trae_variant()`) that registers the plugin in ZCode's marketplace and writes `ovcli.conf` credentials.
- Since ZCode supports the standard plugin manifest + marketplace model, the install path resembles Claude Code's (marketplace add + install) more than TRAE's (write hooks into a config file).

> **Decision provenance**: Adversarial review R2 (B2) — without `install.sh` wiring, `bash install.sh --harness zcode` hard-fails with exit code 2. Every other IDE plugin (cursor, trae, trae-cn) is fully wired.

### Transcript parsing

The adapter includes a `zcode-turns.mjs` module (analogous to `trae-turns.mjs`) that extracts user/assistant turns from the ZCode Stop-event payload. The exact field names in ZCode's hook stdin are **the primary unknown** — the shared runtime's `readHookInput()` handles multiple field name conventions as a fallback, but the adapter must be validated against real ZCode payloads before assuming correctness. The parser strips injected blocks (`<openviking-context>`, `<relevant-memories>`, `<system-reminder>`) to prevent self-referential pollution.

### Contribution strategy: collaborate first, not compete

An open draft PR (#3544 by `now-ing`) already exists for this feature, and a maintainer (`@huangruiteng`) has a substantive technical comment on the linked issue (#3127) requesting a "ZCode contract." The correct first action is to **comment on PR #3544 and Issue #3127** with the verified ZCode extension-surface information (hook events, output schema, template variables, config paths) gathered during this research. Only if the PR author is unresponsive for 2-4 weeks does a new PR become appropriate.

> **Decision provenance**: Adversarial review R3 — submitting a competing PR for a feature with an active draft PR by a high-output contributor, with a maintainer already engaged, is the highest-avoidance-risk category of open-source conflict.

## Testing Decisions

### Seam 1: Pure transcript parser (unit test — primary)

**What**: `scripts/zcode-turns.test.mjs` using `node:test`.

**Prior art**: `examples/trae-memory-hooks/scripts/trae-hooks.test.mjs` — tests `buildTraeTurns(input, state)` with mock inputs covering: tag stripping, turn building, empty-turn dropping, state fallback.

**Test cases for ZCode**:
- Input with `prompt` + `responseText` → produces `[user, assistant]` turns
- Input with `prompt` + `responsePreview` → produces `[user, assistant]` turns
- Input with only `responseText` → produces `[assistant]` turn (no user)
- Input with empty prompt + state fallback → uses `state.pendingPrompt`
- Input with `<openviking-context>` tags in content → tags stripped
- Input with `<relevant-memories>` tags → tags stripped
- Empty input → produces `[]`
- Rollout fallback: stdin lacks user content → reads rollout file for complete conversation
- Rollout incremental: `state.lastTurnId` set → returns only unseen entries
- Rollout turnId propagation: turns from rollout carry `turnId` for dedup

**Why this seam**: The transcript parser is pure (no I/O, no network, no filesystem), making it the highest-value, lowest-cost test. It validates the only ZCode-specific logic that can break silently — if the field names don't match ZCode's actual payload, turns are empty and capture silently no-ops.

### Seam 2: Hook output schema conformance (unit test — secondary)

**What**: `scripts/zcode-hooks.test.mjs` using `node:test`.

**Prior art**: `examples/trae-memory-hooks/scripts/trae-hooks.test.mjs` — asserts hook contract shape.

**Test cases**:
- `SessionStart` output contains only recognized keys (no `decision: "approve"`)
- `UserPromptSubmit` output's `additionalContext` is a string or absent
- `PreToolUse` deny output uses `permissionDecision: "deny"` (not `"approve"`)
- `Stop` output is empty or contains only recognized keys
- Plugin disabled state → output is empty + exit 0

**Why this seam**: ZCode's strict JSON schema is the #1 silent-failure mode (adversarial review R1-F1, R4-V1). A hook that emits an unrecognized key has its entire output discarded — the plugin appears "installed but doing nothing." This seam catches that regression at unit-test time before it reaches manual verification.

### What is NOT tested automatically (out of seam scope)

- End-to-end hook triggering (requires a live ZCode process)
- MCP tool exposure (requires a running OpenViking backend)
- Recall/capture correctness against a live server (integration test)
- Template variable expansion (requires the ZCode plugin loader)

These are covered by a manual verification SOP (documented in the plugin README, modeled on `examples/codex-memory-plugin/VERIFICATION.md`) that accounts for the seven false-positive/negative scenarios identified in the adversarial review.

### CI compatibility

Both test files use `node:test` (Node.js built-in test runner), runnable via:
```bash
node --test scripts/zcode-turns.test.mjs scripts/zcode-hooks.test.mjs
```
No external dependencies, no `npm install` required. Matches the pattern used by TRAE, Claude Code, and the shared runtime tests.

## Out of Scope

- **Competing PR submission**: The first action is commenting on PR #3544 / Issue #3127 with verified info, not opening a new PR. A new PR is a fallback only if collaboration fails.
- **`PreCompact` / `SessionEnd` / `SubagentStart` / `SubagentStop` hooks**: ZCode does not support these events. The commit-on-`Stop` strategy compensates for the absence of `PreCompact`/`SessionEnd`. Subagent isolation is not possible without `SubagentStart`/`SubagentStop`.
- **`PostToolUse` skill-experience injection**: The Claude Code plugin injects skill/experience context on `PostToolUse(Read)`. This is a nice-to-have enhancement, not part of the initial plugin. ZCode supports `PostToolUse`, so it can be added later.
- **Statusline**: ZCode may or may not have a statusline mechanism equivalent to Claude Code's `.statusLine` settings entry. Statusline integration is deferred until the core hook/MCP functionality is verified.
- **ZCode desktop app vs CLI divergence**: Some MCP field-name migrations (`environment` → `env`) are handled by the CLI config parser but may not be by the desktop app's session creation path. The plugin uses canonical field names (`env`, `command`, `args`) but does not attempt to work around desktop-specific parsing quirks.
- **Cross-session memory attribution**: Multiple agents (ZCode, Claude Code, Codex) share one OpenViking data store. Distinguishing which agent captured a given memory is handled by `peer_id` / session prefixing, but a UI for per-agent memory filtering is out of scope.

## Further Notes

### Verified ZCode extension surface (ground truth)

These facts were verified against the live ZCode installation on the development machine (docs from the built-in `zcode-guide` plugin + actual `~/.zcode/cli/config.json` + 4 real installed plugins with hooks):

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
| MCP auto-connect | All scopes (user, workspace, plugin, env, CLI) auto-connect at session start |
| Hook runner enablement | Auto-enabled when any plugin contributes a hook |
| Timeout units | `command` type: `timeout` in **seconds**; `process` type: `timeoutMs` in milliseconds |
| `async` field | No runtime effect — hooks always run inline |
| Third-party plugin trust | All plugin hooks are runnable (no trust gate) |

### Primary unknowns to resolve during implementation

1. **Exact hook stdin field names**: ZCode's `Stop` payload field names for prompt text, assistant response, and transcript path. The shared runtime has multi-field fallbacks, but the adapter must be validated against real payloads.
2. **Output schema acceptance**: Whether `{ "hookSpecificOutput": { "hookEventName": "...", "additionalContext": "..." } }` is accepted as-is, or whether ZCode requires a different wrapper (e.g., top-level `additionalContext`). Must be tested by running the script manually and checking the ZCode log for validation failures.
3. **MCP tool name format**: Whether the namespaced form `mcp__plugin:openviking:openviking__find` is the exact tool name the model sees, and whether existing prompt examples that reference `mcp__openviking__find` need updating.

### Adversarial review artifacts

This spec incorporates findings from a four-reviewer adversarial review:
- **R1 (ZCode extension surface)**: 12 concrete runtime failure scenarios; confirmed 5/6 technical claims; refuted the "vendor is a recognized mechanism" framing.
- **R2 (codebase conventions)**: 2 blockers (vendor/relative-path inconsistency, missing install.sh wiring), 5 majors (missing tests, docs, sync.test.mjs, manifest format).
- **R3 (PR strategy)**: 4 high-risk findings; recommended collaboration over competition.
- **R4 (verification realism)**: 7 false-positive/negative scenarios in the original verification plan.

### Files to be created/modified

Created:
- `examples/zcode-memory-plugin/.zcode-plugin/plugin.json`
- `examples/zcode-memory-plugin/hooks/hooks.json`
- `examples/zcode-memory-plugin/.mcp.json`
- `examples/zcode-memory-plugin/scripts/zcode-hook.mjs`
- `examples/zcode-memory-plugin/scripts/zcode-turns.mjs`
- `examples/zcode-memory-plugin/scripts/zcode-turns.test.mjs`
- `examples/zcode-memory-plugin/scripts/zcode-hooks.test.mjs`
- `examples/zcode-memory-plugin/scripts/session-start.mjs`
- `examples/zcode-memory-plugin/scripts/auto-recall.mjs`
- `examples/zcode-memory-plugin/scripts/auto-capture.mjs`
- `examples/zcode-memory-plugin/scripts/uri-guard.mjs`
- `examples/zcode-memory-plugin/servers/mcp-proxy.mjs`
- `examples/zcode-memory-plugin/scripts/shared/*.mjs` (generated by sync.mjs)
- `examples/zcode-memory-plugin/README.md`
- `examples/zcode-memory-plugin/README_CN.md`
- `examples/zcode-memory-plugin/DESIGN.md`
- `docs/en/agent-integrations/15-zcode.md`
- `docs/zh/agent-integrations/15-zcode.md`

Modified:
- `examples/memory-plugin-shared/sync.mjs` (add zcode to TARGETS)
- `examples/memory-plugin-shared/sync.test.mjs` (mirror TARGETS change)
- `examples/memory-plugin-shared/install.sh` (add zcode harness: detection, validation, TUI, install function)
- `docs/en/agent-integrations/01-overview.md` (add zcode row)
- `docs/zh/agent-integrations/01-overview.md` (add zcode row)
