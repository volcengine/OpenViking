# CodeBuddy host contract

Established facts for the CodeBuddy Code CLI host, per
`docs/en/agent-integrations/18-plugin-development.md` §2.

**Verified against**: CodeBuddy Code CLI **v2.163.0** (`client: "CLI"` in every payload),
macOS 26.x / darwin 25.5.0, 2026-10-09.

**Method**: a throwaway probe plugin loaded with `codebuddy --plugin-dir /tmp/cb-ov-probe`
(never installed, no user config written). It appended one JSON line per event to
`/tmp/cb-ov-probe/out.jsonl` and always returned a valid response. Three runs:

| Run | Command | Turns |
| --- | --- | --- |
| 1 | `codebuddy --plugin-dir <probe> -p '<prompt>'` | 1 (single-shot) |
| 2 | `codebuddy -p --input-format stream-json --output-format stream-json` | 2 |
| 3 | `codebuddy --plugin-dir <probe>` — **interactive TUI**, 3 prompts, one subagent, `/exit` | 3 |

Legend: **verified** = observed in a real session · *documented* = only in the host docs ·
**pending** = not reproduced (see §9).

---

## 1. Versions and platforms

| Item | Value |
| --- | --- |
| Host | CodeBuddy Code CLI v2.163.0 |
| Hook feature status | **Beta** (host docs, `cn/cli/hooks.md:3-4`) |
| Verified OS | macOS (darwin 25.5.0) — Windows/Linux untested |
| Node runtime | The host does **not** bundle one. Hook processes inherit the user's PATH, where `node` resolves. `node` on PATH, `process.execPath` = the nvm v24 node. **No shebang or `bin/` wrapper needed** as long as the command is `node <abs path>`. |

## 2. Installation

| Form | Status |
| --- | --- |
| `codebuddy --plugin-dir <dir>` (session-scoped) | **verified** — plugin loaded, hooks fired, 3 runs |
| `CODEBUDDY_PLUGIN_DIRS` env var | *documented* equivalent of `--plugin-dir` |
| Local marketplace → versioned cache | **pending** (P6) |

- `${CODEBUDDY_PLUGIN_ROOT}` and `${CLAUDE_PLUGIN_ROOT}` are both exported to hook processes
  and both point at the plugin directory (**verified**, run 1).
- **Inline substitution inside hook command strings is implemented**: the shipped bundle
  performs `replace(/\$\{CODEBUDDY_PLUGIN_ROOT\}/g, pluginRoot).replace(/\$\{CLAUDE_PLUGIN_ROOT\}/g, pluginRoot)`
  (verified in `dist/codebuddy.js`). `${…SKILL_DIR}` variants exist too. Not yet exercised by
  a run — the probe used hardcoded paths.
- `${CODEBUDDY_PLUGIN_DATA}` resolved to `~/.codebuddy/plugins/data/cb-ov-probe-inline`. The
  **`-inline` suffix comes from `--plugin-dir`**; an installed plugin gets a different id, so
  the data directory must always be read from the env var, never rebuilt from the plugin name.
- On load the host logs `mods: skipped <dir>: no hooks/hooks.json with modules: not a mod directory`.
  Benign — that is the Claude-Code-style `modules` lookup. **Do not put a `modules` key in
  `hooks/hooks.json`.**

## 3. Events

| Event | Fired? | matcher semantics |
| --- | --- | --- |
| `SessionStart` | **verified** (all 3 runs) | `source`; observed `startup` |
| `UserPromptSubmit` | **verified in the TUI only** (3×); **never fired in 2 headless runs / 4 turns** | *documented*: no matcher |
| `PreToolUse` | **verified** | tool-name regex; observed names `Bash`, `Write`, `Read`, `Edit`, `Grep`, `ToolSearch`, `Agent` |
| `PostToolUse` | **verified** | same matcher space as PreToolUse |
| `Stop` | **verified** | *documented*: no matcher |
| `SessionEnd` | **verified** (run 3) | `reason`; observed `prompt_input_exit` |
| `SubagentStart` | **verified** (run 3) | not in the user-hook event table, **but fires for plugin hooks** |
| `SubagentStop` | **verified** (run 3) | no independent stdin schema in the host docs |
| `PreCompact` | **pending** — no compaction occurred in any run | *documented*: `manual` / `auto` |

⚠️ **`UserPromptSubmit` is emitted by the interactive TUI but not by headless paths** — an
undocumented asymmetry with direct consequences for automation-based testing of the recall
path (§9). The emission code and guard exist in the bundle.

Same-event concurrency: **verified parallel** — two hooks on `SessionStart` produced samples
with identical millisecond timestamps in runs 1 and 3.

Duplicate delivery: `SessionStart` fired **twice per registration** in run 2 (stream-json)
but once in runs 1 and 3. Any session-start work must therefore be idempotent.

## 4. Input (stdin JSON)

*Documented* common shape: `{session_id, transcript_path, cwd, permission_mode,
generation_id?, hook_event_name}`. Observed keys are **richer than documented**:

| Field | SessionStart | UserPromptSubmit | Pre/PostToolUse | Stop / SubagentStop | SessionEnd | SubagentStart |
| --- | --- | --- | --- | --- | --- | --- |
| `session_id` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `transcript_path` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ (subagent file) |
| `cwd` | **absent** | ✓ | ✓ | ✓ | ✓ | ✓ |
| `hook_event_name` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `permission_mode` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `generation_id` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `client` / `version` / `model` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `agent_type` | — | — | ✓ (`cli`) | ✓ (`cli`) | ✓ (`cli`) | ✓ (`general-purpose`) |
| `prompt` | — | ✓ | — | — | — | — |
| `is_internal_continuation` | — | ✓ (`false`) | — | — | — | — |
| `tool_name` / `tool_input` | — | — | ✓ | — | — | — |
| `tool_response` | — | — | PostToolUse | — | — | — |
| `call_id` / `tool_use_id` | — | — | ✓ | — | — | — |
| `stop_hook_active` | — | — | — | ✓ (`false`) | — | — |
| `last_assistant_message` | — | — | — | ✓ | — | ✓ |
| `is_truncation_recovery`, `background_tasks`, `session_crons` | — | — | — | ✓ | — | — |
| `source` | ✓ | — | — | — | — | — |
| `reason` | — | — | — | — | ✓ | — |
| `agent_id` | — | — | — | ✓ | — | ✓ |
| `agent_transcript_path` | — | — | — | ✓ | — | — |

**Consequences for the adapter**

1. `SessionStart` has **no `cwd`**; `process.cwd()` equalled the session cwd in the probe, so
   the cwd-based config reload can fall back to `process.cwd()` for that event. No
   session→cwd map is needed unless a later run contradicts this.
2. **`UserPromptSubmit` carries `is_internal_continuation`** — recall must skip those turns
   (they are host-driven continuations, not user asks).
3. `PreToolUse` exposes `tool_input` for the literal `viking://` scan, and `session_id` +
   `cwd` for the guard decision.
4. `SubagentStart`/`SubagentStop` carry the subagent's own transcript file
   (`<session>/subagents/agent-<id>.jsonl`), so subagent conversations can be captured
   separately; `agent_id` is stable across Start→Stop.

## 5. Output

Envelope is **byte-compatible with Claude Code**:

- context injection (`SessionStart`, `UserPromptSubmit`):
  `{"decision":"approve","hookSpecificOutput":{"hookEventName":"<event>","additionalContext":"…"}}`
- `PreToolUse`: `hookSpecificOutput.permissionDecision` ∈ `allow|deny|ask` +
  `permissionDecisionReason` + optional `modifiedInput`.
- public fields: `continue` (default true), `stopReason`/`reason`, `suppressOutput`,
  `systemMessage` (user-visible only).

**Verified behaviours**

| Behaviour | Result |
| --- | --- |
| Unknown JSON field tolerated? | **yes** — a `SessionStart` response carrying an extra `_probe_unknown_field` **plus** `additionalContext` still got the context injected: the marker `__CB_PROBE_CTX__` appeared **4×** in the session transcript and the model reasoned about it. The Claude Code envelope (including `decision: "approve"`) can be reused as-is. |
| Empty output (`{}`) safe? | **yes** — every probe hook returned `{}` and no session was blocked or delayed. |
| `permissionDecision: "allow"` honoured? | **yes** — the probe allowed a `Bash` call that carried `viking://`; it ran. |
| Is `permissionDecisionReason` visible to the model? | **NO** — the probe returned `permissionDecisionReason: "__CB_PROBE_NOTICE__ …"` on that same `PreToolUse`; the marker count in the transcript is **0**. No in-band model notice channel exists on `PreToolUse`. |

> **Design consequence**: a "this is a virtual URI" *notice* cannot be delivered to the model.
> Either `deny` the call (with a reason the user sees) or drop the notice; `systemMessage` is
> user-visible only.

## 6. Exit codes

*documented*: `0` success (`UserPromptSubmit`/`SessionStart` stdout joins the context),
`2` blocking error (stdout `reason` wins, stderr is the fallback), anything else is a
non-blocking error shown to the user. **Not exercised by the probe** — every hook exited 0.

## 7. Time limits and async writes

*documented*: default 60 s per hook, overridable per command with `timeout` (seconds).

**Verified (run 3)** — registered on `SessionEnd` with `timeout: 2`:

| Observation | Reading |
| --- | --- |
| The hook parent did not reach its post-timeout code path (`KILLED` line absent) | the **2 s timeout is enforced** — the hook process is killed |
| A **detached** grandchild (`spawn(..., {detached:true}).unref()`) that slept 30 s then appended a line | **survived** — line present, file mtime exactly +30 s after the event |
| A **non-detached** child doing the same | **also survived** |
| No process-group teardown | **confirmed by the pair above** |

> **Design consequence**: killing a hook does **not** kill its descendants, so the shared
> `maybeDetach` async-write pattern is safe on this host — a `Stop`/`SessionEnd` hook can
> detach a worker and return immediately. (The timeout still bounds the *hook's own* work,
> so any inline work must fit inside it.)

## 8. Message source (transcript)

- Path: `~/.codebuddy/projects/<project-slug>/<session-uuid>.jsonl`. The slug encodes the
  session cwd (`/` → `-`; `/private/tmp/cb-ov-probe` → `private-tmp-cb-ov-probe`).
- Subagent conversations live in `<session-uuid>/subagents/agent-<uuid>.jsonl` (verified;
  files exist and are what `SubagentStart`/`SubagentStop` point at).
- Format: append-only JSONL; record `type` ∈ `message`, `reasoning`, `function_call`,
  `function_call_result`, `file-history-snapshot`, `summary`, `turn-metrics`, … plus
  `sessionId`, `cwd`, `providerData`.
- **Flush timing: complete at `Stop`** — verified: the size recorded by the `Stop` hook
  (37 571 B) was byte-identical after the session ended, the tail line parsed as JSON, and it
  already contained the sentinel token and the final assistant text. Capture can read the
  transcript directly at `Stop`; no "read one turn behind" fallback is required.

## 9. Remaining unknowns

1. `PreCompact` — never fired (no compaction in any run). Register it, but treat as unproven.
2. Plugin `.mcp.json` shape — `{"mcpServers":{…}}` vs the bare map the Claude Code plugin
   uses. Verify at P2/P10.
3. Marketplace install → versioned cache behaviour (P6), including the "installed plugins
   cannot reference files outside their directory" rule.
4. Non-blocking-exit (code 1 / other) and exit-2 semantics — documented but unexercised.

## 10. Naming divergence

The OpenViking server-side log-ingestion subsystem ships a `workbuddy` adapter
(`openviking/ingest/sources/workbuddy.py`) that parses **this same transcript format** (host
blocks `<user_query>`, `cb_summary`, `memory_and_skills_reminder`, …). That name is a
**server-side ingest identifier**, not this client's harness id: the plugin registers the
canonical harness key **`codebuddy`** in `HARNESS_KEYS`. Do not add `workbuddy` to
`HARNESS_KEYS`; do reuse the adapter's parsing knowledge in `scripts/cb-transcript.mjs`.
