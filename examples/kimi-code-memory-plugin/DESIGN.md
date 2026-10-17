# Kimi Code host contract & design record

OpenViking memory plugin for **Kimi Code CLI**, native managed-plugin form.

This record follows the plugin development spec
(`docs/zh/agent-integrations/18-plugin-development.md` §2): every capability is
marked ✅ verified / ⚠️ degraded / ❌ unsupported, with the version it was
verified against. Do not fill gaps with another agent's behavior — verify
against the Kimi source or a real run.

**Verified against: Kimi Code CLI 2.2.0 (main @ 2026-10-17), Windows, Node.js 24.**
The hook protocol core (`runHook.ts` spawn/exit-code/JSON-parse, stable since
the #2805 refactor of 2026-08-19) is unchanged from what the previous adapter
verified, but the hook chain is NOT frozen — two behavior changes landed
recently and are reflected in this plugin: **#4081** folds UserPromptSubmit
hook output into the prompt message as a `<hook_result hook_event="...">`
text part (the shared sanitizer strips the wrapper — verified in
`capture-utils.test.mjs`), and **#4242** passes the agent cwd to agent-level
hooks (irrelevant here: plugin hooks always run with cwd = plugin root).

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
  `$KIMI_CODE_HOME/plugins/managed/openviking-memory/` (local-dir sources must
  be absolute paths); the CLI always runs from that managed copy (editing the
  source dir requires reinstall).
- Manifest: `kimi.plugin.json` at the plugin root. `name` must match
  `/^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*(?:@[A-Za-z0-9_-]+)?$/` (lowercased
  to the install id). Supported declarations used here: `hooks` (inline
  array), `mcpServers` (inline object), `skills` (directory). Unsupported
  fields (`tools`, `apps`, `inject`, `configFile`, `bootstrap`) are ignored
  with an info-level diagnostic — the manifest still loads.
- **Hook entries are strict**: only `event`/`matcher`/`command`/`timeout`
  (timeout int 1–600s, default 30). Any other field — including `env` — gets
  the WHOLE hook dropped with a warn diagnostic. The pending-replay bound
  therefore cannot be declared in the manifest; session-start.mjs defaults
  `OPENVIKING_PENDING_REPLAY_LIMIT` to 2 in code.
- `installed.json` records carry `id/root/source/enabled/installedAt` — no
  `state` (computed from load diagnostics) and no embedded manifest (re-parsed
  from `root` on every load). `/plugins remove` deletes only the record; the
  managed directory is left behind as an orphan (safe to delete manually).
- Changes take effect after `/reload` or `/new`. Plugins are per-user, not
  per-project; project scoping is the peer mechanism (§Identity), not the
  installer. Hooks of the same event across plugins run concurrently
  (`Promise.all`), deduped by cwd+command; the first block result wins.

### Events ✅

20 hook events exist; only `UserPromptSubmit`, `PreToolUse` and `Stop` can
block. This plugin registers 7:

| Event | Timeout | Purpose |
|---|---|---|
| `SessionStart` | 30s | bounded pending-queue replay. **Stdout is discarded** (kimi-code #2873, open) — never injects. |
| `UserPromptSubmit` | 20s | recall injection via **raw stdout text** |
| `PreToolUse` (matcher `Read\|Glob\|Grep`) | 5s | deny local file tools on `viking://` URIs |
| `Stop` | 30s | capture + threshold commit |
| `PreCompact` | 30s | commit before context compaction |
| `SessionEnd` | 30s | final capture + commit. Fires only when the session closes with reason `exit` or `archive` — a killed process or `/clear` does NOT fire it; the pending queue + ack-set cursor own that recovery. |
| `Interrupt` | 10s | synchronous capture; all OV requests share a 2s budget. **Fire-and-forget**: the host does not await it (unlike Stop/PreCompact/SessionEnd), so there is no ordering guarantee — the session lock in session-state is what serializes it against a following SessionEnd. |

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

1. **Text is complete; media is not.** The write-time dehydrator rewrites
   `data:` URIs (base64 payload ≥ 4KB) in any object carrying a `url` key
   into `blobref:<mime>;<sha256>` references; plain http(s) image URLs are
   NOT rewritten. Text parts land verbatim.
2. **The wire is a branch tree.** After undo/rewind, the abandoned branch's
   records remain in the file; the official reader (`wireService.read` →
   `tree.activeChain`) yields only the active chain. This plugin reads through
   wire-tree.mjs, a port of `tree.activeChain`, so undone turns are not
   captured; a corrupt tree is a skip status, never a guess. (Two tolerated
   port deviations: a corrupt line mid-file is skipped-and-continued rather
   than triggering truncate+repair, and `restorableChain` is not ported —
   read-only capture does not need it.)
3. **The Stop hook runs BEFORE `turn.ended` lands.** Stop fires in
   `onDidFinishStep` (after the last step's records, before `endTurn`), so at
   Stop time the current turn is still unclosed and the decoder emits nothing
   for it — its capture lands at the NEXT trigger (Stop/PreCompact/
   SessionEnd). `endTurn` awaits `drainPersisted`, so `turn.ended` — and
   everything before it — is on disk before SessionEnd; final completeness is
   guaranteed by the SessionEnd trigger, intermediate Stop captures are
   merely one turn behind. Records also reach the file by a queueMicrotask
   batch flush rather than synchronous appends, so the cursor re-reads file
   content instead of trusting event arrival.
4. **Subagent conversations live in `agents/<subagent>/wire.jsonl`**, not in
   main. Main carries only `subagent.spawned/completed` lifecycle mirrors
   (which carry no turnId/stepUuid — and their `parentToolCallId` uses a
   different namespace than main's `toolCallId`, so v2 attribution must join
   via `parentToolCallUuid` or re-measure). v1 captures main plus those
   mirrors as assistant steps; full subagent capture is v2.
5. **The journal can lose its tail.** `repairWireJournal` truncates a corrupted
   journal to its valid prefix (the first failure's original is kept as
   `.bak`; a later repair does not overwrite an existing `.bak`). The cursor
   tolerates a shrinking file (§Cursor).
6. `llm.request` records are metadata only (hashes, counts, projection flags) —
   never a content source.
7. Loop events are a closed set of five: `step.begin`, `step.end`,
   `content.part`, `tool.call`, `tool.result`. (`turn.prompt`/`turn.ended`/
   `metadata`/`subagent.*` are separate record types, and human-event records
   exist outside the manifest's index; the decoder filters by `row.type`.)
   Unknown event types are logged and skipped.
8. `metadata.protocol_version` (currently 1.5) is checked at decode time; a
   newer version warns and skips capture rather than mis-parsing.

### Process model ⚠️ (Windows degradation)

Kimi's docs describe hook process groups and timeout signals for **non-Windows**
platforms only. The previous adapter detached background writers on
Stop/PreCompact/SessionEnd; on Windows a detached worker would die with its
parent. This plugin therefore runs **everything synchronously** inside the hook
timeout with a short internal OV budget (~8s for capture; Interrupt 2s),
overflowing to the pending queue for SessionStart replay. Two host facts shape
this: the host kills a timed-out hook without a usable grace period on Windows
(runHook sends SIGTERM then SIGKILL after 100ms — SIGTERM terminates
immediately on Windows), and Interrupt is fire-and-forget, so a slow Interrupt
capture is serialized against a following SessionEnd only by the session lock,
not by host ordering.

**Subagent scopes share the session_id.** Every agent scope (main + each
subagent) instantiates its own agent-level hooks, but the payload's
`session_id` is the session-level id everywhere — Stop fires per scope, so a
subagent finishing triggers this plugin's capture with the main session's id.
This is safe by construction: capture only ever decodes `agents/main/wire.jsonl`
(a subagent-scope Stop is an early, partial, deduplicated no-op), the session
lock serializes concurrent writers, and the ack set makes any repeat a no-op.
It does mean session state must never assume one hook process per session at a
time — the lock exists precisely because that assumption is false.

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
- Peer (project memory scope): hooks resolve it via shared `workspace-peer`
  from Git identity; non-Git directories get no peer. The MCP proxy does not
  follow that rule — `toMcpProxyConfig` sends no peer unless
  `recallPeerScope` is `"actor"`, so `remember`/`add_resource` calls through
  MCP land in the user-level space while captured turns land under the
  project peer. Peer is a memory scope, not an authorization boundary.

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
  keyed by tool id; a second entry that would collide gets a `#n` suffix),
  advanced only on server confirmation; failures go to the pending queue with
  the cursor untouched. An index cursor was rejected because journal
  truncation would drift it.
- Commit: only for a strand-free run — no `failed` payloads, nothing queued,
  and no older queue entry for the session. Then `interrupt`/`session-end`
  always, `pre-compact` unless `autoCommitOnCompact=false`, otherwise at
  `commitTurnThreshold` counted in payloads (one per captured turn). A
  SessionEnd that cannot commit because messages are parked queues a
  `commitSession` behind them, so the replay commits after they drain.

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
2. **Step-level message grouping** — an assistant step's text and its tool
   calls are separate messages (text closes the turn) rather than one mixed
   message. The server's retention handles both; grouping is a fidelity
   nice-to-have, not a correctness issue.
3. **System-origin marking** — `system_trigger`/`task`/`cron_job` prompts are
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
