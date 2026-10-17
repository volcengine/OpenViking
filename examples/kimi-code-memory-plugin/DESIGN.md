# Kimi Code host contract & design record

OpenViking memory plugin for **Kimi Code CLI**, native managed-plugin form.

This record follows the plugin development spec
(`docs/zh/agent-integrations/18-plugin-development.md` §2): every capability is
marked ✅ verified / ⚠️ degraded / ❌ unsupported, with the version it was
verified against. Do not fill gaps with another agent's behavior — verify
against the Kimi source or a real run.

**Verified against: Kimi Code CLI 2.2.0, Windows, Node.js 24.** Hook code paths
are unchanged since 0.43.1 (the version the previous adapter was verified
against; confirmed by issue #5673's reporter across 0.43.1 and 2.1.1).

## Form choice (spec §2.1)

Kimi loads native plugins from its managed directory with a `kimi.plugin.json`
manifest — the same shape as Claude Code and Codex. This plugin is therefore a
standalone native plugin whose entries call the shared runtime
(`scripts/shared/`, generated from `examples/memory-plugin-shared/lib/`). It
replaces the previous `agent-hook-plugin/hosts/kimicode/` adapter, which
shoehorned a native-plugin host into the config-file dispatcher family and
could not be installed on Windows (its `install.sh` gates on
`uname -s` ∈ {Darwin, Linux}).

## Contract

### Version & platform ✅

- Minimum host: 2.1.1. Verified: 2.2.0 on Windows; macOS/Linux expected but
  unverified.
- Node.js ≥ 18.

### Installation ✅

- `/plugins install <local dir | zip URL | GitHub URL>` copies the plugin to
  `$KIMI_CODE_HOME/plugins/managed/openviking-memory/`; the CLI always runs
  from that managed copy (editing the source dir requires reinstall).
- Manifest: `kimi.plugin.json` at the plugin root (`name` must match
  `PLUGIN_NAME_REGEX`). Supported declarations used here: `hooks` (inline
  array), `mcpServers` (inline object), `skills` (directory). Unsupported
  runtime fields (`tools`, `apps`, `inject`, `configFile`, `bootstrap`) are
  rejected by the loader.
- Changes take effect after `/reload` or `/new`. Plugins are per-user, not
  per-project; project scoping is the peer mechanism (§Identity), not the
  installer.

### Events ✅

20 hook events exist; only `UserPromptSubmit`, `PreToolUse` and `Stop` can
block. This plugin registers 7:

| Event | Timeout | Purpose |
|---|---|---|
| `SessionStart` | 30s | bounded pending-queue replay; clears stale end markers. **Stdout is discarded** (kimi-code #2873, open) — never injects. |
| `UserPromptSubmit` | 20s | recall injection via **raw stdout text** |
| `PreToolUse` (matcher `Read\|Glob\|Grep`) | 5s | deny local file tools on `viking://` URIs |
| `Stop` | 30s | capture + threshold commit |
| `PreCompact` | 30s | commit before context compaction |
| `SessionEnd` | 30s | final capture + commit |
| `Interrupt` | 10s | synchronous capture; all OV requests share a 2s budget |

### Input ✅

stdin is snake-case JSON: `hook_event_name`, `session_id`, `session_title`,
`client_type`, `cwd`, plus per-event fields (`prompt` — string **or**
ContentPart[], kimi-code #917; `tool_name`/`tool_input`; `source`;
`reason`).

### Output ✅

- Exit codes: 0 allow (stdout may be appended to context), 2 block (stderr is
  the reason), anything else **fail-open**. Timeout/crash also fail-open.
- Block JSON: `{"hookSpecificOutput": {"permissionDecision": "deny",
  "permissionDecisionReason": "..."}}`.
- **Injection channel: raw stdout text only.** Structured
  `hookSpecificOutput.additionalContext` is silently dropped by Kimi
  (`runHook.ts` parses only message/permissionDecision/permissionDecisionReason;
  kimi-code #4255, open on main).

### Time limits ✅

`timeout` 1–600s per hook (default 30). Windows has no process-group signals,
so a timeout kills the process with no grace period — hence the internal
budgets below.

### Message source ✅ (the core of this plugin)

The authoritative transcript is the session's `agents/main/wire.jsonl`, located
via `~/.kimi-code/session_index.jsonl`. Structural facts verified in the Kimi
source (`packages/agent-core-v2/src/wire/`):

1. **Text is complete; media is not.** The write-time dehydrator rewrites only
   media containers (image URLs) into blob references; text parts land
   verbatim.
2. **The wire is a branch tree.** After undo/rewind, the abandoned branch's
   records remain in the file; the official reader (`wireService.read` →
   `tree.activeChain`) yields only the active chain. **TODO: port activeChain**
   — until then, undo is a known capture-fidelity gap (see §Open issues).
3. **Subagent conversations live in `agents/<subagent>/wire.jsonl`**, not in
   main. Main carries only `subagent.spawned/completed` lifecycle mirrors. v1
   captures main plus those mirrors as assistant steps; full subagent capture
   is v2.
4. **The journal can lose its tail.** `repairWireJournal` truncates a corrupted
   journal to its valid prefix (original kept as `.bak`). The cursor tolerates
   a shrinking file (§Cursor).
5. `llm.request` records are metadata only (hashes, counts, projection flags) —
   never a content source.
6. Loop events are a closed set of five: `step.begin`, `step.end`,
   `content.part`, `tool.call`, `tool.result`. Unknown types are logged and
   skipped.
7. `metadata.protocol_version` (currently 1.5) is checked at decode time; a
   newer version warns and skips capture rather than mis-parsing.

### Process model ⚠️ (Windows degradation)

Kimi's docs describe hook process groups and timeout signals for **non-Windows**
platforms only. The previous adapter detached background writers on
Stop/PreCompact/SessionEnd; on Windows a detached worker would die with its
parent. This plugin therefore runs **everything synchronously** inside the hook
timeout with a short internal OV budget (~8s for capture), overflowing to the
pending queue for SessionStart replay.

### cwd ✅ (verified in source)

Plugin hooks run with `cwd` = the plugin's managed root
(`app/plugin/manager.ts` sets `cwd: record.root`; `KIMI_PLUGIN_ROOT` and
`KIMI_CODE_HOME` are exported as env). **`process.cwd()` is never the project
directory** — every entry reads the project cwd from the stdin payload's `cwd`
field. (CLI 2.2.0 additionally predates the agent-hook cwd fix `c1459a61`.)

### MCP & identity ✅

- `mcpServers.openviking` → `node ./servers/mcp-proxy.mjs` (shared
  `mcp-proxy-core`), exposing the server's tools (`find`, `search`, `grep`,
  `read`, `remember`, `add_resource`, `add_skill`, …) under the
  `openviking_`-free names the server reports.
- Connection/credentials resolve through `buildPluginConfig("kimicode", …)` —
  `kimicode` is a registered harness key, so `ov config` overrides apply.
- OV session id: `kc-<kimi-session-id>` (prefix kept from the previous adapter
  for data compatibility).
- Peer (project memory scope) resolves via shared `workspace-peer` from Git
  identity; non-Git directories get no peer. Peer is a memory scope, not an
  authorization boundary.

### Recovery ⚠️

- resume: same session_id → cursor continues. Kimi #4174 (open) can drop
  post-action hook messages after resume; the file-content cursor is immune
  because it never trusts event arrival order.
- Log rotation / journal truncation: ack-set cursor + reset-on-shrink (§Cursor).
- Protocol drift: warn-and-skip (§Message source 7).

## Capture design

### What is captured

Per turn: the user's real input and the assistant's **visible text** (thinking
parts excluded), plus every tool call/result as structured parts. Everything
the conversation did not author is stripped first (`sanitizeCapturedText`):
`<openviking-context>` recall blocks, `<system-reminder>`, skill/plugin
injections, metadata fences — otherwise this turn's injection becomes next
turn's memory and the loop feeds on itself.

### Origin filter (12-kind taxonomy from wire-manifest.d.ts)

- **Capture**: `user` (and undefined).
- **Capture, marked**: `system_trigger`, `task`, `cron_job` — real prompts not
  typed by the user; marked so extraction can tell them apart.
- **Drop**: `skill_activation`, `plugin_command`, `injection`,
  `shell_command`, `compaction_summary`, `cron_missed`, `hook_result`,
  `retry`.

### Tool mapping

Kimi writes each call exactly once (verified: 205 calls / 205 unique
toolCallIds, 0 duplicates — unlike Codex's multiple representations, so no
dedup machinery is needed). The adapter normalizes to OpenAI-shaped entries and
the shared extractor does the rest:

```
tool.call   → { type: "function_call",       call_id, name, arguments }
tool.result → { type: "function_call_output", call_id, output, isError?, duration_ms? }
```

- `isError` maps to `tool_status: "error"` (shared `toolStatus` reads it
  natively); failed results still carry their output text.
- `note` (host display annotation, e.g. "31 lines read") is dropped.
- `duration_ms` is kept (the server's ToolPart accepts it).
- Text parts and tool calls of the same `stepUuid` merge into one assistant
  message; each tool.result becomes a user-role message that the server's
  retention layer classifies as `tool_transport`.
- Output is sent verbatim; the server owns truncation/externalization.

### Payload & cursor

- Payload: `{ role, parts }` when the turn has structured parts, else
  `{ role, content }`; `turn_id` stamped when available; `peer_id` when a peer
  is resolved. Batched ≤100 per request.
- Cursor: a **turnId acknowledgement set** (dedupKey `turnId:role`, tool parts
  keyed by tool id), advanced only on server confirmation; failures go to the
  pending queue with the cursor untouched. An index cursor was rejected
  because journal truncation would drift it.
- Commit: on `interrupt`/`session-end` always; on `pre-compact` unless
  `autoCommitOnCompact=false`; otherwise at `commitTurnThreshold`.

## Recall design

- Query = the payload's prompt (string or ContentPart[]), project cwd from the
  payload; peer resolved per request.
- Retrieval, ranking, budgets and the server-compat fallback all come from the
  shared recall core — the adapter never re-implements them.
- Empty results stay empty (no placeholder text reaches the model).
- The profile block rides the first successful prompt hook of a session
  (SessionStart stdout is discarded).
- Any failure exits 0 silently: memory must never block the user.

## Skills

`skills/` holds three sync-generated copies (`SKILL_TARGETS` in
`examples/memory-plugin-shared/sync.mjs`, committed): `openviking-memory`
(when/how to search, read, write), `openviking-skills` (the `add_skill` flow),
and `ov-experience-memory` (the experience workflow — the full version applies
because this plugin captures tool parts, so its reads feed trajectories back).
The previous adapter shipped no skills; the model could only receive injected
context, never actively use the MCP tools.

## Open issues affecting this plugin (kimi-code, open on main at 2026-10-17)

- #4255 — `additionalContext` dropped; slash-skill turns skip UserPromptSubmit
  → recall blind spot on `/skill` invocations (accepted).
- #3692 — goal-continuation turns (`system_trigger` origin) skip
  UserPromptSubmit → recall blind spot there (accepted; capture still marks
  them).
- #952 / #3565 — no assistant text in Stop, no native turn-completed event →
  wire.jsonl decoding is mandatory (implemented).
- #4141 — hook events can arrive out of order (reported on Windows) → the
  file-content cursor is immune by design.
- #2873 — SessionStart stdout discarded → profile rides the prompt hook.
- #4174 — post-action hook messages intermittently missing after resume.

## Known gaps (this plugin)

1. **Subagent content** — `agents/<subagent>/wire.jsonl` is not captured; the
   main wire's `subagent.spawned/completed` mirrors are not yet turned into
   assistant steps either (v2).
2. **Doctor install checks** — `ov-memory-doctor.mjs` has placeholder
   install/config/activity checks for the Kimi managed-plugin layout.
3. **Step-level message grouping** — an assistant step's text and its tool
   calls are separate messages (text closes the turn) rather than one mixed
   message. The server's retention handles both; grouping is a fidelity
   nice-to-have, not a correctness issue.
4. **System-origin marking** — `system_trigger`/`task`/`cron_job` prompts are
   captured but the marker convention on the OV side (how extraction tells
   them apart from typed input) is not agreed yet.

Closed since the first draft: undo/branch fidelity (wire-tree.mjs ports
kimi-code's tree.ts activeChain — verified by fixture tests) and turn_id
propagation (a small additive extension to the shared extractCaptureTurns,
regenerated into every plugin's copies by sync.mjs).

## Evidence index

Kimi source (github.com/MoonshotAI/kimi-code): hook types
`packages/agent-core-v2/src/features/externalHooks/internal/types.ts`; injection
`.../internal/userPrompt.ts`; schema `.../internal/runHook.ts`; origin guard
`.../agent/agentExternalHooksService.ts:340`; SessionStart
`.../session/sessionExternalHooksService.ts`; wire manifest
`packages/agent-core-v2/docs/wire-manifest.d.ts`; branch tree
`packages/agent-core-v2/src/wire/{wireService,tree/tree,repair}.ts`; media
offload `.../agent/blob/agentBlobServiceImpl.ts`; plugin manifest
`.../app/plugin/manifest.ts`; plugin hook cwd `.../app/plugin/manager.ts`
(`enabledHooks`); docs `docs/zh/customization/{hooks,plugins}.md`.

Shared runtime: `examples/memory-plugin-shared/lib/` (`capture-utils.mjs`
buildToolPart/toolStatus/sanitizeCapturedText, `batch-send.mjs`,
`agent-hook-runtime.mjs`, `recall-core.mjs`, `pending-queue.mjs`,
`workspace-peer.mjs`, `mcp-proxy-*.mjs`, `doctor-core.mjs`).

Reference implementations: `examples/codex-memory-plugin/`,
`examples/claude-code-memory-plugin/`.

Previous adapter (migration source, removed on merge):
`examples/agent-hook-plugin/hosts/kimicode/`.
