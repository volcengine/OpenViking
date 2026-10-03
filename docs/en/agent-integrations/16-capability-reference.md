<a id="compare-agent-integration-capabilities"></a>

# Agent integration capabilities

This page compares what each OpenViking agent integration does after it is installed. Use it to answer three questions before you rely on an integration:

- Which OpenViking tools can the agent call by itself?
- What happens without the agent asking: recall before each prompt, context at session start, capture of the conversation, and the commits that turn it into long-term memory?
- What is kept and what is lost when the agent exits, crashes, compacts its context, or cannot reach the server?

For installation, start with [Choose an integration](./01-overview.md). The comparisons below describe the implementations linked in [Source notes](#source-notes); your server determines the MCP tools available to an integration.

## Two kinds of capability

**Agent-callable tools** are tools the model decides to call, such as `search`, `read`, or `remember`. Nothing happens until the model calls one. A plain MCP connection gives you only these.

**Automatic lifecycle behavior** runs from host events without the model asking. It covers recall before a prompt reaches the model, profile and skill context at session start, capture of each turn into an OpenViking session, and commits. Hooks, plugins, and native extensions provide it.

Long-term memories are extracted from a session only after a **commit**. Captured messages that have not been committed stay in the server session and do not become memories until a later commit. [Capture and commits](#capture-and-commits) explains when each integration commits and what an exit can leave behind.

<a id="reading-guide"></a><a id="_1-capability-overview"></a><a id="_1-2-automatic-hook-surface-driven-by-the-harness"></a><a id="_1-3-grouping-by-form"></a>

## At a glance

### Agent-callable tools

"Server MCP tools" means whatever the server's `tools/list` returns: 20 tools on the current server, listed in [Server MCP tools](#server-mcp-tools).

| Integration | How it connects | Tools the agent can call |
|---|---|---|
| [Claude Code](#claude-code) | Plugin with hooks and an MCP proxy | Server MCP tools |
| [Codex, TraeCode CLI 2.0](#codex-and-traecode-cli-2-0) | Codex plugin with hooks and an MCP proxy | Server MCP tools |
| [Cursor](#cursor) | Hook and MCP configuration, plus a rule | Server MCP tools |
| [TRAE, TRAE CN](#trae-and-trae-cn) | Hook and MCP configuration | Server MCP tools |
| [ZCode](#zcode) | Hook and MCP configuration | Server MCP tools |
| [Kimi Code](#kimi-code) | Kimi Code plugin with hooks and an MCP proxy | Server MCP tools |
| [OpenCode](#opencode) | npm plugin that adds an MCP entry | Server MCP tools, named `openviking_<tool>` |
| [DSH](#dsh) | In-process Cordis plugin plus an MCP proxy | Server MCP tools, named `mcp__openviking__<tool>` |
| [pi](#pi) | Native extension with a built-in MCP client | Server MCP tools, registered as `openviking_<tool>` after the MCP handshake succeeds |
| [OpenClaw](#openclaw) | Context-engine plugin | 15 native tools, 14 enabled by default |
| [Hermes](#hermes) | Hermes memory provider | 6 native `viking_*` tools |
| [MCP clients, Agent Plugins](#other-integrations) | MCP only | Server MCP tools |
| [ov CLI](#ov-cli) | Command-line client | Every operation as an explicit command; no agent involved |

### What runs automatically

| Integration | Recall before each prompt | Session-start context | Commits during a session | Commits at a normal exit | Host compaction |
|---|---|---|---|---|---|
| Claude Code | Yes, session-aware | Profile, memory index, skills | At 20,000 pending tokens | Yes | Commits, then the host summarizes |
| Codex | Yes, session-aware | Profile, memory index, skills | At 20,000 pending tokens | Codex 0.145+; otherwise at the next start | Commits, then the host summarizes |
| TraeCode CLI 2.0 | Yes, session-aware | Profile, memory index, skills | At 20,000 pending tokens | Only if the build has `SessionEnd`; otherwise at the next start | Same as Codex |
| Cursor | Yes, session-aware | Profile, memory index, skills | Every 8 captured messages | No | Commits, then the host summarizes |
| TRAE, TRAE CN | Yes, session-aware | Profile, memory index, skills | Every turn | No exit event; turns are already committed | No pre-compaction event |
| ZCode | Yes, session-aware | Profile, memory index, skills | Every turn | No exit event; turns are already committed | No pre-compaction event |
| Kimi Code | Yes, session-aware | Profile, memory index, skills, at the first prompt | Every 8 captured messages | Only when `SessionEnd` captures new messages | Commits newly captured messages |
| OpenCode | Yes, session-aware | Profile, memory index, skills, indexed repositories | At 20,000 pending tokens, when idle | Yes, within the host's cleanup time | Commits around compaction |
| DSH | Yes, session-aware | Profile, memory index, skills, once per session | At 20,000 pending tokens | Yes, with a 3-second budget | Not observed |
| pi | Yes, session-aware | Profile, memory index, skills, every turn | Takeover on: about 30,000 tokens; off: 20,000 | Takeover on: no; off: yes | Takeover replaces pi's summary |
| OpenClaw | Yes, not session-aware | None | At half the token budget (64,000 by default) | No | The plugin owns compaction |
| Hermes (bundled) | Yes, session-aware with a fallback | Profile and memory listings | No; only at session boundaries | Yes, if pending uploads finish within 10 seconds | Commits at fork-style compaction |
| ov CLI | No | No | Only `ov session commit` | Not applicable | Not applicable |

How to read this table:

- **Session-aware recall** sends the session ID so the server can use the conversation to interpret the query. The shared plugins’ context mode also expands the query and tracks recently injected memories to avoid repeating them. Hermes uses list mode, which has no injection ledger; OpenClaw uses `/find`, which has no session field. See [How recall reaches the server](#how-recall-reaches-the-server) for the request paths and fallbacks.
- **Pending tokens** are server-side counts of captured but uncommitted messages. Most thresholds are client settings; [the commit table](#when-each-integration-commits) lists them.
- **Commits at a normal exit** means the integration sends a commit request. Network errors, host exit budgets, or process termination can still interrupt it. [What happens at exit](#what-happens-at-exit) covers Ctrl+C, signals, and crashes.

<a id="_1-1-active-tool-surface-agentic-calls"></a><a id="_2-shared-capability-core"></a>

## Tools the agent can call

<a id="_2-1-server-side-mcp-tool-surface"></a>

### Server MCP tools

Every MCP-based integration exposes the same server-defined tools and keeps no catalog of its own. A local proxy, or pi's built-in MCP client, reads `~/.openviking/ovcli.conf`, connects to the server's `/mcp` endpoint, and passes through what `tools/list` returns. A server upgrade can add tools without a plugin release; reconnect the client to see them. Parameters for each tool are in [MCP tools and protocol](../guides/06-mcp-integration.md#available-mcp-tools).

| Group | Tools |
|---|---|
| Search and browse | `find`, `search`, `read`, `list`, `tree`, `grep`, `glob` |
| Write | `remember`, `write`, `edit`, `add_resource`, `add_skill` |
| Delete | `forget` |
| Watched resources | `list_watches`, `cancel_watch` (not yet available on the commercial edition) |
| Accounts and access control | `list_users`, `list_groups`, `get_acl`, `set_acl` |
| Status | `health` |

Behavior to know before relying on a tool:

- **`remember`** creates a one-shot session named `mcp-store-<id>`, adds the messages, and commits it at once. It is the only MCP tool that commits. No MCP tool commits the agent's current conversation; that is the job of the automatic hooks.
- **`find` and `search`**: `find` is a fast search without session context. `search` can take a `session_id` and run intent analysis (`retrieval.enable_intent`, on by default). `find` with `context_type="skill"` returns one hit per skill package, pointing at its `SKILL.md`, from both the user's and the account's skills.
- **`write` and `edit`** can write only under `viking://resources`, `viking://user`, and `viking://agent`. A new file must end in `.md`, `.txt`, `.json`, `.yaml`, `.yml`, `.toml`, `.py`, `.js`, or `.ts`. The user's `skills/`, `peers/`, `privacy/`, and `sessions/` directories are read-only. A write under `viking://agent/skills` is accepted but skips skill installation, so use `add_skill` for skills.
- **`add_resource` with a local path** returns a signed upload URL (valid for 600 seconds by default). The model must upload the file to it, for example with a shell command; ingestion then starts on its own. Remote URLs are ingested directly.
- **`forget`** deletes whatever URI it is given, non-recursively by default. It does not check whether the URI is a memory, resource, or skill. See [Write and delete limits](#write-and-delete-limits).
- **Schemas** are flattened at server start so that clients accepting only the OpenAPI 3.0 subset, such as Gemini, can load them. Validation still follows the original signatures; `read`, for example, also accepts a single string. Every client receives the same schema.
- **Identity** is resolved the same way as for REST requests. See [Authentication](../guides/04-authentication.md).

Host differences:

- **OpenCode** prefixes the tools with `openviking_`. **DSH** names them `mcp__openviking__<tool>`.
- **DSH** runs one MCP proxy per DSH profile. Tool calls therefore carry a process-level actor peer, and `remember` is not tied to the DSH session. Recall, capture, and commits still resolve a peer per session.
- **pi** registers the tools only when the session is not bypassed, the health check passes, the OpenViking session exists, and the `/mcp` handshake returns a tool list. If the handshake fails, that session has no OpenViking tools, but recall, sync, and takeover continue, and a later turn retries the handshake. `mcpEnabled: false` turns the bridge off. A root API key is refused on `/mcp` with 403, so a credential chain that ends at `ov.conf`'s `server.root_api_key` produces a session without tools.
- **Codex** passes its MCP server only the environment variables listed in `.mcp.json` `env_vars`.

### OpenClaw tools

OpenClaw registers its own tools instead of passing MCP tools through.

| Group | Tools | Notes |
|---|---|---|
| Memory | `memory_recall`, `memory_store`, `memory_forget` | `memory_recall` searches memory; `memory_store` commits through a temporary session |
| Resources and skills | `ov_search`, `ov_read`, `ov_multi_read`, `ov_list`, `add_skill`, `add_resource` | `ov_search` covers resources and the user's skills by default. `add_resource` is off by default and also needs `enableAddResourceTool` |
| Session archives | `ov_archive_search`, `ov_archive_expand` | Search and expand archived history |
| Large tool output | `openviking_tool_result_read`, `openviking_tool_result_search`, `openviking_tool_result_list` | Read tool output the server stored separately |
| Diagnostics | `ov_recall_trace` | Shows how automatic recall was built |

### Hermes tools

Hermes has six tools: `viking_search`, `viking_read`, `viking_browse`, `viking_remember`, `viking_forget`, and `viking_add_resource`. There is no skill tool. `viking_remember` submits a fact through its own session and returns before extraction finishes; extraction may add, merge, or skip the fact. See [Hermes](#hermes) for the differences between the bundled provider and the external plugin.

<a id="_3-5-type-boundaries-for-writes-and-deletes"></a><a id="_3-5-1-write-boundary"></a><a id="_3-5-2-delete-boundary"></a>

### Write and delete limits

The server applies the same checks to every delete, whichever tool or command sends it:

- The caller needs manage permission on the URI, and a user whose own deletion is in progress is refused.
- `viking://`, `viking://user`, and `viking://agent` cannot be deleted. Deleting `viking://resources` or a whole user root such as `viking://user/<id>` requires the ROOT role. Non-root callers cannot write to the `viking://temp` root.
- URIs hidden by the caller's actor-peer view are refused.

These checks protect namespaces, not content types. Restrictions by type come from the client:

| Entry point | Can delete | Client-side limits |
|---|---|---|
| MCP `forget` (every MCP integration, including DSH and pi) | Any URI it is given | Non-recursive unless `recursive=true`; no type or score check |
| `ov rm` | Any URI | No confirmation; `-r` deletes recursively |
| `ov tui`, `d` key | Any URI except roots and scope directories | Asks for confirmation |
| OpenClaw `memory_forget` | Memory files only | URI must match a user, peer, or agent memory path. A search-based delete runs only for a single candidate scoring at least 0.85; otherwise the candidates are listed. Always non-recursive |
| Hermes `viking_forget` | One user memory `.md` file | Rejects directories, non-`.md` files, generated `.abstract.md` and `.overview.md` files, and URIs with a query or fragment |
| LangChain `viking_forget` | Any URI | Exposed only with `profile="admin"` or `allow_forget=True`; the model controls `recursive` |
| Open WebUI | Nothing | No delete tool |

Skills follow their own path. Create, install, or replace them with the MCP `add_skill` tool, OpenClaw's `add_skill`, `ov add-skill`, or `POST /api/v1/skills`; `add_resource` refuses skill URIs. Remove a skill with `ov skills remove` or `DELETE /api/v1/skills/{name}`. MCP `forget` and `ov rm` can delete a skill directory, but they leave the skill's privacy configuration behind. See [Skills](../api/04-skills.md).

<a id="_3-dimensions-in-detail"></a><a id="_3-2-automatic-recall-and-injection"></a>

## Automatic recall and session-start context

<a id="_3-2-1-mechanism-foundation-one-shared-pipeline-two-server-side-paths"></a>

### How recall reaches the server

Every integration except OpenClaw and Hermes uses the shared plugin code, which tries up to three requests:

1. **Context search.** `POST /api/v1/search/search` with `mode: "context"`, `purpose: "coding"`, and the session ID. Budgets, quotas, query expansion, and digest options are sent only when you configure them; otherwise the server defaults apply, including a 1,600-token injection budget (`max_tokens`).
2. **Legacy recall.** If the server rejects context mode as an older version, the plugin records that in `~/.openviking/state/context-face.json` for six hours and calls `/api/v1/search/recall` instead. The file is shared by every integration on the machine, so one integration's result applies to all of them.
3. **Plain find.** As a last resort the plugin runs `find` on `viking://~/memories` and `viking://~/skills`, ranks the results locally, and fills its own token budget. `recallTokenBudget`, `recallMaxContentChars`, and `recallPreferAbstract` apply only at this step.

The shared-plugin recall pipeline excludes resources; the model searches resource documents by calling `search` itself. Hermes can include resources when its resource-recall option is enabled.

The server handles a session ID in two ways:

- **Context mode** (context search and legacy recall) expands the query from the conversation and keeps a per-session ledger of injected memories. Expansion needs `retrieval.enable_intent` (on by default), a session that already has messages, and either an archive overview or current messages. The original query stays first, followed by up to three planned queries.
- **List mode** (the default when `mode` is omitted) lets intent analysis replace the query, so the original query may not survive, and it skips the ledger. Codex's second fallback, Hermes's `viking_search(mode="deep")`, and Hermes's preferred recall path land here. They send a session ID but do not get expansion or deduplication.

Cross-turn deduplication (`dedup_turns`):

- The server default in context mode is **0**, which disables it. The shared plugins send 5 (`recallDedupTurns`). A direct API caller must send `dedup_turns` itself, even with a session ID.
- The window counts messages, not conversation rounds. When both user and assistant messages are captured, 5 covers about two rounds.
- With automatic capture off and recall on, the message count never moves, so a memory already injected stays suppressed for the rest of the session. Set `OPENVIKING_RECALL_DEDUP_TURNS=0` to turn deduplication off.

<a id="_3-2-2-decision-matrix"></a>

### Recall by integration

The table shows the default path. Turning on [recall digests](#recall-digest) changes the request.

| Integration | Runs on | Query | Where the context goes |
|---|---|---|---|
| Claude Code | Every `UserPromptSubmit` | The prompt, trimmed | `<openviking-context>` in `additionalContext` |
| Codex, TraeCode CLI 2.0 | Every `UserPromptSubmit`; the whole hook has a 120-second deadline | The prompt | `<openviking-context source="auto-recall">` |
| Cursor | `beforeSubmitPrompt` | The prompt; repeated events within 500 ms reuse the previous result | `additional_context` |
| TRAE, TRAE CN | `UserPromptSubmit` | The prompt, with earlier injected blocks removed | `additionalContext` |
| ZCode | `UserPromptSubmit` | The prompt, with three kinds of injected block removed, including `<system-reminder>` | `additionalContext`, strict JSON |
| Kimi Code | `UserPromptSubmit` | The prompt | Plain context text, not JSON |
| OpenCode | v1: every `chat.message`; v2: every prompt | Text parts of the message | v1 prepends a synthetic part. v2 stores the result in message metadata and injects it before that message at each model step, without a new request |
| DSH | `agent/pre-step` | Every message in the claimed batch, minus its own injected content | Appended as a user message |
| pi | Queued at `before_agent_start`, run in the `context` event | The prompt | Prepended to the last real user message, so this turn's prompt gets this turn's memories |
| OpenClaw | Context assembly | Last user message, cleaned and cut to 4,000 characters | `<relevant-memories>` prepended to the last user message |
| Hermes | Before every model call | User input of 5 characters or more, with skill scaffolding removed | `<memory-context>` appended to the current user message in the request only; never stored |

<a id="_3-2-3-profile-opening-injection"></a>

### Session-start context

Integrations that use the shared plugin code inject one `<openviking-context>` block at session start. It holds the user profile (`viking://user/<space>/memories/profile.md`), an index of the `preferences/` and `entities/` memories, and an `<available-skills>` catalog:

```text
<openviking-context source="startup">
<user-profile uri="viking://user/default/memories/profile.md">...</user-profile>
<available-memories>...</available-memories>
<available-skills>
  OpenViking skills (stored in OpenViking, not local files). Before following one, read <dir>/<name>/SKILL.md with the OpenViking read tool.
  viking://user/default/skills/
    - pr-review — Review a pull request against the team checklist.
  viking://agent/skills/
    - deploy-runbook — Shared deployment runbook for the payments service.
</available-skills>
</openviking-context>
```

When each integration injects it:

| Integration | When |
|---|---|
| Claude Code | `SessionStart`, every source |
| Codex | `SessionStart` on startup, clear, and resume |
| Cursor, TRAE, TRAE CN, ZCode | `SessionStart` |
| Kimi Code | The first prompt; retried on later prompts until it succeeds |
| OpenCode | The first message of each session; not retried after a failure, and skipped for subagent sessions. The list of indexed repositories also goes into the system prompt |
| DSH | Once per session; not sent again after compaction |
| pi | In the system prompt, rebuilt every turn |
| OpenClaw | Not injected |
| Hermes | Its own reader loads the profile and the preferences and entities listings, with a 6,000-token default budget |

Budgets and limits:

- `profileTokenBudget` (default 10,000 tokens) covers the profile and the memory index. The profile gets half. An oversized profile keeps its first 8 lines and its end; an oversized listing ends with `... +N more`. Token estimates count CJK characters as 1.5 tokens and other text as four characters per token.
- `skillCatalogTokenBudget` (default 1,200 tokens, `OPENVIKING_SKILL_CATALOG_TOKEN_BUDGET`) is a separate budget for the skill catalog. `skillCatalog` (`OPENVIKING_SKILL_CATALOG`) or a budget of 0 turns the catalog off.
- The catalog comes from one `GET /api/v1/skills` request. The user's own skills come first, and an account skill with the same name as a user skill is left out. Each description is cut to about 40 tokens. If descriptions do not fit, the catalog lists names only; if names do not fit, it ends with `... +N more`, or shrinks to a one-line count. Servers without that endpoint get no catalog.
- `sessionStartMaxBytes` caps the whole block in UTF-8 bytes: 9,500 for Claude Code and Codex, whose hosts move hook output above about 10,000 characters into a file and show only a preview; 20,000 for ZCode, which drops output above 32 KB; no cap elsewhere. Above the cap, the memory index is dropped first, then the skill catalog.

On resume, some integrations also inject the previous archive summary, with a 32,000-token budget: Claude Code on resume and compact, Codex on resume after its local session was cleared, OpenCode at session start, and pi when takeover is off. Claude Code and Codex skip a profile block identical to the one the session already received.

<a id="_3-2-4-timeout-and-budget-chain"></a>

### Timeouts and budgets

The server stops query expansion after 5 seconds (`retrieval.recall_intent_timeout_s`) and digest rewriting after 30 seconds (`retrieval.recall_rewrite_timeout_s`). The shared plugins raise their context-request timeout to at least 15 seconds when expansion is on and at least 45 seconds when a digest is requested, so the client waits for every server stage. A host deadline can still end the hook first. To change these values, see [Tune recall latency](./19-recall-tuning.md#request-timeout).

| Integration | Recall timeout |
|---|---|
| Claude Code | 15 seconds, inside a 60-second hook limit |
| Codex | A 120-second deadline for the whole hook, including a local compressor of up to 110 seconds |
| Cursor, TRAE, TRAE CN, ZCode | 15 seconds, inside a 20-second host limit |
| OpenCode | 30 seconds |
| DSH | 10 seconds, raised to at least 15 with query expansion. Recall blocks the pre-step |
| pi | 15 seconds |
| OpenClaw | 5 seconds for the whole recall, including a 500 ms health check. With the default `recallPreferAbstract=false`, each memory costs an extra read, which allows at most one find and six reads |
| Hermes | 4 seconds in total and 3 seconds per request; configurable |

OpenClaw and Hermes limit injected recall to 4,000 characters and skip an entry that does not fit instead of cutting it.

<a id="_3-2-5-recall-digest"></a>

### Recall digest

A recall digest asks a model to rewrite recall results into a short list of cited points before they reach the agent.

**On the server.** Context search accepts `rewrite` (`false`, `true`, or `"auto"`; default `false`) and `rewrite_max_bullets` (default 6, range 1–20). The server uses the `query_planner` model; with `rewrite=true` and no `query_planner`, it falls back to the main `vlm`, while `"auto"` runs only when `query_planner` is configured. The digest starts with `OpenViking memory digest:` and has one `- ` bullet per point. Each bullet must be at most 500 characters and cite a `viking://` URI from the results; bullets without a valid citation are dropped. If the model finds nothing relevant, the server returns `stats.rewrite="no_relevant"` and an empty block, and that turn is not recorded in the deduplication ledger. After 30 seconds the server returns the unrewritten block.

**In the plugins.** `OPENVIKING_RECALL_COMPRESS`, or `recallCompress` in the shared settings, picks the mode. `recallRewrite` is still accepted as an alias.

| Mode | Behavior |
| --- | --- |
| `off` | No digest |
| `server` | Send `rewrite: true` and prefer the server digest |
| `client` | Use a local compressor; Claude Code and Codex only |
| `auto` | Claude Code and Codex use a local compressor when one is available and otherwise send `rewrite: "auto"`. Other integrations send `rewrite: "auto"` |

Claude Code and Codex default to `auto`; the other integrations default to `off`. Server digests are supported by Claude Code, Codex, OpenCode, DSH, pi, Cursor, TRAE, TRAE CN, ZCode, OpenClaw, and the Hermes external plugin, and need a server with context-search rewrite support. The bundled Hermes provider does not support recall digests. The legacy values `1` and `0` mean `auto` and `off`. A `no_relevant` result suppresses injection; the plugin must not fall back to the raw block.

Local compressors:

- **Claude Code** checks for `claude` with `claude --version` (cached for 7 days) and runs `claude -p --model sonnet --effort low --strict-mcp-config`. The subprocess timeout defaults to 110 seconds (`recallCompressTimeoutMs`), but the 60-second prompt hook can end it sooner. Inputs under 1,500 characters are not compressed. Cited URIs are matched back to real result URIs by edit distance, and bullets that cannot be matched are dropped. On failure the plugin injects locally formatted results, or the rendered block.
- **Codex** picks a model from `~/.codex/models_cache.json` (`gpt-5.3-codex-spark` first, then `gpt-5.6-luna` with low effort; cached for 7 days) and runs `codex exec` in a read-only, ephemeral sandbox with a 110-second default timeout. One runtime failure disables local compression until the next `SessionStart`. Output is capped at 4,000 characters; failures fall back as for Claude Code.

These settings apply to automatic recall only. An explicit MCP `search` call uses the arguments the model passes. For older-server fallbacks and host time budgets, see the [shared plugin documentation](https://github.com/volcengine/OpenViking/blob/main/examples/memory-plugin-shared/README.md#cloud-recall-compression) and [Tune recall latency](./19-recall-tuning.md).

<a id="_3-2-6-injection-backflow-protection"></a>

### Keeping injected context out of capture

Injected context is wrapped in fixed tags such as `<openviking-context>`, and capture removes those blocks before sending a turn, so recalled memories are not stored again as new conversation. The shared capture code also removes digest blocks, metadata fences, and timestamp prefixes. TRAE and ZCode use their own cleaning functions; ZCode's removes three kinds of injected block. OpenClaw removes `<relevant-memories>` both when it captures a turn and when it builds the next recall query. Hermes drops the calls and results of its three read-only tools from the captured batch and keeps write-tool calls.

<a id="_3-3-session-and-commit-lifecycle"></a>

## Capture and commits

<a id="_2-3-server-side-session-and-commit-semantics"></a><a id="_3-3-1-mechanism-foundations"></a>

### How server sessions and commits work

- **Sessions are created implicitly.** The server creates a session when it receives the first message for it, or on the first context-mode recall with that session ID. DSH is the only integration that creates its sessions explicitly.
- **A commit has two phases.** `POST /api/v1/sessions/{id}/commit` returns after phase 1 archives the messages. Its response includes a `task_id` for phase 2, memory extraction, which runs in the background. A successful commit response does not mean extraction has finished.
- **`keep_recent_count`** sets how many recent messages a commit leaves live in the session. The server default is 0, which archives everything. Claude Code, Codex, OpenCode, and DSH send 10 on threshold commits; Cursor, TRAE, TRAE CN, ZCode, and Hermes send 0; pi sends the exact message count of its last 3 user turns in takeover mode and 10 otherwise; OpenClaw sends 10 on its threshold commit and 0 on reset, `memory_store`, and compaction.
- **Server auto-commit is off by default.** `memory.session_auto_commit.enabled` defaults to `false`, and the idle scanner does not start while it is off. A new session can still get a policy from `server.user_config_defaults.auto_commit_policy`, or explicitly through `POST /api/v1/sessions`, `PATCH /api/v1/sessions/{id}/config`, the SDK, or `ov session new --auto-commit-policy-json` and `ov session config set`. A policy's defaults are 150,000 pending tokens (strictly greater than), 100 messages, an 86,400-second idle timeout, `keep_recent_count` 0, and no minimum interval. The idle timeout also needs `memory.session_auto_commit.enabled=true`. The memory plugins send no policy, so without one of these settings the client is the only thing that commits.
- **Writes are batched.** The shared plugins send up to 100 messages per `POST /messages/batch`, matching the server limit, and fall back to one message at a time when the batch endpoint returns 404 or 405.
- **Large tool output is stored separately.** The server moves tool output above 20,000 characters into a separate record and leaves a `tool_output_ref`. Plugins raise their own limit (`captureToolMaxChars`) to 1,000,000 only as a safety net.
- **Some writes run in the background.** Claude Code, Codex, and ZCode hand the `Stop` write to a detached worker by default (`OPENVIKING_WRITE_PATH_ASYNC`), so the host does not wait. The worker survives the terminal closing once it has started, but network errors or process termination can still interrupt it. While this is on, `Stop` no longer prints the `appended N turn(s)` notice.

<a id="_3-3-2-regular-commit-triggers"></a>

### When each integration commits

Thresholds in this table are client-side. They read the server's pending-token count unless noted.

| Integration | During the session | On an explicit action or boundary | Before or after compaction |
|---|---|---|---|
| Claude Code | `Stop` at 20,000 pending tokens | `SessionEnd` and `SubagentStop` always commit; `SessionStart` replays queued writes | `PreCompact` always commits, synchronously |
| Codex | `Stop` at 20,000 pending tokens | `SessionEnd` (Codex 0.145+) catches up missed turns, then commits in a detached worker. `SessionStart` on startup or clear commits sessions marked as ended or idle for more than 30 minutes | `PreCompact` catches up and commits everything |
| TraeCode CLI 2.0 | Same as Codex | Same as Codex; without `SessionEnd`, only the start-up sweep | Same as Codex |
| Cursor | `stop` after 8 captured messages since the last commit (`commitTurnThreshold`), counted locally | `sessionEnd` is registered but does not run in practice | `preCompact` always commits |
| TRAE, TRAE CN | Every `Stop` that captured content | None | No pre-compaction event |
| ZCode | Every `Stop`; turns a missed `Stop` skipped are caught up from the rollout file at the next `Stop` | None | No pre-compaction event |
| Kimi Code | `Stop` after 8 captured messages since the last commit | `SessionEnd` and `Interrupt` commit when they capture new messages | `PreCompact` commits when it captures new messages |
| OpenCode | v1 `session.idle`, v2 end of execution: at 20,000 pending tokens | Session deletion, v1 `session.error`, v1 dispose, and v2 cleanup force a commit | v1 before and after compaction; v2 once after compaction ends |
| DSH | `turn/end` at 20,000 pending tokens | Cordis teardown commits each session | None |
| pi, takeover on (default) | Locally estimated 30,000 tokens with more than 3 user turns | `/viking commit` | `session_before_compact` |
| pi, takeover off | 20,000 pending tokens after each sync | `session_shutdown` and `/viking commit` | `session_before_compact` |
| OpenClaw | After a turn, at `tokenBudget × commitTokenThresholdRatio` (128,000 × 0.5 by default) | `/new`, `/reset`, and `memory_store` commit and wait | `compact()` commits and waits up to 5 minutes for extraction |
| Hermes (bundled) | None | Session end, session switch (`/new`, `/resume`, `/branch`, fork-style compaction), gateway cache eviction, and an exit handler; `/undo` and in-place compaction do not commit | At fork-style compaction only |
| ov CLI | None | `ov session commit`; `ov add-memory` creates, fills, and commits its own session | Not applicable |

<a id="_3-3-3-shutdown-method-×-harness-outcome-matrix"></a>

### What happens at exit

"Commits" means the exit path sends a commit request. "Conditional" depends on the note in the last column. Messages that were already written stay in the server session and are archived by the next commit of the same session. The table assumes server auto-commit is off.

| Integration | Normal exit | Ctrl+C | SIGTERM | Terminal or window closed | `kill -9` or crash | How the rest is recovered |
|---|---|---|---|---|---|---|
| Claude Code | Commits | Commits | Commits | Commits | No | `SessionEnd` hands the commit to a detached worker in its own process group, which SIGHUP does not stop. Otherwise the next threshold, `/compact`, or `SessionEnd` |
| Codex | Commits | Conditional | No | No | No | A double Ctrl+C quits cleanly and fires `SessionEnd`; a single one does not. Anything missed is committed at the next `SessionStart` on startup or clear: at once if the end marker survived, otherwise after 30 minutes idle |
| TraeCode CLI 2.0 | No, unless the build has `SessionEnd` | No | No | No | No | The 30-minute idle sweep at the next `SessionStart` |
| Cursor | No | No | No | No | No | Closing or switching a chat fires no event. `sessionEnd` fires only on window close, after the host has already stopped running hook commands. Messages below the 8-message threshold wait for later messages in the same session |
| TRAE, TRAE CN | No | No | No | No | No | Every `Stop` already committed, so at most the turn in progress is lost |
| ZCode | No | Conditional | No | No | No | Ctrl+C after that turn's `Stop` fired lets the detached worker finish. Every `Stop` commits, and missed turns are caught up at the next `Stop` |
| Kimi Code | Conditional | Conditional | Not verified | Not verified | No | `SessionEnd` and `Interrupt` commit only when they capture new messages; a tail already captured by `Stop` waits for the next commit |
| OpenCode | Conditional | Conditional | Conditional | Conditional | No | v1 1.15.11+ `dispose` and v2 cleanup commit every session, but host cleanup time is limited, so slow requests or many sessions can be cut off. v2 cleanup also runs after 60 minutes idle and on plugin hot reload |
| DSH | Commits | Commits | Commits | No | No | Teardown gives each session one 3-second commit, queued behind any slow write, inside a 5-second process grace period. A second Ctrl+C force-quits without it. Closing a browser tab in the web form does not tear down |
| pi, takeover on | No | No | No | No | No | `session_shutdown` saves takeover state but does not commit. The next run that reaches the threshold, or `/viking commit` |
| pi, takeover off | Commits | Commits | Commits | Commits | No | A failed commit goes to the offline queue |
| OpenClaw | No | No | No | No | No | The `session_end` handler does not commit. Archiving relies on `/new`, `/reset`, and the threshold |
| Hermes (bundled) | Conditional | Conditional | Conditional | Conditional | No | The exit handler drains uploads for up to 10 seconds, then commits; if the drain does not finish, it skips the commit instead of committing half a session. SIGTERM and SIGHUP get a 1.5-second grace period by default, and the Hermes exit watchdog can cut a slow commit short |
| LangChain, Open WebUI, Agent Plugins, MCP clients | No | No | No | No | No | No session hooks. The `DELETE /mcp` an MCP proxy sends on exit only closes the protocol session. LangChain relies on the caller's `close()` |

What this means in practice:

- **Commits at a normal exit:** Claude Code, Codex 0.145+, OpenCode, DSH, pi with takeover off, and Hermes. The others rely on the recovery in the last column.
- **No integration commits after `kill -9`.** Written messages stay live until the next commit of that session. A server auto-commit policy with an idle timeout is the only server-side fallback, and the plugins do not configure one.
- **TRAE, TRAE CN, and ZCode** have the simplest exit behavior because every turn commits, at the cost of a full archive and extraction on every `Stop`.

<a id="_3-3-4-pending-queue-offline-compensation-comparison"></a>

### Offline retries

| Integration | What happens when a write fails |
|---|---|
| Claude Code, Cursor, TRAE, TRAE CN, ZCode, Kimi Code, OpenCode, DSH, pi | Retryable failures go to an on-disk queue under `~/.openviking/pending`. It is replayed at session start: up to 50 entries per run and 3 attempts per entry, kept for 7 days. Network errors, 408, 429, and 5xx are retryable; other 4xx responses, including 401 and 403, are not queued. A failed message stops the replay so order is kept |
| Codex, TraeCode CLI 2.0 | New captures are not queued. The transcript cursor moves only past messages the server accepted, so the next capture or start-up sweep resends the rest. `SessionStart` still replays queued entries |
| OpenClaw | No queue. A failed turn is not sent again |
| Hermes (bundled) | Uploads run in in-process threads and are not replayed from disk. Pending-commit markers under `$HERMES_HOME/openviking/pending_sessions/` let a later start commit sessions left by a dead run (POSIX only) |
| LangChain | A failed commit is retried on the next record, in memory only. A partial write raises `OpenVikingPartialWriteError` with counts that let the caller retry the remaining messages |
| Log ingestion | A SQLite cursor store records each batch before sending. After a crash, the next run checks the server's message count to decide whether the batch arrived |

<a id="_3-3-5-subagent-session-comparison"></a>

### Subagents

| Integration | Subagent handling |
|---|---|
| Claude Code | Each subagent gets its own session, `cc-<id>__subagent-<agent_id>`. `SubagentStop` sends its transcript and commits |
| Codex, TraeCode CLI 2.0 | Subagent output is folded into the main session |
| OpenCode | Subagents get `oc-<parent>__subagent-<child>` sessions. Session-start context is skipped for them; recall is not |
| DSH | Each subagent is a separate `dsh-<id>` session with no link to its parent, and each gets its own session-start context |
| Hermes | Delegated tasks run with `skip_memory=True`, so the subagent has no OpenViking session, recall, or tools, and its output is not captured |
| Cursor, TRAE, ZCode, Kimi Code, pi, OpenClaw | No special handling. A subagent with its own session ID gets its own session; otherwise its messages join the main session. OpenClaw can exclude sessions with `bypassSessionPatterns` |
| Log ingestion | The Claude Code adapter skips sidechain and meta records, so subagent conversations are not imported |

<a id="_3-4-compaction-takeover"></a><a id="_3-4-1-decision-matrix"></a>

## Host compaction

When the host shortens its context, most integrations make sure the dropped messages are committed and leave the summary to the host. pi and OpenClaw can replace the host's summary with OpenViking's archive.

| Integration | Approach | Before compaction | After compaction |
|---|---|---|---|
| Claude Code | Host summarizes | `PreCompact` commits synchronously; this is the one write that never runs in the background, because the host rewrites the transcript right after | `SessionStart` with `source="compact"` injects the archive overview and up to 5 summaries |
| Codex, TraeCode CLI 2.0 | Host summarizes | `PreCompact` catches up uncaptured turns, commits everything, and starts a new OpenViking session. If the catch-up is incomplete, it does not commit and retries later | The archive summary is injected on resume |
| Cursor | Host summarizes | `preCompact` commits | Nothing |
| TRAE, TRAE CN, ZCode | Host summarizes | No pre-compaction event | Nothing |
| Kimi Code | Host summarizes | `PreCompact` commits newly captured messages | Nothing |
| OpenCode | Host summarizes | v1 flushes and commits; v2 captures the transcript | v1 commits again on `session.compacted`; v2 commits after `session.compaction.ended` |
| DSH | Not observed | Nothing. Injected context is a user message and shrinks with the host's compaction; the profile is not sent again | Nothing |
| pi | Takeover, on by default | Commits and waits for the archive overview | The overview replaces pi's summary; on failure pi compacts as usual |
| OpenClaw | The plugin owns compaction | `compact()` commits and waits | The next context assembly rebuilds history from the server |
| Hermes (bundled) | Host summarizes | Fork-style compaction commits the old session; in-place compaction does nothing | Nothing |

<a id="_3-4-2-pi-takeover"></a>

### pi takeover

- **What it changes.** pi's own history is not modified. The extension rewrites the messages sent in each `context` event: everything before a boundary becomes one synthetic user message, `[OpenViking Session Context]`, holding the archive overview cut to 3,000 tokens. Its timestamp is just before the first kept message, so the provider request stays stable and prompt caching keeps working.
- **When it runs.** On token pressure, not on pi's compaction event: about 30,000 tokens with the last 3 user turns kept. The commit sends the exact message count of those 3 turns as `keep_recent_count`.
- **Where the summary comes from.** The overview of the archive that commit produced, polled up to 15 times at 2-second intervals. The boundary is stored in pi's branch as an `ov-takeover` entry, so it survives a restart.
- **When it falls back.** On a fingerprint mismatch, a history shorter than the boundary, or a missing overview, pi gets its full history. If the overview stays empty, the boundary does not move and the pending count resets until it builds up again.
- **pi's own compaction.** When it succeeds, `session_before_compact` returns the OpenViking summary to pi. Without a `firstKeptEntryId`, pi runs its default compaction.

<a id="_3-4-3-openclaw-contextengine"></a>

### OpenClaw context engine

- OpenClaw registers the plugin as its context engine with `ownsCompaction: true`, so the host no longer writes its own summary.
- Context assembly has two parts. `transformContext` only adds recall, behind five passthrough checks. The main assembly calls `getSessionContext(tokenBudget)` and replaces the host's live history with the server's response, split across four budget tiers, behind three passthrough checks and a message-cleaning step.
- `compact()` commits with `keep_recent_count` 0, polls every 500 ms for up to 5 minutes, and returns the archive overview as the summary.
- `ingest()` and `ingestBatch()` do nothing on purpose; turns are captured after each turn.
- When an archive exists, a short guide is added to the system prompt. It tells the model to reread the summary before saying it has no information, and to try at least two keyword sets with `ov_archive_search`.

<a id="_3-4-4-pi-vs-openclaw-takeover"></a>

### Comparing pi and OpenClaw takeover

| | pi takeover | OpenClaw context engine |
|---|---|---|
| Host contract | Rewrites messages in the `context` event | Context engine with `ownsCompaction: true` |
| Source of history | pi's local branch | The server's session context |
| Trigger | Client token threshold (30,000, last 3 user turns kept) | Each host call to assemble or compact |
| Result | One synthetic user message, up to 3,000 tokens | A rebuilt message list plus a compaction summary |
| On failure | Full history | The host's live messages |
| Recall | Context search with a session ID | `/find` without a session ID, so no expansion or deduplication |

<a id="_3-6-degradation-and-fault-tolerance"></a><a id="_3-6-1-decision-matrix"></a>

## When the server is unavailable

Recall errors are handled so the host can continue without injected memories. Requests can still delay a prompt until they finish or reach their deadline; error recovery does not mean there is no waiting.

| Integration | Server unreachable | Cached failure | Waiting and error propagation |
|---|---|---|---|
| Claude Code | Every hook catches the error and lets the host continue; session start skips queue replay | Context-search marker for 6 hours, local CLI check for 7 days, health for 5 seconds | Recall waits for its request or deadline before continuing. URI guard denials are intentional and separate from recall failures |
| Codex, TraeCode CLI 2.0 | Every hook catches the error and does nothing | Context-search marker; a failed local compressor stays off until the next start | Recall waits for its request or deadline, then the prompt continues |
| Cursor, TRAE, TRAE CN, ZCode | Request errors are swallowed and nothing is injected. A hook that cannot get its lock within 5 seconds skips silently | Context-search marker only, so every turn waits the full 15-second recall timeout | Up to the recall timeout each turn |
| OpenCode | Recall, capture, and cleanup errors are caught and logged | Context-search marker; health checks are not cached | Recall awaits network requests; cleanup can also wait until its deadline |
| DSH | The plugin swallows errors. Session setup failures are not cached, so each pre-step makes two 5-second health calls | Context-search marker; the user-space lookup is cached for the life of the process | Pre-step runs profile and recall in sequence, and session flush blocks |
| pi | If the health check fails at start, no tools are registered and later prompts retry quietly. A failed MCP handshake alone does not stop recall, sync, or takeover; the status line shows `tools ✗` and `/viking` prints the error | Context-search marker. The MCP handshake is retried once per turn and can use its 5-second budget before recall starts | Recall waits for its request or deadline. `session_shutdown` waits up to 30 seconds without takeover, and a network error at `turn_end` waits 10 seconds per message |
| OpenClaw | Recall is skipped if a 500 ms health check fails | None; one health check per turn | Only `memory_store` errors reach the model; `compact()` can wait up to 5 minutes |
| Hermes (bundled) | A failed connection is not retried for 30 seconds unless its settings change | The failed settings are remembered | Recall waits within its 4-second total budget before continuing without context |
| ov CLI | Most commands exit with status 1; `ov status` in table mode and `ov health` exit 0 even when unhealthy | None | Not applicable |

<a id="_3-6-2-common-timeouts"></a>

### Shared timeouts and locks

The shared plugins use a 15-second HTTP timeout (1-second minimum). MCP proxy requests time out after 15 seconds, and the `DELETE /mcp` sent on exit after 2 seconds. Cross-process locks wait 5 seconds and are considered stale after 60 seconds; queue entries being replayed are reclaimed after 10 minutes. The MCP proxy does not handle SIGHUP, so closing a terminal sends no `DELETE /mcp`; the server runs MCP statelessly, so nothing is left behind.

<a id="_3-1-integration-forms-installation-and-configuration"></a><a id="_3-1-1-decision-matrix"></a>

## Installation, sessions, and settings

The session ID prefix tells you which integration wrote a session on the server.

| Integration | Installed with | Session ID | Settings read from |
|---|---|---|---|
| Claude Code | Unified installer (`--harness claude`) or the plugin marketplace | `cc-<session id>`; subagents `cc-<id>__subagent-<agent_id>` | Shared settings, `plugin.claude_code` |
| Codex | Unified installer (`--harness codex`) or `codex plugin marketplace add` | `cx-<id>`, derived from the Codex session | Shared settings, `plugin.codex` |
| TraeCode CLI 2.0 | Unified installer (`--harness trae-cli`), which runs the Codex flow against `traecli` | Same as Codex | Same as Codex |
| Cursor | Unified installer; writes `~/.cursor/hooks.json` and `mcp.json` | `cu-<conversation id>` | Shared settings, `plugin.cursor` |
| TRAE, TRAE CN | Unified installer; writes `~/.trae/` or `~/.trae-cn/` hooks and MCP files | `tr-` or `trcn-` | Shared settings, `plugin.trae` or `plugin.trae_cn` |
| ZCode | Unified installer; merges into `~/.zcode/cli/config.json` and turns hooks on | `zc-<id>` | Shared settings, `plugin.zcode` |
| Kimi Code | Unified installer; managed Kimi Code plugin | `kc-<id>` | Shared settings, `plugin.kimicode` |
| OpenCode | Unified installer, npm (`@openviking/opencode-plugin`), or source | `oc-<id>`; subagents `oc-<parent>__subagent-<child>` | Shared settings, `plugin.opencode` |
| DSH | Unified installer or `dsh plugin add @openviking/dsh-memory-plugin` | `dsh-<id>` | Shared settings, `plugin.dsh`, then the Cordis patch |
| pi | Unified installer, into pi's extension directory | `pi-<id>` | Shared settings, `plugin.pi` |
| OpenClaw | ClawHub, npm installer, or offline bundle; then `openclaw openviking setup` | The session UUID, or a SHA-256 of the session key | `openclaw.json` and a few environment variables |
| Hermes (bundled) | Ships with Hermes; `hermes memory setup openviking` | Hermes's own session ID | `.env`, a linked `ovcli.conf`, and Hermes `config.yaml` |
| ov CLI | npm, `uv tool install`, cargo, or GitHub Releases | Not managed | `ovcli.conf` profiles |

"Shared settings" means the layered plugin settings in [Settings and workspace files](#settings-and-workspace-files).

<a id="_3-1-2-unified-installer"></a>

### Unified installer

`examples/memory-plugin-shared/install.sh` installs Claude Code, Codex, TraeCode CLI 2.0, Cursor, TRAE, TRAE CN, ZCode, Kimi Code, OpenCode, pi, and DSH. OpenClaw and Hermes have their own channels. Things worth knowing:

- Without `--harness`, it shows a multi-select menu. The setup helpers bundled with each plugin pass `--harness` for you. When piped from `curl`, it reads answers from `/dev/tty`.
- It downloads from the documentation site, or uses the local checkout when run from one.
- Hook and MCP entries carry an `OPENVIKING_INTEGRATION_ID` marker, so a rerun replaces its own entries and leaves other tools' entries alone. Each changed file is backed up to `.bak` and replaced atomically with mode `0600`.
- The credential step writes `~/.openviking/ovcli.conf` for a local server, OpenViking Service, or a custom URL. Existing values are shown, with the API key masked, before you choose to keep or change them.
- `--uninstall` covers Cursor, TRAE, TRAE CN, ZCode, and Kimi Code. Remove the others through the host's own plugin manager.
- Node.js 18 or later is required.

<a id="_3-1-3-credential-systems"></a>

### Credential sources

Four credential systems exist, each with its own variable names and headers. When a connection fails, first check which one the integration uses.

| Used by | Server URL | API key | Identity | Auth header |
|---|---|---|---|---|
| Shared plugin code: Claude Code, Codex, Cursor, TRAE, ZCode, Kimi Code, OpenCode, DSH, pi, Agent Plugins | `OPENVIKING_URL`, then `OPENVIKING_BASE_URL` | `OPENVIKING_BEARER_TOKEN`, then `OPENVIKING_API_KEY` | `OPENVIKING_ACCOUNT`, `OPENVIKING_USER`, `OPENVIKING_PEER_ID` | `Authorization: Bearer` only |
| OpenClaw | `OPENVIKING_BASE_URL`, then `OPENVIKING_URL` | `OPENVIKING_API_KEY`, or a SecretRef | `OPENVIKING_ACCOUNT_ID`, `OPENVIKING_USER_ID` | `X-API-Key` |
| Hermes | `OPENVIKING_ENDPOINT` | `OPENVIKING_API_KEY` | `OPENVIKING_ACCOUNT`, `OPENVIKING_USER`, `OPENVIKING_AGENT` | Both `X-API-Key` and `Bearer`. With a key, tenant headers are omitted unless the server asks for them, then retried once |
| ov CLI | `ovcli.conf` | `ovcli.conf` | `--account`, `--user`, `--actor-peer-id` | `X-API-Key`; Basic or Bearer depending on `auth_mode`. A key with two or more dots is also sent as Bearer |

For the shared plugin code:

- `OPENVIKING_CREDENTIAL_SOURCE` (`env`, `cli`, or `auto`, default `auto`) chooses where credentials come from. In `auto`, any credential variable in the environment wins. Only when none is set and `ovcli.conf` has credentials does the plugin use that file instead. `env` reads no file and defaults the URL to `http://127.0.0.1:1933`.
- Hooks and the MCP proxy resolve the connection with the same function and never read the working directory, so a proxy started from the plugin directory reaches the same server with the same identity as the hooks.
- `X-OpenViking-Account` and `X-OpenViking-User` are sent only in trusted mode. With an API key, the server reads the identity from the key.

The full resolution order is in [Client configuration](../configuration/02-client.md#connection-and-authentication) and the [plugin development guide](./18-plugin-development.md#_4-2-credentials-are-not-ordinary-workspace-settings).

<a id="_3-1-4-configuration-layers"></a>

### Settings and workspace files

Integrations built on the shared plugin code read behavior settings from, highest first: `OPENVIKING_*` environment variables, a per-machine workspace registry, the repository's `.openviking/config.local.json` and `.openviking/config.json`, `ovcli.conf` `plugin.<harness>`, `ovcli.conf` `plugin`, and the legacy harness section of `ov.conf`. Workspace files cannot set URLs, keys, or other credentials. See [Plugin settings](../configuration/02-client.md#plugin-settings) and [Workspace configuration](../configuration/02-client.md#workspace-configuration). OpenClaw reads `openclaw.json` and Hermes reads its `.env` and `config.yaml`.

**Workspace peer.** By default, these integrations tag memories with a peer derived from the repository's normalized `origin` URL, so every clone of a repository shares one peer. Outside a repository no peer is sent, and memories go to the user's own space. `peer.source` and `.openviking/config.json` change this; see [Workspace peer](../configuration/02-client.md#workspace-peer). OpenClaw derives its peer from `peer_role` and `peer_prefix`; with `peer_role=sender`, tool calls fail when the host provides no sender. Hermes sets no assistant peer unless `OPENVIKING_AGENT`, a linked OpenViking peer, or the YAML `agent` key sets one. Agent Plugins sends no peer.

Settings that apply only to some integrations:

- `OPENVIKING_COMMIT_TURN_THRESHOLD`: Cursor and Kimi Code. TRAE, TRAE CN, and ZCode commit every turn.
- `OPENVIKING_WRITE_PATH_ASYNC`: Claude Code, Codex, and ZCode.
- `OPENVIKING_RECALL_COMPRESS=client`: Claude Code and Codex. `server` and `auto` work for every integration listed under [Recall digest](#recall-digest).
- The workspace files, the `ovcli.conf` `plugin` section, `OPENVIKING_RECALL_DEDUP_TURNS`, `OPENVIKING_RECALL_QUERY_EXPANSION`, `OPENVIKING_PEER_SOURCE`, and the skill catalog settings: every integration built on the shared plugin code, not OpenClaw or Hermes.

<a id="_3-7-additional-ux-comparison"></a>

## Other host features

| Integration | Status line | Commands | Skills shipped | Setup wizard |
|---|---|---|---|---|
| Claude Code | Yes | `/openviking-memory:ov` shows server status, identity, and where injected context came from | `openviking-memory`, `openviking-skills`, `ov-experience-memory`, `ov-memory-doctor` | Yes |
| Codex, TraeCode CLI 2.0 | No | None | Same four as Claude Code | Yes |
| Cursor | No | None | An always-on rule plus `openviking-memory`, `openviking-skills`, `ov-experience-memory` | Installer menu |
| TRAE, TRAE CN, ZCode | No | None | None | Installer menu |
| OpenCode | No | None | The same three as Cursor, only when the plugin registers its MCP server | Yes |
| DSH | No | None | The same three as Cursor | No |
| pi | Yes | `/viking`, `/viking commit` | The same three as Cursor, unless `mcpEnabled` is `false` | Yes |
| OpenClaw | No | `/add-resource`, `/add-skill`, `/ov-search`, `/ov-query-config`, `/ov-recall-trace` | Three plugin skills | Yes; checks the key's role and version compatibility |
| Hermes (bundled) | No | None | None | Yes; `hermes memory status` lists environment overrides |
| ov CLI | No | The CLI itself | None | `ov config` |

Claude Code, Codex, Cursor, TRAE, ZCode, Kimi Code, DSH, and pi also install a **URI guard**: a file tool whose path is a `viking://` URI is denied with a hint to use the OpenViking tools, and a shell command that contains one runs with a notice attached. Which tools are checked differs by host and is listed in [Notes by integration](#notes-by-integration).

<a id="_4-harness-profile-cards"></a>

## Notes by integration

Each note covers what is specific to one integration. Shared behavior is in the sections above.

### Claude Code

[Claude Code Memory Plugin](./02-claude-code.md). A marketplace plugin with 9 hooks, an MCP proxy, a slash command, a status line, and 4 skills.

- It registers more hooks than any other integration: `SessionStart`, `UserPromptSubmit`, `PostToolUse:Read` (the skill-experience hook, off by default), `PreToolUse` on Read, Glob, Grep, Edit, Write, and Bash (the URI guard), `Stop`, `PreCompact`, `SessionEnd`, `SubagentStart`, and `SubagentStop`.
- Recall digests are on by default through a local `claude -p`, with the server digest as the fallback.
- Every exit except `kill -9` sends a commit.
- The capture cursor is kept under `/tmp`. If the system clears it, the whole session is sent again.
- The URI guard runs even when the plugin's own switch is off.

<a id="codex"></a><a id="trae-cli-traecode-cli-2-0"></a>

### Codex and TraeCode CLI 2.0

[Codex Memory Plugin](./04-codex.md), [TRAE Memory Integration](./13-trae.md). A marketplace plugin with 6 hooks (`SessionStart`, `UserPromptSubmit`, `PreToolUse:Bash`, `Stop`, `SessionEnd`, `PreCompact`), an MCP proxy, and the same 4 skills as Claude Code.

- `SessionEnd` fires only on a clean exit and only on Codex 0.145 and later. Signals, crashes, older builds, and `codex app-server` deferral fall to the start-up sweep, which uses a 30-minute idle timeout.
- `Stop` and `SessionEnd` write in a detached worker. A per-session lock orders the `Stop` worker, `PreCompact`, the `SessionEnd` worker, and the sweep.
- Codex edits files with `apply_patch`, which has no path argument, so the URI guard on Bash only adds notices and never denies.
- Codex keeps a trust record per hook. After an upgrade that adds hooks, approve the new ones in `/hooks`.
- TraeCode CLI 2.0 (binary `traecli`, config `~/.trae/traecli.toml`) is a Codex-family CLI and installs this same plugin. Only version 2.0 is supported; the earlier standalone plugin for 1.0 has been removed, and `--harness trae-cli --uninstall` still removes a copy an old install left behind. A build without `SessionEnd` ignores that hook.

### Cursor

[Cursor Memory Integration](./12-cursor.md). Hook and MCP configuration with 6 hooks (`sessionStart`, `beforeSubmitPrompt`, `beforeReadFile`, `stop`, `preCompact`, `sessionEnd`), an always-on rule, and 3 skills.

- The URI guard runs on `beforeReadFile` only, independent of the plugin switch. Shell commands are not checked, and an upgrade removes the `beforeShellExecution` entry older installs registered.
- Capture is text-only, so `ov-experience-memory` can find and apply Experience but cannot link its reads back to the Experience used.
- `sessionEnd` fires only on window close, after Cursor has stopped running hook commands, so it does not commit in practice.
- With the server unreachable, every turn waits out the 15-second recall timeout.

<a id="trae-trae-cn-ide-editions"></a>

### TRAE and TRAE CN

[TRAE Memory Integration](./13-trae.md). Hook and MCP configuration with 4 hooks: `SessionStart`, `UserPromptSubmit`, `PreToolUse`, and `Stop`. The MCP server is named `openviking`.

- `PreToolUse` denies Read, Glob, and Grep on `viking://` paths and adds a notice to Bash and RunCommand commands that contain one.
- Every `Stop` with content commits, so an exit loses at most the turn in progress.
- TRAE and TRAE CN differ only in session prefix and install paths. The same work in both clients creates separate sessions; they share memories only after extraction.
- No compaction handling, status line, skills, or subagent handling.

### ZCode

[Community integrations: ZCode](./08-community-plugins.md#zcode-memory-integration). Hook and MCP configuration with 4 hooks: `SessionStart`, `UserPromptSubmit`, `PreToolUse` on Read, Glob, and Grep, and `Stop`.

- The rollout file `~/.zcode/cli/rollout/model-io-<sid>.jsonl` is the source of captured turns; turns a missed `Stop` skipped are caught up at the next `Stop`.
- The first capture reads the whole rollout, so installing into a long-running session produces one large upload.
- Capture removes injected blocks but does no other text cleanup.
- `Stop` writes in a detached worker by default.

<a id="kimicode"></a>

### Kimi Code

[Community integrations: Kimi Code](./08-community-plugins.md#kimi-code-memory-integration). A managed Kimi Code plugin with 7 hooks (`SessionStart`, `UserPromptSubmit`, `PreToolUse` on Read, Glob, and Grep, `Stop`, `PreCompact`, `SessionEnd`, `Interrupt`) and an MCP proxy. The installer builds one self-contained copy under `$KIMI_CODE_HOME/plugins/managed/openviking-memory`.

- Captured turns come from `wire.jsonl`. A turn moves the cursor only after it is sent or stored in the offline queue.
- Session-start context is injected at the first prompt and retried on later prompts until it succeeds.
- `Stop`, `PreCompact`, and `SessionEnd` may write in the background. `Interrupt` stays synchronous with a 2-second request budget.
- `UserPromptSubmit` returns plain context text, not JSON.
- Installation keeps other plugin records and does not edit the older `config.toml` or `mcp.json`.

### OpenCode

[OpenCode Plugin](./10-opencode.md). The npm plugin `@openviking/opencode-plugin` supports OpenCode v1 (1.15.7 and later) and v2 (2.0.15 and later) from one entry point.

- v2 recalls once per prompt and stores the result in the user message's metadata; later model steps reuse it without new requests, which keeps the prompt cache stable. At the end of each execution, v2 reads the complete user, assistant, and tool messages for capture.
- v2 checks the commit threshold after every execution, including failed or interrupted ones, which keep their state. It handles only sessions of its own location and stores its cursor in plugin storage. v2 has no toast API and logs notices instead.
- MCP tools keep the `openviking_` prefix; v2 sets `codemode: false`. The 3 skills are added only when the plugin registers its MCP server, not in hook-only mode or with `mcp.openviking` disabled.
- Commit timeout is 30 seconds.

<a id="dsh-deepseek-harness"></a>

### DSH

[DSH Plugin](./17-dsh.md). The only in-process Cordis plugin. Hooks call the REST API directly; tools come through `@deepseek-ai/dsh-mcp-client` and the shared stdio proxy.

- It listens to `agent/session-start`, `agent/pre-step`, `session/event`, `session/flush`, `tools/pre-execute`, and `tools/post-execute`.
- `ctx.provide("openvikingMemory")` lets other Cordis plugins build on it.
- The installer uses the `web` profile unless `--dsh-profile` names another. Every mode except `dev` installs the published npm package.
- Tool results are captured by default (`captureToolResults: true`), so `ov-experience-memory` runs its full loop; turning capture off leaves it able only to find and apply Experience.
- The URI guard lowercases tool names, denies file tools on `viking://` paths in `tools/pre-execute`, and adds a notice to `bash` commands in `tools/post-execute`.
- Credentials named in the Cordis patch override every other source. For behavior settings the patch is the lowest layer.

<a id="pi-pi-coding-agent-extension"></a>

### pi

[pi Coding Agent Extension](./11-pi.md). A native extension with 9 event handlers and a `/viking` command. Recall, sync, session-start context, and takeover use REST; tools use pi's built-in MCP client.

- Tools are whatever the server's `tools/list` returns, renamed `openviking_<tool>`. A server that adds or drops a tool changes pi's tools at the next session, without an extension release.
- Upgrading from 0.3.x renamed every tool from `viking_*` with no alias period. Update any `--tools` or `--exclude-tools` list, or the tools disappear silently.
- `openviking_read` returns file content with `offset` and `limit`. A directory URI returns an error. The old `level="abstract"` and `"overview"` reads have no MCP equivalent; use `openviking_search(mode="context", detail="overview")` or `openviking_tree(include_abstract=true)`.
- A tool call times out after 15 seconds (`OPENVIKING_TIMEOUT_MS`). A timeout or ESC fails the call only locally; the request already sent keeps running, so a write may still land.
- `tool_call` denies read, grep, find, ls, write, and edit on `viking://` paths; `tool_result` adds a notice to `bash` output that contains one.
- With takeover off, resuming with `pi -c` sends the whole branch again.
- Bypass uses `bypassSessionPatterns`; the older `bypassPatterns` still works.

### OpenClaw

[OpenClaw Plugin](./03-openclaw.md). A context-engine plugin that owns compaction, with 15 tools, 5 slash commands, lifecycle hooks, Gateway HTTP routes, and feature-gated RPC methods.

- `memory_recall` searches memory and `ov_search` searches resources and the user's skills; explicit parameters let either reach the other.
- Recall goes through `/find` without a session ID, so there is no query expansion or deduplication, and the same memory can be injected repeatedly in a long session. By default each recalled memory costs one extra read (`recallPreferAbstract=false`).
- Exits do not commit. Archiving relies on `/new`, `/reset`, and the threshold. There is no offline queue, so a failed turn is not sent again.
- `compact()` can block for up to 5 minutes.
- Configuration is validated strictly: an unknown key or invalid value puts the plugin into setup-only mode.
- The commit threshold is `commitTokenThresholdRatio` (default 0.5) of `tokenBudget` (default 128,000).

<a id="hermes-nous-research"></a>

### Hermes

[Hermes Agent](./05-hermes.md). Two OpenViking memory providers exist for Hermes, and both register under the provider name `openviking`:

- The **bundled provider** ships inside Hermes under `plugins/memory/openviking` and needs no installation; `hermes memory setup openviking` configures it. The Hermes rows on this page describe the bundled provider as of [Hermes commit `989798c`](https://github.com/NousResearch/hermes-agent/tree/989798cd5e691230b54b2ea72e5937b68133014c/plugins/memory/openviking).
- The **external plugin** is maintained in this repository under [`examples/hermes-plugin`](https://github.com/volcengine/OpenViking/tree/main/examples/hermes-plugin) and installed with `hermes plugins install`. According to its README, a Hermes release that still bundles the provider loads the bundled copy, and the external plugin becomes active only after the bundled copy is removed. Configuration and stored data carry over.

The two are separate code bases. Where their behavior differs:

| | Bundled provider (Hermes `989798c`) | External plugin (this repository) |
|---|---|---|
| Commits during a session | None; only at session boundaries | A background commit at 20,000 pending tokens (`commit_token_threshold`) |
| Recall digests | Not supported | Optional server digest (`recall_compress`) |
| Mirroring Hermes's built-in memory | Additions only | Additions, replacements, and removals, tracked in a URI registry |
| `viking_forget` | User memory files with an explicit user ID | Also accepts `viking://~/`, rejects user-ID-less layouts, and checks ownership before deleting |
| Cron, subagent, and flush contexts | Not documented at that commit | Recall works; automatic writes and mirroring are skipped |

Behavior of the bundled provider:

- Recall runs before every model call, through session-aware `search/search`, with `/find` as a fallback. Defaults: 6 results, score threshold 0.15, 4,000 characters, 4 seconds in total and 3 seconds per request.
- `viking_remember` sends the fact unchanged through its own session and commits it; it returns `status: submitted`.
- Commits leave no live messages (`keep_recent_count` 0). Uploads are not durable, but pending-commit markers let a later start commit sessions from a dead run on POSIX.
- Memories are written under `viking://user/<uid>/memories/`, or `viking://user/<uid>/peers/<peer>/memories/` when a peer is set.
- Linking an OpenViking `ovcli.conf` profile clears the five connection variables from the Hermes `.env`. `hermes backup` includes the default or environment-selected `ovcli.conf` under `$HOME`; back up a YAML-linked file separately.

<a id="_5-ov-cli-command-reference"></a><a id="_5-1-command-tree"></a><a id="_5-2-global-options-and-unique-mechanisms"></a><a id="_5-3-capabilities-only-the-cli-has"></a>

### ov CLI

[Install and use the CLI](../getting-started/05-cli-setup.md). `ov` is a Rust client for the REST API. It has no host events, automatic recall, or compaction handling; every action is a command you or a script runs. Run `ov --help` for the command list, and see [CLI output format](../api/01-overview.md#cli-output-format) for JSON output.

What the CLI offers that the plugins do not: multiple `ovcli.conf` profiles, account and user administration (`ov admin`), root operations with `--sudo`, privacy policy management (`ov privacy`), snapshots, backup and restore, export and import, `ov reindex`, local root-key generation (`ov system crypto init-key`), the `ov tui` file browser, and session auto-commit policies (`ov session new --auto-commit-policy-json`, `ov session config set`). Server operations among these are also available through the HTTP API and SDK.

Behavior that matters in scripts:

- `ov rm` deletes without confirmation; `-r` deletes recursively.
- `ov health` exits 0 even when the server reports unhealthy, and `ov status` in table mode always exits 0.
- `find`, `search`, `ls`, `tree`, `grep`, and `glob` print a `cmd: …` line to standard output before the result while `echo_command` is on, which is the default, even with `-o json`.
- Most commands print `{"ok": true, "result": …}` or `{"ok": false, "error": …}` in compact JSON mode; the `ov config` commands print `{"status": "ok", "result": …}`.
- A display language must be configured before commands run; in a non-interactive shell without one, the CLI exits with status 2.
- `ov doctor` exists only in the Python package installed with `uv tool install openviking`, where it checks the server's `ov.conf`. The npm and cargo binaries do not include it.

<a id="_6-custom-agent-integration-guide"></a><a id="_6-1-integration-path-×-capabilities-you-get"></a>

## Build your own integration

If your agent is not listed, choose the least work that gives you the behavior you need:

| Approach | Effort | Tools for the agent | Automatic recall and capture | Commits |
|---|---|---|---|---|
| [Connect over MCP](#connect-over-mcp) | Minutes | Server MCP tools | None; the model calls tools itself | Only `remember`, in its own session |
| [Agent Plugins package](./15-agent-plugins.md) | Minutes | Server MCP tools, plus a skill that teaches their use | None | Only `remember` |
| [HTTP API, SDK, or LangChain](#call-the-http-api-or-sdk) | Hours | Whatever you call | You build it, or use the LangChain middleware | You decide |
| [Reuse the shared plugin code](#reuse-the-shared-plugin-code) | Days | Server MCP tools through the proxy | Recall, capture, commits, and the offline queue | Depends on the host events you connect |

<a id="_6-2-path-1-direct-mcp-connection-recommended-starting-point"></a>

### Connect over MCP

Clients that support Streamable HTTP can connect to the server's `/mcp` endpoint. This example uses the common `mcpServers` format; other clients use other fields, listed in [MCP clients](./06-mcp-clients.md).

```json
{
  "mcpServers": {
    "openviking": {
      "url": "http://127.0.0.1:1933/mcp",
      "headers": {
        "Authorization": "Bearer <api_key>"
      }
    }
  }
}
```

Use a user or admin API key; the server reads the account and user from it. `X-OpenViking-Account` and `X-OpenViking-User` apply only in trusted identity mode and cannot override a normal key. Add `X-OpenViking-Actor-Peer` when you need workspace peer context; see [Authentication](../guides/04-authentication.md). Clients that support only stdio can use the [Agent Plugins proxy](./15-agent-plugins.md). A direct MCP connection has no automatic recall, capture, or commits.

<a id="_6-3-path-2-programmatic-integration"></a>

### Call the HTTP API or SDK

- **REST.** Recall with `POST /api/v1/search/search`; query expansion and deduplication need `mode: "context"`, a `session_id`, and `dedup_turns` (see [How recall reaches the server](#how-recall-reaches-the-server)), and `rewrite` requests a [digest](#recall-digest). Write with `POST /api/v1/sessions/{id}/messages/batch` (up to 100 messages; creates the session), commit with `POST /api/v1/sessions/{id}/commit`, and read with `GET /api/v1/content/read`. See [Sessions](../api/05-sessions.md) and [Retrieval](../api/06-retrieval.md). For server-side auto-commit, see [How server sessions and commits work](#how-server-sessions-and-commits-work).
- **LangChain and LangGraph** (`pip install langchain-openviking`). `OpenVikingContextMiddleware` injects recall into `<openviking_context>` before model calls and captures and commits after the agent runs, following `CommitPolicy` (default `never`). Read-only calls are retried once; writes are never retried. A partial write raises `OpenVikingPartialWriteError` with the counts needed to retry the rest. See [LangChain and LangGraph](./07-langchain-langgraph.md).
- **Open WebUI.** `python -m openviking_openwebui` starts a separate OpenAPI tool server with 7 tools and no delete or hooks. See [Other integrations](#other-integrations).

<a id="_6-4-path-3-reuse-a-reference-implementation-for-automatic-hooks"></a><a id="_2-2-the-memory-plugin-shared-layer"></a>

### Reuse the shared plugin code

For automatic recall, capture, commits, and offline retries, reuse the existing code instead of writing it again:

- **`examples/memory-plugin-shared/lib/`** (Node.js) holds the shared modules: recall with its fallbacks, session-start context, capture cleaning, the offline queue, batch sending, the MCP proxy, session IDs, and credentials. Hosts configured through hook files can add an adapter to `examples/agent-hook-plugin/hosts/`, as Cursor, TRAE, ZCode, and Kimi Code do.
- **The Agent Plugins package** (`agent-plugins/`) is a portable `plugin.json`, `skills/`, and `mcp.json` with a stdio-to-HTTP proxy and no hooks. Its `plugin.test.mjs` checks conformance with the Agent Plugins specification and can serve as a lint baseline for your own package.

The [plugin development guide](./18-plugin-development.md) covers module ownership, configuration, generated copies, installers, and tests. Whatever you build, keep three rules so it behaves like the existing integrations:

1. Use context-mode recall with a session ID and `dedup_turns`, so the server can expand queries and deduplicate across turns.
2. Do not let the adapter's own timeout cut the deadline the shared code computes.
3. Commit at shutdown. Otherwise a conversation tail below the threshold stays uncommitted until something else triggers a commit. If the host has no shutdown event, turn on `memory.session_auto_commit.enabled` and give sessions a policy. Then check exit events and completed writes in the real host.

<a id="_7-appendix-non-coding-integrations-at-a-glance"></a>

## Other integrations

| Integration | What it is | Tools | Sessions and commits | Failure handling | Active when |
|---|---|---|---|---|---|
| [Open WebUI](./08-community-plugins.md#open-webui-tool-server) | Standalone OpenAPI tool server | 7: `ov_search`, `ov_recall_memories`, `ov_add_memory`, `ov_list_memories`, `ov_read_resource`, `ov_add_resource`, `ov_session_status`; no delete | No sessions | No retries or cached failures. Its `/health` echoes its configuration and does not contact OpenViking | You start the process |
| [LangChain and LangGraph](./07-langchain-langgraph.md) | Python SDK adapters: retriever, tools, store, middleware, recorder | 12 tools from `create_openviking_tools()`; `viking_forget` is not in the default agent profile | Session and thread IDs come from the caller; `CommitPolicy` defaults to `never` | Reads retried once, writes never; partial writes raise a structured error | You construct it |
| [Agent Plugins](./15-agent-plugins.md) | Portable package with a stdio-to-HTTP MCP proxy | Server MCP tools; no hooks, a skill tells the model when to call them | Only `remember` | The proxy retries once after 401 or 403 with fresh credentials, and once after 400 or 404 with a new MCP session | The client loads it |
| [MCP clients](./06-mcp-clients.md) | Direct connection to `/mcp` | Server MCP tools | Only `remember` | Up to the client | Always available on the server |
| [Log ingestion](./09-log-ingestion.md) | `openviking-server ingest`, run on the machine with the logs | None; import only | Session ID `{prefix}__{harness}__{id}`; commits at 6,000 pending tokens or 5 seconds idle, `keep_recent_count` 0 | Cursor store, single-instance lock, and a check of the server's message count after a crash | Off by default; `ingest.enabled` and each adapter's `enabled` must both be on. Adapters: Claude Code, Codex, Hermes, OpenCode, OpenClaw, Cursor |
| [OpenViking Helper](./14-openviking-helper.md) | Closed-source desktop app | Not covered here | Not covered here | Not covered here | Not covered here |

## Source notes

The comparisons above were checked against these locations. They help when you need to confirm a limitation for your version.

- Server MCP tools: `openviking/server/mcp_endpoint.py`, where the `@mcp.tool` registrations are the authoritative list.
- Write and delete checks: `openviking/storage/content_write.py` and `openviking/storage/viking_fs/_access.py`.
- Server auto-commit: `openviking_cli/utils/config/memory_config.py` and `openviking/session/auto_commit_policy.py`.
- Plugin settings and defaults: `examples/memory-plugin-shared/lib/config-schema.mjs`.
- Hook hosts: `examples/agent-hook-plugin/hosts/` for Cursor, TRAE, ZCode, and Kimi Code.
- pi takeover: `examples/pi-coding-agent-extension/lib/takeover-core.mjs`.
- OpenClaw tools and lifecycle hooks: `examples/openclaw-plugin/registries/openviking-tools.ts` and `examples/openclaw-plugin/plugin/openviking-lifecycle-hooks.ts`.
- Hermes: the bundled provider at the pinned Hermes commit linked in [Hermes](#hermes), and the external plugin's [README](https://github.com/volcengine/OpenViking/blob/main/examples/hermes-plugin/README.md).

## See also

- [Choose an agent integration](./01-overview.md)
- [Tune recall latency](./19-recall-tuning.md)
- [Develop and maintain an agent plugin](./18-plugin-development.md)
- [MCP clients](./06-mcp-clients.md)
- [MCP tools and protocol](../guides/06-mcp-integration.md)
- [Client configuration](../configuration/02-client.md)
- [Retrieval API](../api/06-retrieval.md)
- [Sessions API](../api/05-sessions.md)
- [Authentication](../guides/04-authentication.md)
