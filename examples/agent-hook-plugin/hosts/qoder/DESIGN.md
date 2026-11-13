# Qoder CLI host contract

Established facts for the Qoder CLI host, per the host docs (`docs.qoder.com/cli/hooks`, `/cli/hooks-reference`, `/cli/plugins`, `/cli/plugins-reference`, `/cli/settings-reference`), plus the design of the `qoder` adapter that implements them (`hosts/qoder.mjs` and `hosts/qoder-turns.mjs` beside this file).

**Verified against**: Qoder CLI **v1.1.88** (npm `@qoder-ai/qodercli`, `entrypoint: "cli"` and `version: "1.1.88"` on every transcript record), macOS / darwin 27.2.0, 2026-11-13.

**Method**: a throwaway probe plugin (every hook appended its stdin and env to a JSONL file and answered per a mode file; one skill; one stdio MCP server that logged its argv and env), installed through the real host CLI, exercised with one-line prompts, then uninstalled. The finished adapter was then run once more through the real host (run 11), and the installer's marketplace flow was replayed with the real `qodercli` against a throwaway `QODER_CONFIG_DIR` (run 12).

| Run | Command | Turns | Purpose |
| --- | --- | --- | --- |
| 1 | `qodercli -p --output-format stream-json '<prompt>'` (marketplace install) | 1 | env, deny, MCP listing |
| 2 | same | 1 | MCP call naming, Pre/PostToolUse `additionalContext` |
| 3 | `qodercli -p '<prompt>'` | 1 | plain-text stdout, `SessionEnd` timeout and kill semantics |
| 4 | `qodercli -p -c '<prompt>'` | 1 | resume |
| 5 | `qodercli -p -c '/compact keep the secret words'` | 0 | manual compaction |
| 6 | `qodercli -p --output-format stream-json '<prompt>'` | 1 | plugin skill, Explore subagent |
| 7 | `qodercli -p '<prompt>'`, `UserPromptSubmit` exits 2 | 0 | blocking (no model call) |
| 8 | interactive TUI in tmux, 2 prompts, `/exit` | 2 | TUI events, exit 1 |
| 9 | `qodercli -p '<prompt>'` (`name@local` install) | 1 | transcript flush at `Stop` |
| 10 | `qodercli -p --plugin-dir <dir>`, `UserPromptSubmit` exits 2 | 0 | `--plugin-dir` env and listing |
| 11 | `qodercli -p --plugin-dir <assembled openviking-memory>` against a mock OpenViking | 1 | the adapter end to end |
| 12 | `plugins validate`, `marketplace remove/add`, `install`, `list`, `uninstall` with `QODER_CONFIG_DIR=<tmp>` | 0 | the installer's host calls |

Legend: **verified** = observed in a real session · *documented* = only in the host docs or read from the shipped bundle (`bundle/qodercli.js`) · **pending** = not reproduced (see §10).

---

## 1. Versions and platforms

| Item | Value |
| --- | --- |
| Host | Qoder CLI v1.1.88, binary `qodercli` |
| Config root | `~/.qoder`, overridden by `QODER_CONFIG_DIR` or `--config-dir` (**verified** for the `plugins` commands, run 12); a fresh root is not logged in |
| Region builds | Only the global build is supported. The bundle also carries a CN region build whose config root is `~/.qoder-cn` (`QODERCN_CONFIG_DIR`) under the same `qodercli` binary name; the installer never looks there (§10) |
| Verified OS | macOS (darwin 27.2.0); Windows and Linux untested |
| Node runtime | The host does not hand hooks a Node. Hook processes inherit the user's PATH and the `node` found there. Commands run through a shell (`bash -c`, *documented*), so `node "<abs path>"` needs no shebang or `bin/` wrapper. |

The npm package also ships a `qoder` binary. It is a dispatcher that hands `ide`, `chat`, `serve-web`, `tunnel` and any first argument naming an existing path to the Qoder IDE (*documented*, bundle), and the IDE may put its own `qoder` on PATH, so the installer only ever calls `qodercli`.

## 2. Installation

| Form | Status |
| --- | --- |
| `qodercli plugins install <local dir> [--json] [-s user]` → `<name>@local` | **verified** |
| Local marketplace: `plugins marketplace add <dir>` then `plugins install <name>@<marketplace>` | **verified** |
| `qodercli --plugin-dir <dir>` (session-scoped, loads in place) | **verified** |
| Plugin `.mcp.json` → stdio MCP server | **verified**, see §9 |

Both install routes are non-interactive (there is no `-y` flag and none is needed), and a new `qodercli` process picks the plugin up without `/plugins reload`. `--json` on `install` answers `{"ok":true,"pluginId":"…","version":"…","path":"<cache dir>"}`; a failure answers `{"error":true,"code":"plugin_not_found",…}` with exit code 1.

**Manifest.** `.qoder-plugin/plugin.json`, only `name` required; the loader falls back to `.claude-plugin/plugin.json` (*documented*, bundle). `plugins validate <dir>` lists the components it found; for the assembled plugin it reports `skills (4)`, `hooks (6)`, `mcp-servers (1)` (**verified**, run 12).

**Marketplace manifest.** Looked up as `.qoder-plugin/marketplace.json`, then `.claude-plugin/marketplace.json`, then `marketplace.json` (*documented*, bundle; the first **verified**). Required: `name`, `owner`, `plugins[]`. A local entry's `source` **must start with `./`** (`"../x"` is rejected with `plugins.0.source: Path must start with ./`), so the marketplace root is the plugin's parent directory, as for CodeBuddy. `marketplace add` **copies** the directory to `<config root>/plugins/marketplaces/<marketplace>/`, records it in `plugins/known_marketplaces.json` and in `settings.json` → `extraKnownMarketplaces`. `marketplace remove` on a name that is not registered exits 1 with `Marketplace "<name>" not found.` (**verified**).

**Where the installed copy lives** (verified for both routes):

| Route | Plugin id | `QODER_PLUGIN_ROOT` (cache) | `QODER_PLUGIN_DATA` |
| --- | --- | --- | --- |
| local dir | `<name>@local` | `~/.qoder/plugins/cache/local/<name>/<version>` | `~/.qoder/plugins/data/<name>-local` |
| marketplace | `<name>@<marketplace>` | `~/.qoder/plugins/cache/<marketplace>/<name>/<version>` | `~/.qoder/plugins/data/<name>-<marketplace>` |
| `--plugin-dir` | none | the source directory itself | `~/.qoder/plugins/data/<name>-flag` (created and left behind) |

Install state: `plugins/installed_plugins_v2.json` (`installPath`, `version`, `scope`) plus `settings.json` → `enabledPlugins["<id>"]: true`. The adapter keeps no state in `QODER_PLUGIN_DATA`; hook state lives under `~/.openviking/hook-state/qoder/`, as for every hook host.

**The cache copy must be self-contained.** Hooks and the MCP server run from the cache copy (verified: `QODER_PLUGIN_ROOT` and the MCP server's argv both point into `cache/`). The copier **silently drops symlinks** (a symlinked directory and a symlinked file were both missing from the cache, verified) and skips `node_modules` (verified) plus `.git .svn .hg .vscode .idea .husky .github bower_components` (*documented*, bundle). Hidden files are kept. So the installer assembles real files, the shared runtime beside the adapters, exactly as for CodeBuddy.

**Upgrades.** Re-running `install` with the **same** version refreshes the cache directory in place; a **bumped** version lands in a new `<version>/` and the old one stays on disk.

**Uninstall.** `plugins uninstall <id>` removes the registry entry and the `enabledPlugins` key, **deletes the data directory** unless `--keep-data`, and **leaves the cache directory** behind with an `.orphaned_at` marker (epoch ms). `plugins marketplace remove <name>` deletes the `marketplaces/<name>` copy and the `known_marketplaces.json` entry, **uninstalls every plugin installed from it** (`Uninstalled 1 installed plugin from it; plugin caches were marked orphaned`, verified, run 12) and leaves the caches marked orphaned rather than deleting them.

Env exported to every plugin hook (verified): `QODER_PLUGIN_ROOT`, `QODER_PLUGIN_DATA`, `QODER_PROJECT_DIR` (the session cwd) **and `CLAUDE_PLUGIN_ROOT`** (same value as `QODER_PLUGIN_ROOT`).

`${QODER_PLUGIN_ROOT}` in a hook command is expanded **by the shell**, not substituted by the CLI (*documented*; verified as `node "${QODER_PLUGIN_ROOT}/…/hook.mjs" …`). The docs recommend double-quoting the placeholder. Under PowerShell the CLI substitutes it itself (*documented*).

## 3. Events

27 events are documented; the ones the adapter can use:

| Event | Fired? | matcher semantics |
| --- | --- | --- |
| `SessionStart` | **verified**: `startup` (new), `resume` (`-c`), `compact` (after `/compact`) | `source`: `startup`/`resume`/`clear`/`compact`/`new` |
| `UserPromptSubmit` | **verified in `-p` and in the TUI**, once per prompt | *documented*: none |
| `PreToolUse` | **verified**: `Read`, `Glob`, `Bash`, `Skill`, `Agent`, `mcp_get`, `mcp_call` | tool name: exact, `a\|b`, regex, `*` or omitted = all |
| `PostToolUse` | **verified** (not fired for a denied call) | same as `PreToolUse` |
| `Stop` | **verified**: exactly once per turn, `-p` and TUI | *documented*: none |
| `SessionEnd` | **verified**: `reason: "other"` in `-p` **and on TUI `/exit`** | `reason` |
| `PreCompact` / `PostCompact` | **verified** (manual `/compact`) | `trigger`: `manual`/`auto` |
| `SubagentStart` / `SubagentStop` | **verified** (Explore subagent) | agent type |

Sequences observed:

- `-p`: `SessionStart(startup)` → `UserPromptSubmit` → (`PreToolUse` → `PostToolUse`)* → `Stop` → `SessionEnd(other)` → **a second `SessionEnd(other)` for a different `session_id`** whose `transcript_path` does not exist and which never had a `SessionStart`. It is an internal auxiliary session; it fired in every `-p` run (run 11 included) and never in the TUI.
- `-p -c`: the same with `SessionStart(resume)`, **the same `session_id` and the same transcript file**.
- `-p -c '/compact …'`: `SessionStart(resume)` → `PreCompact(manual)` → `PostCompact(manual)` → `SessionStart(compact)` → `SessionEnd` ×2; no `UserPromptSubmit`, no `Stop`; the session id does not change.
- TUI: `SessionStart(startup)` → (`UserPromptSubmit` → `Stop`) × 2 → `SessionEnd(other)` on `/exit`. Unlike CodeBuddy, **`UserPromptSubmit` fires headless too**.

Same-event concurrency: **parallel**; two `SessionEnd` hooks started in the same millisecond.

Hooks fired by a subagent's own tool calls carry `agent_id` and `agent_type`; the main agent's do not.

## 4. Input (stdin JSON)

Observed keys are richer than documented (`agent_id`/`agent_type` appear only inside a subagent):

| Field | SessionStart | UserPromptSubmit | Pre/PostToolUse | Stop | SessionEnd | Pre/PostCompact | SubagentStart/Stop |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `session_id`, `transcript_path`, `cwd`, `hook_event_name`, `workspace_dirs`, `permission_mode` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `model` | ✓ | | ✓ | ✓ | | ✓ | ✓ |
| `source` | ✓ | | | | | | |
| `prompt`, `request_set_id` | | ✓ | | | | | |
| `tool_use_id`, `tool_name`, `tool_input` | | | ✓ | | | | |
| `tool_response` | | | Post | | | | |
| `skill_context` / `subagent_context` | | | `Skill` / `Agent` calls | | | | |
| `stop_hook_active`, `last_assistant_message` | | | | ✓ | | | Stop |
| `parent_request_set_id`, `parent_business_info` | | | | ✓ | | | |
| `reason` | | | | | ✓ | | |
| `trigger` | | | | | | ✓ | |
| `custom_instructions` / `compact_summary` | | | | | | Pre / Post | |
| `agent_id`, `agent_type` | | | subagent only | | | | ✓ |
| `agent_transcript_path` | | | | | | | Stop |

`permission_mode` is the host's own vocabulary (`default`, `yolo`, …).

**Consequences for the adapter**

1. `cwd` is present on every event, including `SessionStart` (unlike CodeBuddy), and equals the hook's `process.cwd()`; no fallback is needed.
2. `UserPromptSubmit` has **no internal-continuation flag**. Host-queued prompts (peer messages, task notifications, the malformed-tool-use retry) are written with `isMeta: true` and a non-`human` `origin.kind` (*documented*, bundle); whether they go through `UserPromptSubmit` at all is **pending**. Recall uses `prompt` as-is.
3. `Stop` carries `last_assistant_message` but neither the prompt nor the tool calls, so capture reads the transcript (§8), which is complete at `Stop`.
4. `PreToolUse` exposes `tool_input` (`file_path` for `Read`, `pattern`/`path` for `Glob`) for the `viking://` scan.
5. `SubagentStop.agent_transcript_path` is `<session>/subagents/agent-<agent_id>.jsonl`.

## 5. Output

The Claude Code envelope works on both injection events: `{"hookSpecificOutput":{"hookEventName":"<event>","additionalContext":"…"}}`. The docs make `hookEventName` mandatory whenever `hookSpecificOutput` is present (the whole output is rejected otherwise). Top-level `decision` is documented as `"allow"`/`"deny"` only, so the adapter leaves `decision` out; CodeBuddy's `"decision":"approve"` was never tried.

**Verified behaviours**

| Behaviour | Result |
| --- | --- |
| `additionalContext` on `SessionStart` / `UserPromptSubmit` | **injected**: the model quoted both markers back (runs 1–3, 6) |
| Plain-text (non-JSON) stdout on `UserPromptSubmit` | **injected** (run 3); *documented* for `SessionStart` too |
| `additionalContext` on `PreToolUse` (with `allow`) and `PostToolUse` | **injected**, rendered to the model as `PreToolUse hook additional context (tool: Read): …` / `PostToolUse hook additional context (tool: Read): …`. Unlike CodeBuddy, `PreToolUse` **does** have an advisory channel. |
| `permissionDecision: "deny"` + `permissionDecisionReason` on `Read` | **blocks**: the call does not run, `PostToolUse` does not fire, and the model reads an `is_error` tool result `Error: <hook command> hook blocking error from command: "<hook command>": <reason>`. The reason reaches the model, prefixed with the raw hook command line. |
| `permissionDecision: "allow"` on `mcp_call` | **does not bypass** the host permission check: in `-p` with `permission_mode: default` the call still failed with `Error: Allow MCP tool <server>/<tool>?` |
| Is injected context persisted in the transcript? | **Yes** (unlike CodeBuddy): JSON `additionalContext` becomes an `attachment` record `{type:"hook_output", hookEventName, output[, toolName]}`; plain-text stdout becomes `{type:"hook_success", content, hookEvent}`. On `-c` resume the `SessionStart(resume)` output was **not** recorded (whether it was injected is pending). |
| Empty output | safe: the write-path hooks answer nothing and no session was blocked |

> **Design consequence**: capture never reads `attachment` records as conversation, or the recalled memories this plugin injects would be captured back. The deny reason does reach the model, and because MCP tools load lazily (§9) its example names the `mcp_call` form (`mcp_call(toolName="mcp__openviking__read", arguments={uri="…"})`). Whether a model follows a deny with a successful `mcp_call` is *pending*: no probe ran that sequence, and in `-p` with `permission_mode: default` the `mcp_call` is refused anyway.

## 6. Exit codes

| Code | Result |
| --- | --- |
| `0` | success; stdout parsed as JSON, otherwise taken as plain text (injected on `SessionStart`/`UserPromptSubmit` only) |
| `2` on `UserPromptSubmit` | **verified**: the prompt is rejected: no model call, no `Stop`, and `qodercli -p` exits 1 with `result.subtype: "error_during_execution"`. A recall hook must therefore never exit 2. |
| `1` on `UserPromptSubmit` | **verified** (TUI): non-blocking; stdout is **ignored**, stderr is shown to the user as `UserPromptSubmit hook error / Failed with non-blocking status code: <stderr>`, and the turn proceeds |

## 7. Time limits and async writes

*documented*: default **600 s** per `command` hook, overridable with `timeout` (**seconds**); `async: true` (group or entry) runs a hook in the background, its result injected on the next model turn; `asyncRewake`, `once`, `if`, `statusMessage` and `args` (exec form) also exist. None of the `async` variants was exercised.

**Verified (run 3)**: registered on `SessionEnd` with `timeout: 2`, the hook slept 10 s after spawning two children that each slept 8 s and then appended a line.

| Observation | Reading |
| --- | --- |
| Session log `"error":"Hook timed out after 2s"`; the parent's post-10 s line absent | the timeout is enforced and the hook is killed |
| The **detached** child (`spawn(…,{detached:true}).unref()`) wrote its line | detached descendants **survive** |
| The **non-detached** child never wrote its line | **unlike CodeBuddy, the kill reaches the hook's process group** |
| `qodercli -p` exited only after the `SessionEnd` hooks of both sessions had finished or timed out | `SessionEnd` hooks hold up exit |

> **Design consequence**: the shared async writer spawns its worker with `detached: true` (`memory-plugin-shared/lib/async-writer.mjs`), so `detachesCapture` is safe here (run 11 captured and committed through the detached worker); any helper that is not detached dies with the hook. Every hook has an explicit `timeout`, since with the 600 s default a stuck `SessionEnd` would block `qodercli` exit for ten minutes.

Debugging aid: `~/.qoder/logs/sessions/<cwd-slug>/<session_id>/segments/*.jsonl` logs every hook run (`hook.started` / `hook.finished` with `plugin_id`, `duration_ms`, `exit_code`, `error`).

## 8. Message source (transcript)

- Path: `~/.qoder/projects/<cwd-slug>/<session_id>.jsonl`, the slug being the cwd with every non-alphanumeric character replaced by `-` (`/private/tmp/a.b/ws` → `-private-tmp-a-b-ws`). Every event's `transcript_path` names it. Beside it, `<session_id>/` holds `state.json`, `compression-v2/state.json` and `subagents/agent-<agent_id>.jsonl` (with `.meta.json` and `task-*.json`).
- Format: append-only JSONL. Records carry `uuid`/`parentUuid`/`isSidechain`/`sessionId`/`cwd`. Types: `workspace-directories`, `runtime-config`, `user`, `assistant`, `attachment`, `system`, `active-leaf`, `last-prompt`.
- **The human prompt**: a `user` record with `origin: {kind: "human"}`, `humanInput: {text, mode: "prompt"}`, a `promptId`, `isSidechain: false` and no `isMeta`. `message.content` is `[{type:"text", text}]`, the raw prompt without host markup.
- **Not a prompt**, all `type: "user"`:
  - tool results: `content: [{type:"tool_result", tool_use_id, is_error?, content}]` with `sourceToolAssistantUUID`, `toolUseResult` and the turn's `promptId`;
  - a skill body: `isMeta: true`, string content `Base directory for this skill: …`;
  - the compaction summary: `isCompactSummary: true`, `isCompactionPrefix: true`, no `origin`.
  Other `origin.kind` values in the bundle (`bridge`, `peer`, `task-notification`, `coordinator`, `malformed-tool-use-retry`) were not observed (*documented*).
- **Assistant side**: one `assistant` record **per content block** (`text`, `tool_use`, `thinking`), the blocks of one model response sharing `responseGroupId`. Persisted `thinking` blocks carry the model's reasoning text. `tool_use` is `{id, name, input}`; an MCP call is `name: "mcp_call"` with `input: {toolName: "mcp__<server>__<tool>", arguments}` (§9).
- **Host records to skip**: `attachment` (`skill_listing`, `environment`, `hook_output`, `hook_success`, `mcp_lazy_tools_delta`, `agent_listing_delta`, `session_context`), `system`, `active-leaf`, `last-prompt`, `runtime-config`, `workspace-directories`.
- **Subagents**: nothing of the subagent's conversation lands in the main transcript (every main record had `isSidechain: false`); the main transcript only shows the `Agent` `tool_use` and its `tool_result`. The subagent file's first record is a `user` record with `origin.kind: "human"` and `humanInput`, but **`isSidechain: true`**, `isDelegatedTask: true` and `agentId`.
- **Compaction**: the transcript is not rewritten. `/compact` appends a `system` record (`subtype: "compact_boundary"`, `compactMetadata: {trigger, preTokens, postTokens, messagesSummarized, preservedSegment}`), the summary `user` record, and replays of the session's prefix attachments marked `isCompactionPrefix: true`. A record-index cursor survives. `PreCompact.custom_instructions` carries the `/compact` argument; `PostCompact.compact_summary` carries the summary text. Auto-compaction (`trigger: "auto"`) is pending.
- **Resume** (`-c`): same `session_id`, same file, new records appended after the first run's.
- **Flush timing: complete at `Stop`** (verified): at `Stop` the file already held the final assistant text (`last_assistant_message`) and had the same length and line count at `SessionEnd`; only the trailing `last-prompt` / `workspace-directories` / `runtime-config` records are written after that, at process exit.

## 9. MCP and skills

**MCP.** The plugin's `.mcp.json` (falling back to `mcp.json`, *documented*) is started as a local stdio server from the cache copy. **Verified** shape:

```json
{"mcpServers": {"openviking": {"command": "node", "args": ["${QODER_PLUGIN_ROOT}/agent-integrations/qoder/servers/mcp-proxy.mjs"], "env": {"OPENVIKING_HOOK_SOURCE": "qoder"}}}}
```

- `${QODER_PLUGIN_ROOT}` is substituted in `args`, and both `${QODER_PLUGIN_ROOT}` and `${QODER_PLUGIN_DATA}` in `env` values; `QODER_PLUGIN_ROOT` is also in the server's own env. Run 11 saw the proxy connect (`initialize` from `clientInfo.name: "qodercli"`).
- The server name is **not prefixed with the plugin name**, and the fully-qualified tool name is `mcp__<server>__<tool>`. A user-scope MCP server with the same name would collide; which wins is pending.
- **Lazy loading is the effective default.** MCP tools are not in the model's tool list; the host exposes three meta tools `mcp_list` / `mcp_get` / `mcp_call` and announces the catalogue in an `mcp_lazy_tools_delta` attachment. The bundle enables lazy mode when `QODER_MCP_LAZY=1`, when `mcp.lazyLoad: true`, **or when no settings file declares `mcp.lazyLoad` at all**; the docs' "default `false`" does not hold for v1.1.88. Consequences:
  - `PreToolUse` sees `tool_name: "mcp_call"` (or `mcp_get`/`mcp_list`) with the real tool in `tool_input.toolName`, so a matcher such as `mcp__openviking__.*` does not fire in lazy mode. The documented `mcp_context` / `original_request_name` fields were not present on these calls.
  - The uri-guard is unaffected: its file-tool matcher (`Read|Write|Edit|Glob|Grep`) never needs to see MCP calls.
- In `-p` with `permission_mode: default`, `mcp_call` to a plugin tool was refused (`Allow MCP tool <server>/<tool>?`), and a `PreToolUse` allow did not change that. Headless memory-tool use needs a permission rule or a permissive mode; the rule syntax for MCP tools is pending. The TUI prompts the user.

**Skills.** `skills/<name>/SKILL.md` from an installed plugin is listed as **`<plugin>:<name>`** (the probe's `qx-probe:qx-hello`) in `init.skills`, in the slash-command list and in the turn's `skill_listing` attachment (`isInitial: true`), for both the installed copy and `--plugin-dir`. The model invokes it as `Skill {"skill":"<plugin>:<name>"}`; the body arrives as the `isMeta` user record of §8. The skill was already listed on the first turn.

## 10. Remaining unknowns

1. Whether host-queued `isMeta` prompts (`peer`, `task-notification`, `bridge`) fire `UserPromptSubmit`, and whether `bridge` (remote-control) prompts carry `humanInput`.
2. Auto-compaction (`trigger: "auto"`) and `SessionStart(clear)` / `SessionEnd(clear|resume)`; only manual `/compact` and `-c` were run.
3. Whether `SessionStart(resume)` `additionalContext` reaches the model (it is not recorded in the transcript; `startup` and `compact` outputs are).
4. `decision: "approve"`: undocumented here and untested; the adapter omits it.
5. The permission-rule syntax that lets `mcp_call` to the plugin's server run headless, and the precedence between a plugin MCP server and a same-named user server.
6. `async` / `asyncRewake` hooks: documented, unexercised; the detached writer is used instead.
7. Windows / PowerShell placeholder substitution: documented only.
8. Whether a Qoder IDE install creates `~/.qoder` too; the installer treats the directory as a sign of Qoder CLI.
9. Whether the CN region build (`~/.qoder-cn`) is distributed; against it the installer's cache cleanup would target `~/.qoder`.

## 11. Adapter mapping and v1 scope

| CodeBuddy piece | Qoder counterpart |
| --- | --- |
| `hosts/codebuddy.mjs` | `hosts/qoder.mjs`, prefix `qd-`, the same stage map: `session-start` → start, `user-prompt-submit` → prompt, `stop` / `pre-compact` / `session-end` → capture |
| `hosts/codebuddy-turns.mjs` | `hosts/qoder-turns.mjs`, decoding §8 into the incremental turns the dispatcher sends, with `memory-plugin-shared/lib/capture-utils.mjs` building the text and tool parts |
| `hosts/codebuddy/plugin.json` | `hosts/qoder/plugin.json`, copied to `.qoder-plugin/plugin.json` |
| `hosts/codebuddy/hooks.json` | `hosts/qoder/hooks.json`, with the `{ "hooks": … }` wrapper, a quoted `${QODER_PLUGIN_ROOT}` path and an explicit `timeout` on every hook |
| `hosts/codebuddy/.mcp.json` | `hosts/qoder/.mcp.json` |
| installer: local marketplace + `install name@marketplace` | the same, under `~/.openviking/agent-integrations/qoder/marketplace`. Because `marketplace add` copies the directory, the installer first removes any `openviking` marketplace (which also uninstalls a Claude Code plugin added to Qoder from the GitHub marketplace of that name) and the cached copy, then adds and installs. |
| uninstall also removes the host cache | the same: `plugins uninstall` leaves `cache/openviking/…` with `.orphaned_at` |
| harness key `codebuddy` in `HARNESS_KEYS` | `qoder` (`plugin.qoder.*`) |

Measured facts that shape the adapter and are not visible in the code:

- `prompt()` returns the payload's `prompt` trimmed; there is no continuation flag to gate on (§4). Prompt dedup falls back to the prompt hash within 500 ms.
- `envelope()` answers the `hookSpecificOutput` form on `session-start` and `user-prompt-submit`, without `decision`, and nothing on the write-path events (§5).
- `guard()` stays deny-only on the file tools, as on CodeBuddy. Qoder would also accept an advisory `additionalContext` on `allow` (§5), but v1 does not use it.
- The decoder reads only `user` and `assistant` records, skips `isSidechain`, `isMeta`, `isCompactSummary` and non-human `origin.kind`, folds a model response's per-block records (one `responseGroupId`) and the tool results answering it into one assistant turn, inlines tool calls with their input, keeps tool result bytes out, and drops `thinking`. A turn's id is the index of the record that opened it.
- `capture` treats the auxiliary `SessionEnd` (unknown session, missing transcript) as a no-op: it logs `capture_unreadable` and touches neither the network nor the hook state (§3, run 11).
- Commit policy as on CodeBuddy: `Stop` commits every `commitTurnThreshold` captured turns; `PreCompact` (unless `autoCommitOnCompact` is off) and `SessionEnd` commit whatever is still uncommitted, including turns an earlier `Stop` sent.

v1 scope matches the CodeBuddy adapter: no subagent capture (events and per-agent transcripts exist, §3/§8), no resume/compact archive injection, no keyword capture mode, no local recall compressor default.
