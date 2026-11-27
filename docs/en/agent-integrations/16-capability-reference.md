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

"Server MCP tools" means whatever the server's `tools/list` returns: 22 tools on the current server, listed in [Server MCP tools](#server-mcp-tools).

| Integration | How it connects | Tools the agent can call |
|---|---|---|
| [Claude Code](#claude-code) | Plugin with hooks and an MCP proxy | Server MCP tools |
| [Codex, TraeCode CLI 2.0](#codex-and-traecode-cli-2-0) | Codex plugin with hooks and an MCP proxy | Server MCP tools |
| [Cursor](#cursor) | Hook and MCP configuration, plus a rule | Server MCP tools |
| [TRAE, TRAE CN](#trae-and-trae-cn) | Hook and MCP configuration | Server MCP tools |
| [ZCode](#zcode) | Hook and MCP configuration | Server MCP tools |
| [Kimi Code](#kimi-code) | Kimi Code plugin with hooks and an MCP proxy | Server MCP tools |
| [CodeBuddy Code](#codebuddy-code) | Plugin with hooks and an MCP proxy, installed by the unified installer | Server MCP tools |
| [Qoder CLI](#qoder-cli) | Plugin with hooks and an MCP proxy, installed by the unified installer | Server MCP tools |
| [Antigravity](#antigravity) | Plugin with hooks and an MCP proxy, installed by the unified installer | Server MCP tools |
| [OpenCode](#opencode) | npm plugin that adds an MCP entry | Server MCP tools, named `openviking_<tool>` |
| [DSH](#dsh) | In-process Cordis plugin plus an MCP proxy | Server MCP tools, named `mcp__openviking__<tool>` |
| [pi](#pi) | Native extension with a built-in MCP client | Server MCP tools, registered as `openviking_<tool>` after the MCP handshake succeeds |
| [OpenClaw](#openclaw) | Context-engine plugin | 16 native tools, 15 enabled by default |
| [Hermes](#hermes) | Hermes memory provider | 7 native `viking_*` tools in the updated external plugin; 6 in older bundled providers |
| [MCP clients, Agent Plugins](#other-integrations) | MCP only | Server MCP tools |
| [ov CLI](#ov-cli) | Command-line client | Every operation as an explicit command; no agent involved |

### What runs automatically

| Integration | Recall before each prompt | Session-start context | Commits during a session | Commits at session end | Host compaction |
|---|---|---|---|---|---|
| Claude Code | Yes, session-aware | Profile, memory index, skills | By the server policy | Yes | Commits, then the host summarizes |
| Codex | Yes, session-aware | Profile, memory index, skills | By the server policy | Codex 0.145+ | Commits, then the host summarizes |
| TraeCode CLI 2.0 | Yes, session-aware | Profile, memory index, skills | By the server policy | Only if the build has `SessionEnd` | Same as Codex |
| Cursor | Yes, session-aware | Profile, memory index, skills | By the server policy | No | Commits, then the host summarizes |
| TRAE, TRAE CN | Yes, session-aware | Profile, memory index, skills | By the server policy | No | No pre-compaction event |
| ZCode | Yes, session-aware | Profile, memory index, skills | By the server policy | No | No pre-compaction event |
| Kimi Code | Yes, session-aware | Profile, memory index, skills, at the first prompt | By the server policy | Yes | Commits, then the host summarizes |
| CodeBuddy Code | Yes, session-aware, in the interactive TUI only | Profile, memory index, skills | By the server policy | Yes, in the interactive TUI only | Commits, then the host summarizes |
| Qoder CLI | Yes, session-aware | Profile, memory index, skills | By the server policy | Yes | Commits, then the host summarizes |
| Antigravity | Yes, session-aware, at the first model call of each turn | Profile, memory index, skills | By the server policy | No | No pre-compaction hook |
| OpenCode | Yes, session-aware | Profile, memory index, skills, at the first prompt | By the server policy | When the session is deleted | Commits, then the host summarizes |
| DSH | Yes, session-aware | Profile, memory index, skills | By the server policy | Yes, with a 3-second budget | Commits, then the host summarizes |
| pi | Yes, session-aware | Profile, memory index, skills, every turn | By the server policy; with takeover, at takeover's threshold | Yes, with a 3-second budget | Takeover off: commits, then pi summarizes; on: replaces pi's summary |
| OpenClaw | Yes, session-aware | Profile, every turn | At half the token budget (64,000 by default) | No | The plugin owns compaction |
| Hermes (bundled) | Yes, session-aware with a fallback | Profile and memory listings | No; only at session boundaries | Yes, if pending uploads finish within 10 seconds | Commits at fork-style compaction |
| ov CLI | No | No | Only `ov session commit` | Not applicable | Not applicable |

Rows labeled **Hermes (bundled)** describe the pinned in-tree provider from earlier Hermes releases. Catalog-based releases use the external plugin. Its different lifecycle behavior is listed in [Hermes](#hermes).

How to read this table:

- **Session-aware recall** sends the session ID so the server can use the conversation to interpret the query. The shared plugins' context mode also expands the query and tracks recently injected memories to avoid repeating them. Hermes uses list mode, which has no injection ledger. OpenClaw uses context search with the session ID but turns deduplication off, because its injected context is rebuilt every turn and never stored in the history. See [How recall reaches the server](#how-recall-reaches-the-server) for the request paths and fallbacks.
- **By the server policy** means the plugin gives the session an auto-commit policy and the server commits at 100,000 pending tokens and after 30 idle minutes. See [When sessions are committed](#when-each-integration-commits).
- **Commits at session end** means the integration sends a commit request at the host's session-end event. Network errors, host exit budgets, or process termination can still interrupt it. [What happens at exit](#what-happens-at-exit) covers the hosts without one.

<a id="host-events"></a>

### Host events

Every integration built on the shared plugin code runs one flow; a host only says which of its events starts each stage. This table is generated from the host modules (`node examples/memory-plugin-shared/host-table.mjs --lang en`).

<!-- host-table:start -->
| Host | Session start | Prompt | Turn end | Before compaction | Session end | Tool call | Has session end | Profile injected at | Writes may detach |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| claude-code | `SessionStart` | `UserPromptSubmit` | `Stop` | `PreCompact` | `SessionEnd`, `SubagentStop` | `PreToolUse` | yes | session start | yes |
| codex | `SessionStart` | `UserPromptSubmit` | `Stop` | `PreCompact` | `SessionEnd` | — | yes | session start | yes |
| cursor | `sessionStart` | `beforeSubmitPrompt` | `stop` | `preCompact` | `sessionEnd` | `preToolUse` | yes | session start | no |
| trae | `session-start` | `user-prompt-submit` | `stop` | `post-compact` (after compaction) | — | — | no | session start | no |
| trae-cn | `session-start` | `user-prompt-submit` | `stop` | `post-compact` (after compaction) | — | — | no | session start | no |
| antigravity | `session-start` | `pre-invocation` | `stop` | — | — | — | no | session start | yes |
| zcode | `session-start` | `user-prompt-submit` | `stop` | — | — | — | no | session start | yes |
| kimicode | `session-start` | `user-prompt-submit` | `stop` | `pre-compact` | `session-end`, `interrupt` | — | yes | first prompt | yes |
| codebuddy | `session-start` | `user-prompt-submit` | `stop` | `pre-compact` | `session-end` | — | yes | session start | yes |
| qoder | `session-start` | `user-prompt-submit` | `stop` | `pre-compact` | `session-end` | `pre-tool-use` | yes | session start | yes |
| opencode | `session.created` | `prompt` | `execution.ended` | `compaction` | `session.deleted` | `execute.before` | yes | first prompt | no |
| dsh | `agent/created` | `agent/pre-step` | `session/event` | `compaction/start` | `session/dispose` | `tools/pre-execute` | yes | session start | no |
| pi | `session_start`, `session_compact` | `context` | `turn_end` | `session_before_compact` | `session_shutdown`, `viking_commit` | `tool_call` | yes | session start | no |
<!-- host-table:end -->

- Codex fires `SessionEnd` from version 0.145. Cursor registers `sessionEnd`, but it fires only on window close, after Cursor has stopped running hook commands, so it does not commit in practice.
- TRAE reports a compaction only after it happened (`post-compact`), so nothing is committed before it.
- "Writes may detach" means the turn-end, compaction, and session-end writes can answer the host first and finish in a detached process (`writePathAsync`, on by default). Claude Code and Codex always run `PreCompact` inside the hook; Codex always detaches `SessionEnd`, which Codex gives 1 second by default and 3 seconds at most.

<a id="_1-1-active-tool-surface-agentic-calls"></a><a id="_2-shared-capability-core"></a>

## Tools the agent can call

<a id="_2-1-server-side-mcp-tool-surface"></a>

### Server MCP tools

Every MCP-based integration exposes the same server-defined tools and keeps no catalog of its own. A local proxy, or pi's built-in MCP client, reads `~/.openviking/ovcli.conf`, connects to the server's `/mcp` endpoint, and passes through what `tools/list` returns. A server upgrade can add tools without a plugin release; reconnect the client to see them. Parameters for each tool are in [MCP tools and protocol](../guides/06-mcp-integration.md#available-mcp-tools).

| Group | Tools |
|---|---|
| Search and browse | `find`, `search`, `rule_search`, `read`, `download`, `list`, `tree`, `grep`, `glob` |
| Write | `remember`, `write`, `edit`, `add_resource`, `add_skill` |
| Delete | `forget` |
| Watched resources | `list_watches`, `cancel_watch` (not yet available on the commercial edition) |
| Accounts and access control | `list_users`, `list_groups`, `get_acl`, `set_acl` |
| Status | `health` |

Behavior to know before relying on a tool:

- **`remember`** creates a one-shot session named `mcp-store-<id>`, adds the messages, and commits it at once. It is the only MCP tool that commits. It returns as soon as the commit is accepted, with the `task_id` of the background extraction; extraction then decides which memories to create or update. No MCP tool commits the agent's current conversation; that is the job of the automatic hooks.
- **`find` and `search`**: `find` is a fast search without session context. `search` can take a `session_id` and run intent analysis (`retrieval.enable_intent`, on by default). `find` with `context_type="skill"` returns one hit per skill package, pointing at its `SKILL.md`, from both the user's and the account's skills.
- **`write` and `edit`** can write only under `viking://resources`, `viking://user`, and `viking://agent`. A new file must end in `.md`, `.txt`, `.json`, `.yaml`, `.yml`, `.toml`, `.py`, `.js`, or `.ts`. The user's `skills/`, `peers/`, `privacy/`, and `sessions/` directories are read-only. A write under `viking://agent/skills` is accepted but skips skill installation, so use `add_skill` for skills.
- **`add_resource` with a local path** returns a signed upload URL (valid for 600 seconds by default). The model must upload the file to it, for example with a shell command; ingestion then starts on its own. Remote URLs are ingested directly.
- **`forget`** deletes whatever URI it is given, non-recursively by default. It does not check whether the URI is a memory, resource, or skill. See [Write and delete limits](#write-and-delete-limits).
- **Schemas** are flattened at server start so that clients accepting only the OpenAPI 3.0 subset, such as Gemini, can load them. Validation still follows the original signatures; `read`, for example, also accepts a single string. Every client receives the same schema.
- **Identity** is resolved the same way as for REST requests. See [Authentication](../guides/04-authentication.md).

Host differences:

- **OpenCode** prefixes the tools with `openviking_`. **DSH** names them `mcp__openviking__<tool>`.
- **DSH** runs one MCP proxy per DSH profile. Tool calls therefore carry a process-level actor peer, and `remember` is not tied to the DSH session. Recall, capture, and commits still resolve a peer per session.
- **pi** registers the tools only when the session is not bypassed and the `/mcp` handshake returns a tool list. If the handshake fails, that session has no OpenViking tools, but recall, sync, and takeover continue, and a later turn retries the handshake. `mcpEnabled: false` turns the bridge off. A root API key is refused on `/mcp` with 403, so a credential chain that ends at `ov.conf`'s `server.root_api_key` produces a session without tools.
- **Codex** passes its MCP server only the environment variables listed in `.mcp.json` `env_vars`.

### Rule search across agents

Rule search is a read-only, model-callable check before an action or response subject to constraints. Pass `task_context` with the user goal, planned action, and relevant facts; `rule_probability_threshold` defaults to `0.65`. Results preserve complete Rule blocks. Evaluate each Rule's `applies_when`, `exception`, and `content` and comply with every applicable Rule. A failed or unavailable check is not an empty set of Rules.

| Integration | Registered tool |
|---|---|
| Claude Code, Codex, TraeCode CLI, Cursor, TRAE/CN, ZCode, Kimi Code, CodeBuddy, Qoder CLI, Antigravity, Agent Plugins, other MCP clients | Server `rule_search`, with the host's MCP namespace |
| DSH / Desktop | `mcp__openviking__rule_search` |
| OpenCode, pi, VikingBot | `openviking_rule_search` |
| VikingBot through MCP | `mcp_<server_name>_rule_search` |
| Microsoft Agent Framework through MCP | Dynamically discovered `rule_search`, with an optional configured prefix |
| OpenClaw, Open WebUI | `ov_rule_search` |
| Hermes external plugin, LangChain / LangGraph | `viking_rule_search` |
| Magpie | `magpie-plugin_rule_search` (source installs use their folder prefix) |

Upgrade the server to a build that advertises `rule_search`, then reconnect MCP clients. Native REST adapters require the updated plugin. The shared `openviking-rule-search` skill ships with Claude Code, Codex/TraeCode CLI, Cursor, TRAE/TRAE CN, ZCode, Kimi Code, CodeBuddy, Qoder CLI, Antigravity, OpenCode, DSH, pi, OpenClaw, and Agent Plugins; hosts without bundled skills still discover the tool. Keep existing MCP opt-in and tool allowlist settings enabled. This adds a query entrypoint; it does not install an automatic per-turn check or enforcement hook.

Hermes catalog installations are pinned by the Hermes catalog and need its pin updated to receive the new plugin; older bundled providers keep their original tools.

### OpenClaw tools

OpenClaw registers its own tools instead of passing MCP tools through.

| Group | Tools | Notes |
|---|---|---|
| Memory | `memory_recall`, `memory_store`, `memory_forget` | `memory_recall` searches memory; `memory_store` commits through a temporary session |
| Rules | `ov_rule_search` | Complete applicable constraints; enabled by default, selectable with the `rules` group |
| Resources and skills | `ov_search`, `ov_read`, `ov_multi_read`, `ov_list`, `add_skill`, `add_resource` | `ov_search` covers resources and the user's skills by default. `add_resource` is off by default and also needs `enableAddResourceTool` |
| Session archives | `ov_archive_search`, `ov_archive_expand` | Search and expand archived history |
| Large tool output | `openviking_tool_result_read`, `openviking_tool_result_search`, `openviking_tool_result_list` | Read tool output the server stored separately |
| Diagnostics | `ov_recall_trace` | Shows how automatic recall was built |

### Hermes tools

The updated Hermes external plugin has seven tools: `viking_search`, `viking_rule_search`, `viking_read`, `viking_browse`, `viking_remember`, `viking_forget`, and `viking_add_resource`. There is no skill installation tool. `viking_remember` submits a fact through its own session and returns before extraction finishes; extraction may add, merge, or skip the fact. Older bundled providers have the original six tools. See [Hermes](#hermes) for distribution differences.

<a id="_3-5-type-boundaries-for-writes-and-deletes"></a><a id="_3-5-1-write-boundary"></a><a id="_3-5-2-delete-boundary"></a>

### Write and delete limits

The server applies the same checks to every delete, whichever tool or command sends it:

- The caller needs manage permission on the URI, and a user whose own deletion is in progress is refused.
- `viking://` and `viking://user` cannot be deleted. Deleting `viking://resources`, `viking://agent`, or a whole user root such as `viking://user/<id>` requires the ROOT role. Non-root callers cannot write to the `viking://temp` root.
- URIs hidden by the caller's actor-peer view are refused.

These checks protect namespaces, not content types. Restrictions by type come from the client:

| Entry point | Can delete | Client-side limits |
|---|---|---|
| MCP `forget` (every MCP integration, including CodeBuddy Code, Qoder CLI, DSH and pi) | Any URI it is given | Non-recursive unless `recursive=true`; no type or score check |
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
2. **Legacy recall.** If Context search is unavailable, the plugin can fall back to `/api/v1/search/recall`. A Context request rejected with HTTP `400` or `422` ends recall for that turn. Codex and Claude Code display the server error as a hook warning, and other callers of the shared recall pipeline write it to stderr.
3. **Plain find.** As a last resort the plugin runs `find` on `viking://~/memories` and `viking://~/skills`, ranks the results locally, and fills its own token budget. `recallTokenBudget`, `recallMaxContentChars`, and `recallPreferAbstract` apply only at this step.

The shared-plugin recall pipeline excludes resources; the model searches resource documents by calling `search` itself. Hermes can include resources when its resource-recall option is enabled.

The server handles a session ID in two ways:

- **Context mode** (context search and legacy recall) expands the query from the conversation and keeps a per-session ledger of injected memories. Expansion needs `retrieval.enable_intent` (on by default), a session that already has messages, and either an archive overview or current messages. The original query stays first, followed by up to three planned queries.
- **List mode** (the default when `mode` is omitted) lets intent analysis replace the query, so the original query may not survive, and it skips the ledger. Hermes's `viking_search(mode="deep")`, and Hermes's preferred recall path land here. They send a session ID but do not get expansion or deduplication.

Cross-turn deduplication (`dedup_turns`):

- The server default in context mode is **0**, which disables it. The shared plugins send 5 (`recallDedupTurns`). A direct API caller must send `dedup_turns` itself, even with a session ID.
- The window counts messages, not conversation rounds. When both user and assistant messages are captured, 5 covers about two rounds.
- With automatic capture off and recall on, the message count never moves, so a memory already injected stays suppressed for the rest of the session. Set `OPENVIKING_RECALL_DEDUP_TURNS=0` to turn deduplication off.

<a id="_3-2-2-decision-matrix"></a>

### Recall by integration

The table shows the default path. Turning on [recall digests](#recall-digest) changes the request. Every integration built on the shared plugin code applies `recallQueryFilters` and `minQueryLength` to the prompt, and a prompt event delivered twice (the same event ID, or the same text within 500 ms) is answered once.

| Integration | Runs on | Query | Where the context goes |
|---|---|---|---|
| Claude Code | Every `UserPromptSubmit` | The prompt, trimmed | `<openviking-context>` in `additionalContext` |
| Codex, TraeCode CLI 2.0 | Every `UserPromptSubmit` | The prompt | `additionalContext` |
| Cursor | `beforeSubmitPrompt` | The prompt | `additional_context` |
| TRAE, TRAE CN | `UserPromptSubmit` | The prompt, with earlier injected blocks removed | `additionalContext` |
| ZCode | `UserPromptSubmit` | The prompt, with injected blocks removed, including `<system-reminder>` | `additionalContext`, strict JSON |
| Kimi Code | `UserPromptSubmit` | The prompt | Plain context text, not JSON |
| CodeBuddy Code | `UserPromptSubmit`, interactive TUI only | The prompt | `<openviking-context>` in `additionalContext` |
| Qoder CLI | `UserPromptSubmit`, interactive and headless | The prompt | `<openviking-context>` in `additionalContext` |
| Antigravity | `PreInvocation` with `invocationNum` 0, once per user turn; later calls in a tool loop and reactive wakeups skip it | `lastUserInput`, with injected `<openviking-context>` blocks removed | A `systemMessage` step in `injectSteps`, which stays in the conversation |
| OpenCode | Every prompt | Text parts of the message | Stored in message metadata and injected before that message at each model step, without a new request |
| DSH | `agent/pre-step` | The prompt, minus its own injected content | Appended as a user message |
| pi | The `context` event | The prompt | Prepended to the last real user message, so this turn's prompt gets this turn's memories |
| OpenClaw | Context assembly | The incoming prompt, cleaned and cut to 4,000 characters | `<relevant-memories>` in the system prompt, rebuilt every turn. On hosts that do not pass the prompt, it is prepended to the last user message |
| Hermes | Before every model call | User input of 5 characters or more, with skill scaffolding removed | `<memory-context>` appended to the current user message in the request only; never stored |

<a id="_3-2-3-profile-opening-injection"></a>

### Session-start context

Integrations that use the shared plugin code inject one `<openviking-context>` block at session start. It holds the user profile (`viking://user/<space>/memories/profile.md`), an index of the `preferences/` and `entities/` memories, and an `<available-skills>` catalog. When the session has an actor peer, the same files under `viking://user/<space>/peers/<peer>/memories/` are added: the profile, preferences, and entities have no message range, so extraction writes the user's own facts to the user root even in a peer session. The user root comes first; the peer profile is a second `<user-profile>` element without the lines it shares with the user profile, and both roots share one budget:

```text
<openviking-context source="session-start">
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

For every integration built on the shared plugin code, the block opens the session at its session-start event (see [Host events](#host-events)). Where the session-start answer does not reach the model (Kimi Code, OpenCode), it goes with the first prompt instead. After a compaction that drops it (CodeBuddy Code, Antigravity, OpenCode, DSH), the next prompt sends it again. pi adds it to its system prompt, which pi rebuilds every turn. OpenClaw adds `<user-profile>` to the system prompt every turn, with no memory index or skill catalog. Hermes's own reader loads the profile and the preferences and entities listings, with a 6,000-token default budget.

Budgets and limits:

- `profileTokenBudget` (default 10,000 tokens) covers the profiles and the memory index. The profiles get half: the user profile first, the peer profile what is left, or a one-line pointer to its URI when less than 200 tokens remain. The two roots split the index budget. An oversized profile keeps its first 8 lines and its end; an oversized listing ends with `... +N more`. Token estimates count CJK characters as 1.5 tokens and other text as four characters per token.
- `skillCatalogTokenBudget` (default 1,200 tokens, `OPENVIKING_SKILL_CATALOG_TOKEN_BUDGET`) is a separate budget for the skill catalog. `skillCatalog` (`OPENVIKING_SKILL_CATALOG`) or a budget of 0 turns the catalog off.
- The catalog comes from one `GET /api/v1/skills` request. The user's own skills come first, and an account skill with the same name as a user skill is left out. Each description is cut to about 40 tokens. If descriptions do not fit, the catalog lists names only; if names do not fit, it ends with `... +N more`, or shrinks to a one-line count. Servers without that endpoint get no catalog.
- `sessionStartMaxBytes` caps the whole block in UTF-8 bytes: 9,500 for Claude Code and Codex, whose hosts move hook output above about 10,000 characters into a file and show only a preview; 20,000 for ZCode, which drops output above 32 KB; no cap elsewhere. Above the cap, the memory index is dropped first, then the skill catalog.

On a resume, a session whose profile has not changed is not sent it again. With `resumeArchiveInject` (off by default), a compaction or a resume also adds the session's latest archive overview to the block, taking up to half of `sessionStartMaxBytes`. After a compaction or a resume, the session-history note (`sessionHistoryPointer`, on by default) tells the model that OpenViking holds the full session and how to look things up in it; the first event whose answer reaches the model delivers it.

<a id="_3-2-4-timeout-and-budget-chain"></a>

### Timeouts and budgets

The server stops query expansion after 5 seconds (`retrieval.recall_intent_timeout_s`) and digest rewriting after 30 seconds (`retrieval.recall_rewrite_timeout_s`). The shared plugins raise their context-request timeout to at least 15 seconds when expansion is on and at least 45 seconds when a digest is requested, so the client waits for every server stage. To change these values, see [Tune recall latency](./19-recall-tuning.md#request-timeout).

All requests of one host event share one deadline, set per event in the host's `host.mjs` (`budgets`) to fit inside the host's hook timeout, so their timeouts cannot add up past it. The prompt event's budget is 20 seconds for Claude Code, 125 seconds for Codex (it includes the local compressor), and 17 seconds for Cursor, Kimi Code, CodeBuddy Code, and Qoder CLI.

OpenClaw uses 15 seconds by default for the context search (`autoRecallTimeoutMs`), after a 500 ms health check. Hermes uses 4 seconds in total and 3 seconds per request. Both limit injected recall to 4,000 characters and skip an entry that does not fit instead of cutting it.

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

Every integration defaults to `off`. Server digests are supported by every integration built on the shared plugin code, OpenClaw, and the Hermes external plugin, and need a server with context-search rewrite support. The bundled Hermes provider does not support recall digests. The legacy values `1` and `0` mean `auto` and `off`. A `no_relevant` result suppresses injection; the plugin must not fall back to the raw block.

Local compressors, used by `client` and `auto`:

- **Claude Code** checks for `claude` with `claude --version` (cached for 7 days) and runs `claude -p --model sonnet --effort low --strict-mcp-config`. The subprocess timeout defaults to 110 seconds (`recallCompressTimeoutMs`), but the prompt hook's budget can end it sooner. Inputs under 1,500 characters are not compressed. Cited URIs are matched back to real result URIs by edit distance, and bullets that cannot be matched are dropped. On failure the plugin injects locally formatted results, or the rendered block.
- **Codex** picks a model from `~/.codex/models_cache.json` (`gpt-5.3-codex-spark` first, then `gpt-5.6-luna` with low effort; cached for 7 days) and runs `codex exec` in a read-only, ephemeral sandbox with a 110-second default timeout. One runtime failure disables local compression until the next `SessionStart`. Output is capped at 4,000 characters; failures fall back as for Claude Code.
- **DSH** runs one model call through the prompting agent's own LLM service.

These settings apply to automatic recall only. An explicit MCP `search` call uses the arguments the model passes. See also [Tune recall latency](./19-recall-tuning.md).

<a id="_3-2-6-injection-backflow-protection"></a>

### Keeping injected context out of capture

Injected context is wrapped in fixed tags such as `<openviking-context>`, and capture removes those blocks before sending a turn, so recalled memories are not stored again as new conversation. The shared capture code also removes digest blocks, metadata fences, and timestamp prefixes. TRAE and ZCode use their own cleaning functions; ZCode's removes three kinds of injected block. OpenClaw removes `<relevant-memories>` both when it captures a turn and when it builds the next recall query. Hermes drops the calls and results of its three read-only tools from the captured batch and keeps write-tool calls.

<a id="_3-3-session-and-commit-lifecycle"></a>

## Capture and commits

<a id="_2-3-server-side-session-and-commit-semantics"></a><a id="_3-3-1-mechanism-foundations"></a>

### How server sessions and commits work

- **Sessions are created implicitly.** The server creates a session when it receives the first message for it, or on the first context-mode recall with that session ID.
- **A commit has two phases.** `POST /api/v1/sessions/{id}/commit` returns after phase 1 archives the messages. Its response includes a `task_id` for phase 2, memory extraction, which runs in the background. A successful commit response does not mean extraction has finished.
- **`keep_recent_count`** sets how many recent messages a commit leaves live in the session. The server default is 0, which archives everything. Every integration built on the shared plugin code and Hermes send 0, except pi in takeover mode, which sends the exact message count of its last 3 user turns; OpenClaw sends 10 on its threshold commit and 0 on reset, `memory_store`, and compaction.
- **Auto-commit policies.** A session can carry an auto-commit policy: a pending-token threshold the server checks on every message write, and an idle timeout. A policy is set through `POST /api/v1/sessions`, `PATCH /api/v1/sessions/{id}/config`, the SDK, `ov session new --auto-commit-policy-json` and `ov session config set`, or for every new session through `server.user_config_defaults.auto_commit_policy`. Policies need OpenViking server 0.4.13 or later. The idle timeout also needs `memory.session_auto_commit.enabled=true` on the server, which defaults to `false`; while it is off the idle scanner does not start.
- **Writes are batched.** The shared plugins send up to 100 messages per `POST /messages/batch`, matching the server limit, and fall back to one message at a time when the batch endpoint returns 404 or 405.
- **Large tool output is stored separately.** The server moves tool output above 20,000 characters into a separate record and leaves a `tool_output_ref`. Plugins raise their own limit (`captureToolMaxChars`) to 1,000,000 only as a safety net.

<a id="_3-3-2-regular-commit-triggers"></a>

### When each integration commits

Every integration built on the shared plugin code follows the same rules:

- **Turn end** captures the turns the host's transcript holds past the session's cursor. The cursor moves only past messages the server accepted or the offline queue took.
- **During a session** the server commits. After the session's first write, the plugin sets the session's auto-commit policy once: `commitTokenThreshold` pending tokens (default 100,000) and `commitIdleSeconds` idle seconds (default 1,800; 0 for none, and values under 300 count as 300). The plugin never checks a threshold itself. On a server older than 0.4.13 there is no threshold commit, and the session is committed only at a compaction or a session end. The idle commit needs `memory.session_auto_commit.enabled` on the server.
- **Before a compaction** the plugin captures and commits, unless `autoCommitOnCompact` is `false`. When the host may still be writing the last assistant message, that message waits for the next capture.
- **At session end** the plugin captures and commits.
- **At session start** the plugin replays the offline queue.

The host's events for each stage are in [Host events](#host-events). Differences:

| Integration | Difference |
|---|---|
| Claude Code | `SubagentStop` captures and commits the subagent's own session. `PreCompact` always runs inside the hook, because the host rewrites the transcript right after |
| Codex | `SessionEnd` exists from Codex 0.145 and always runs in a detached worker. `PreCompact` runs inside the hook |
| TraeCode CLI 2.0 | Same as Codex; a build without `SessionEnd` has no session-end commit |
| Cursor, TRAE, TRAE CN, ZCode, Antigravity | No session-end commit; see [What happens at exit](#what-happens-at-exit). TRAE, TRAE CN, ZCode, and Antigravity also have no pre-compaction event |
| Kimi Code | `Interrupt`, which Kimi Code fires instead of `Stop` after a cancelled turn, captures and commits like `SessionEnd`, inside the hook with a 2-second budget |
| CodeBuddy Code | `SessionEnd` fires only in the interactive TUI |
| pi | `/viking commit` commits at once. With takeover on, takeover owns the commits: the plugin sets no server policy, and takeover commits at its own token threshold |
| OpenClaw | After a turn, at `tokenBudget × commitTokenThresholdRatio` (128,000 × 0.5 by default). `/new`, `/reset`, and `memory_store` commit and wait. `compact()` commits and waits up to 5 minutes for extraction |
| Hermes (bundled) | No commits during a session. Session end, session switch (`/new`, `/resume`, `/branch`, fork-style compaction), gateway cache eviction, and an exit handler commit; `/undo` and in-place compaction do not |
| ov CLI | `ov session commit`; `ov add-memory` creates, fills, and commits its own session |

<a id="_3-3-3-shutdown-method-×-harness-outcome-matrix"></a>

### What happens at exit

On a host with a session-end event, a normal exit captures what is left and commits. On a host without one (Cursor, TRAE, TRAE CN, ZCode, Antigravity, and Codex before 0.145), the plugin does not commit the last stretch of a session: messages already captured stay in the server session until the server's idle commit or a later commit of the same session. Turn on `memory.session_auto_commit.enabled` on the server so the idle commit covers them; no client setting is needed, because the plugin already sends the idle time with the policy.

No integration commits after `kill -9` or a crash; the idle commit is the only fallback. Host-specific exit behavior:

| Integration | At exit |
|---|---|
| Claude Code | `SessionEnd` hands the commit to a detached worker in its own process group, which SIGHUP does not stop |
| Codex | A double Ctrl+C quits cleanly and fires `SessionEnd`; a single one does not |
| Kimi Code | `SessionEnd` and `Interrupt` commit. `Interrupt` shares its 2-second budget between capture and commit, so a slow capture leaves the commit to the next commit of the same session |
| CodeBuddy Code | `SessionEnd` fires only in the interactive TUI; a headless run is committed by the server policy or `/compact` |
| Qoder CLI | `SessionEnd` fires on `/exit` and at the end of a headless `-p` run |
| OpenCode | Session deletion commits |
| DSH | `session/dispose` gives each session one commit within a 3-second budget. A second Ctrl+C force-quits without it. Closing a browser tab in the web form does not tear down |
| pi | `session_shutdown` commits within a 3-second budget, unless takeover is on |
| OpenClaw | The `session_end` handler does not commit. Archiving relies on `/new`, `/reset`, and the threshold |
| Hermes (bundled) | The exit handler drains uploads for up to 10 seconds, then commits; if the drain does not finish, it skips the commit instead of committing half a session. SIGTERM and SIGHUP get a 1.5-second grace period by default, and the Hermes exit watchdog can cut a slow commit short |
| LangChain, Open WebUI, Agent Plugins, MCP clients | No session hooks. The `DELETE /mcp` an MCP proxy sends on exit only closes the protocol session. LangChain relies on the caller's `close()` |

<a id="_3-3-4-pending-queue-offline-compensation-comparison"></a>

### Offline retries

| Integration | What happens when a write fails |
|---|---|
| Every integration built on the shared plugin code | Retryable failures go to an on-disk queue under `~/.openviking/pending`. It is replayed at session start: up to 50 entries per run (2 for Kimi Code) and 3 attempts per entry, kept for 7 days. Network errors, 408, 429, and 5xx are retryable; other 4xx responses are not queued. Content the server refused is not sent again; a write refused for its credentials, or one that could not be queued, stays for the next capture. A failed message stops the replay so order is kept |
| OpenClaw | No queue. A failed turn is not sent again |
| Hermes (bundled) | Uploads run in in-process threads and are not replayed from disk. Pending-commit markers under `$HERMES_HOME/openviking/pending_sessions/` let a later start commit sessions left by a dead run (POSIX only) |
| LangChain | A failed commit is retried on the next record, in memory only. A partial write raises `OpenVikingPartialWriteError` with counts that let the caller retry the remaining messages |
| Log ingestion | A SQLite cursor store records each batch before sending. After a crash, the next run checks the server's message count to decide whether the batch arrived |

<a id="_3-3-5-subagent-session-comparison"></a>

### Subagents

| Integration | Subagent handling |
|---|---|
| Claude Code | Each subagent gets its own session, `cc-<id>__subagent-<agent_id>`. `SubagentStop` sends its transcript and commits |
| OpenCode | Subagents get `oc-<parent>__subagent-<child>` sessions |
| DSH | A subagent's events go to a child session of its own |
| Other hook hosts (Codex, Cursor, TRAE, ZCode, Kimi Code, CodeBuddy Code, Qoder CLI, Antigravity) | Subagents are not captured |
| Hermes | Delegated tasks run with `skip_memory=True`, so the subagent has no OpenViking session, recall, or tools, and its output is not captured |
| OpenClaw | A subagent with its own session ID gets its own session. Sessions can be excluded with `bypassSessionPatterns` |
| Log ingestion | The Claude Code adapter skips sidechain and meta records, so subagent conversations are not imported |

`skipSubagentSessions` turns subagent capture off where it exists.

<a id="_3-4-compaction-takeover"></a><a id="_3-4-1-decision-matrix"></a>

## Host compaction

When the host shortens its context, the integrations built on the shared plugin code capture and commit before it (where the host has a pre-compaction event) and leave the summary to the host. After it, the session-history note points the model at the full session, and on hosts whose compaction drops the session-start context (CodeBuddy Code, Antigravity, OpenCode, DSH) the next prompt sends the profile again. pi and OpenClaw can replace the host's summary with OpenViking's archive.

| Integration | Difference |
|---|---|
| TRAE, TRAE CN, ZCode, Antigravity | No pre-compaction event, so nothing is committed before it |
| pi | With takeover on, commits and waits for the archive overview, which replaces pi's summary; on failure pi compacts as usual |
| OpenClaw | The plugin owns compaction: `compact()` commits and waits, and the next context assembly rebuilds history from the server |
| Hermes (bundled) | Fork-style compaction commits the old session; in-place compaction does nothing |

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

| Integration | Server unreachable | Waiting |
|---|---|---|
| Every integration built on the shared plugin code | Every event catches the error and lets the host continue; nothing is injected, and captures go to the offline queue. There is no health check before recall or capture. A plugin that was installed but never set up (no `ovcli.conf`, no `ov.conf`, and no server in the environment) does nothing | Recall waits for its request, up to the event's budget. The context-search marker remembers a server without context search |
| OpenClaw | Recall is skipped if a 500 ms health check fails | Only `memory_store` errors reach the model; `compact()` can wait up to 5 minutes |
| Hermes (bundled) | A failed connection is not retried for 30 seconds unless its settings change | Recall waits within its 4-second total budget before continuing without context |
| ov CLI | Most commands exit with status 1; `ov status` in table mode and `ov health` exit 0 even when unhealthy | Not applicable |

<a id="_3-6-2-common-timeouts"></a>

### Shared timeouts and locks

The shared plugins use a 15-second HTTP timeout (1-second minimum). MCP proxy requests time out after 15 seconds, and the `DELETE /mcp` sent on exit after 2 seconds. A per-session lock orders the events of one session; it waits 5 seconds and is considered stale after 60 seconds, and a session-end or compaction capture waits longer for it so the final commit is not dropped. Queue entries being replayed are reclaimed after 10 minutes. The MCP proxy does not handle SIGHUP, so closing a terminal sends no `DELETE /mcp`; the server runs MCP statelessly, so nothing is left behind.

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
| CodeBuddy Code | Unified installer, which registers a local marketplace | `cb-<id>` | Shared settings, `plugin.codebuddy` |
| Qoder CLI | Unified installer, which registers a local marketplace | `qd-<id>` | Shared settings, `plugin.qoder` |
| Antigravity | Unified installer (`--harness antigravity`); writes `~/.gemini/config/plugins/openviking-memory` | `ag-<conversation id>` | Shared settings, `plugin.antigravity` |
| OpenCode | Unified installer, npm (`@openviking/opencode-plugin`), or source | `oc-<id>`; subagents `oc-<parent>__subagent-<child>` | Shared settings, `plugin.opencode` |
| DSH | Unified installer, `dsh plugin add @openviking/dsh-memory-plugin`, or Desktop's Plugins page | `dsh-<id>` | Shared settings, `plugin.dsh`, then the Cordis patch |
| pi | Unified installer, into pi's extension directory | `pi-<id>` | Shared settings, `plugin.pi` |
| OpenClaw | ClawHub, npm installer, or offline bundle; then `openclaw openviking setup` | The session UUID, or a SHA-256 of the session key | `openclaw.json` and a few environment variables |
| Hermes (bundled) | Ships with Hermes; `hermes memory setup openviking` | Hermes's own session ID | `.env`, a linked `ovcli.conf`, and Hermes `config.yaml` |
| ov CLI | npm, `uv tool install`, cargo, or GitHub Releases | Not managed | `ovcli.conf` profiles |

"Shared settings" means the layered plugin settings in [Settings and workspace files](#settings-and-workspace-files).

<a id="_3-1-2-unified-installer"></a>

### Unified installer

`examples/memory-plugin-shared/install.sh` installs Claude Code, Codex, TraeCode CLI 2.0, Cursor, TRAE, TRAE CN, ZCode, Kimi Code, CodeBuddy Code, Qoder CLI, Antigravity, OpenCode, pi, and DSH. OpenClaw and Hermes have their own channels. Things worth knowing:

- Without `--harness`, it shows a multi-select menu. The setup helpers bundled with each plugin pass `--harness` for you. When piped from `curl`, it reads answers from `/dev/tty`.
- It downloads from the documentation site, or uses the local checkout when run from one.
- Hook and MCP entries carry an `OPENVIKING_INTEGRATION_ID` marker, so a rerun replaces its own entries and leaves other tools' entries alone. Each changed file is backed up to `.bak` and replaced atomically with mode `0600`.
- The credential step writes `~/.openviking/ovcli.conf` for a local server, OpenViking Service, or a custom URL. Existing values are shown, with the API key masked, before you choose to keep or change them.
- `--uninstall` covers Cursor, TRAE, TRAE CN, ZCode, Kimi Code, CodeBuddy Code, Qoder CLI, and Antigravity. Remove the others through the host's own plugin manager.
- Node.js 18 or later is required.

<a id="_3-1-3-credential-systems"></a>

### Credential sources

Four credential systems exist, each with its own variable names and headers. When a connection fails, first check which one the integration uses.

| Used by | Server URL | API key | Identity | Auth header |
|---|---|---|---|---|
| Shared plugin code: Claude Code, CodeBuddy Code, Qoder CLI, Antigravity, Codex, Cursor, TRAE, ZCode, Kimi Code, OpenCode, DSH, pi, Agent Plugins | `OPENVIKING_URL`, then `OPENVIKING_BASE_URL` | `OPENVIKING_BEARER_TOKEN`, then `OPENVIKING_API_KEY` | `OPENVIKING_ACCOUNT`, `OPENVIKING_USER`, `OPENVIKING_PEER_ID` | `Authorization: Bearer` only |
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

- `OPENVIKING_WRITE_PATH_ASYNC`: the hosts whose writes may detach, listed in [Host events](#host-events).
- `OPENVIKING_RECALL_COMPRESS=client`: Claude Code and Codex. `server` and `auto` work for every integration listed under [Recall digest](#recall-digest).
- The workspace files, the `ovcli.conf` `plugin` section, `OPENVIKING_RECALL_DEDUP_TURNS`, `OPENVIKING_RECALL_QUERY_EXPANSION`, `OPENVIKING_PEER_SOURCE`, the commit policy settings, and the skill catalog settings: every integration built on the shared plugin code, not OpenClaw or Hermes.

<a id="_3-7-additional-ux-comparison"></a>

## Other host features

| Integration | Status line | Commands | Skills shipped | Setup wizard |
|---|---|---|---|---|
| Claude Code | Yes | `/openviking-memory:ov` shows server status, identity, and where injected context came from | `openviking-memory`, `openviking-skills`, `ov-experience-memory`, `openviking-rule-search`, `ov-memory-doctor` | Yes |
| Codex, TraeCode CLI 2.0 | No | None | Same five as Claude Code | Yes |
| Cursor | No | None | An always-on rule plus the same five as Claude Code | Installer menu |
| TRAE, TRAE CN, ZCode | No | None | Same five as Claude Code | Installer menu |
| Kimi Code | No | None | Same five as Claude Code | No |
| OpenCode | No | None | `openviking-memory`, `openviking-skills`, `ov-experience-memory`, `openviking-rule-search`, `ov-memory-doctor`, only when the plugin registers its MCP server | Yes |
| DSH | No | None | The same five as OpenCode, unless `mcpEnabled` is `false` | No |
| CodeBuddy Code | No | None | Same five as Claude Code | No |
| Qoder CLI | No | None | Same five as Claude Code | No |
| Antigravity | No | None | Same five as Claude Code, each also invocable as `/<name>` | Installer menu |
| pi | Yes | `/viking`, `/viking commit` | The same five as OpenCode, unless `mcpEnabled` is `false` | Yes |
| OpenClaw | No | `/add-resource`, `/add-skill`, `/ov-search`, `/ov-query-config`, `/ov-recall-trace` | Four plugin skills, including `openviking-rule-search` | Yes; checks the key's role and version compatibility |
| Hermes (bundled) | No | None | None | Yes; `hermes memory status` lists environment overrides |
| ov CLI | No | The CLI itself | None | `ov config` |

Claude Code, Cursor, TRAE, ZCode, Kimi Code, CodeBuddy Code, Qoder CLI, Antigravity, OpenCode, DSH, and pi also install a **URI guard**: a file tool whose path is a `viking://` URI is denied with a hint to use the OpenViking tools; on hosts that check shell tools, a shell command that contains one runs with a notice attached. Which tools are checked differs by host and is listed in [Notes by integration](#notes-by-integration).

<a id="_4-harness-profile-cards"></a>

## Notes by integration

Each note covers what is specific to one integration. Shared behavior is in the sections above, and each host's events are in [Host events](#host-events). The hook hosts, Claude Code and Codex included, ship from one plugin directory, `examples/agent-hook-plugin/` (plugin version 1.0.0 for all of them), with one hook entry (`scripts/hook.mjs <event> <host>`), one MCP proxy, and one copy of the skills.

### Claude Code

[Claude Code Memory Plugin](./02-claude-code.md). A marketplace plugin with hooks, an MCP proxy, a slash command, a status line, and 5 skills.

- Besides the flow's events it registers `PostToolUse:Read` (the skill-experience hook, off by default) and `PreToolUse` on Read, Glob, Grep, Edit, Write, and Bash (the URI guard).
- Each subagent is captured into its own session at `SubagentStop`.
- The URI guard runs even when the plugin's own switch is off.

<a id="codex"></a><a id="trae-cli-traecode-cli-2-0"></a>

### Codex and TraeCode CLI 2.0

[Codex Memory Plugin](./04-codex.md), [TRAE Memory Integration](./13-trae.md). A marketplace plugin with hooks, an MCP proxy, and the same 5 skills as Claude Code.

- `SessionEnd` fires only on a clean exit and only on Codex 0.145 and later. Without it, the plugin does not commit the last stretch of a session; see [What happens at exit](#what-happens-at-exit).
- There is no URI guard. Codex writes a `PreToolUse` notice into the history between a tool call and its output, and Responses servers that check the history as chat messages, such as Volcengine Ark, reject that order with HTTP 400.
- Codex keeps a trust record per hook. After an update that moves or adds hooks, approve them again in `/hooks`.
- TraeCode CLI 2.0 (binary `traecli`, config `~/.trae/traecli.toml`) is a Codex-family CLI and installs this same plugin. Only version 2.0 is supported; `--harness trae-cli --uninstall` still removes a copy an old install of the 1.0 plugin left behind. A build without `SessionEnd` ignores that hook.

### Cursor

[Cursor Memory Integration](./12-cursor.md). Hook and MCP configuration, an always-on rule, and 5 skills.

- The URI guard runs on `beforeReadFile` only, independent of the plugin switch. Shell commands are not checked, and an upgrade removes the `beforeShellExecution` entry older installs registered.
- `postToolUse`, `postToolUseFailure`, and `afterMCPExecution` keep completed tool calls, uploaded with the conversation at the next capture. Successful Experience reads can link to trajectories produced by that commit; failed or unfinished calls and automatic injection do not count as successful reads.
- `sessionEnd` fires only on window close, after Cursor has stopped running hook commands, so it does not commit in practice.

<a id="trae-trae-cn-ide-editions"></a>

### TRAE and TRAE CN

[TRAE Memory Integration](./13-trae.md). Hook and MCP configuration plus 5 skills. The MCP server is named `openviking`.

- `PreToolUse` denies Read, Glob, and Grep on `viking://` paths and adds a notice to Bash and RunCommand commands that contain one.
- There is no transcript file: turns are built from the hook payloads. The model passes the session to `session_history` itself.
- TRAE and TRAE CN differ only in session prefix and install paths. The same work in both clients creates separate sessions; they share memories only after extraction.
- The skills are installed into `~/.trae/skills/<name>` or `~/.trae-cn/skills/<name>`. A skill the user edited, or an unmanaged directory of the same name, is preserved on upgrade and uninstall. TraeCode CLI also reads `~/.trae/skills`, so with both the TRAE integration and the TraeCode CLI plugin installed, the CLI lists each of these skills twice: once bare and once under the plugin namespace.

### ZCode

[Community integrations: ZCode](./08-community-plugins.md#zcode-memory-integration). Hook and MCP configuration plus 5 skills.

- The skills are installed into `~/.zcode/skills/<name>`. A skill the user edited, or an unmanaged directory of the same name, is preserved on upgrade and uninstall.
- The rollout file `~/.zcode/cli/rollout/model-io-<sid>.jsonl` is the source of captured turns; without it, turns are built from the hook payloads.
- The first capture reads the whole rollout, so installing into a long-running session produces one large upload.

<a id="kimicode"></a>

### Kimi Code

[Community integrations: Kimi Code](./08-community-plugins.md#kimi-code-memory-integration). A managed Kimi Code plugin with hooks, an MCP proxy, and 5 skills. The installer builds one self-contained copy under `$KIMI_CODE_HOME/plugins/managed/openviking-memory`.

- Captured turns come from `wire.jsonl`.
- Only the `UserPromptSubmit` answer reaches the model, so the session-start context goes with the first prompt. The answer is plain context text, not JSON.
- The model passes the session to `session_history` itself, because the MCP server cannot tell which session calls it.
- Installation keeps other plugin records and does not edit the older `config.toml` or `mcp.json`.

### CodeBuddy Code

[CodeBuddy Code](./20-codebuddy.md). A plugin with hooks, an MCP proxy, and 5 skills, installed by the unified installer as a local marketplace.

- `UserPromptSubmit` and `SessionEnd` fire **only in the interactive TUI**. A headless run (`-p`, `--input-format stream-json`) captures on `Stop` and never sends the session-end commit.
- The URI guard denies file tools whose path is a `viking://` URI. The denial reaches the model as the failed tool result; an `allow` carries no text through, so this host has no advisory channel. Shell commands are not checked, so `ov` calls and literal `viking://` arguments still work.

### Qoder CLI

[Qoder CLI](./21-qoder.md). A plugin with hooks, an MCP proxy, and 5 skills, installed by the unified installer as a local marketplace.

- `UserPromptSubmit` and `SessionEnd` fire in the TUI and in headless `-p` runs. Every `-p` run also ends an auxiliary session that has no transcript; its `SessionEnd` is ignored.
- Injected context is written into the transcript as attachment records. Capture reads only the conversation records, so recalled memories are not captured back.
- The URI guard denies file tools whose path is a `viking://` URI, and the denial reaches the model as the failed tool result, with an example `mcp_call` to the matching OpenViking tool. A Bash command that contains such a URI still runs, with a notice pointing at the same tools.
- MCP tools load lazily through `mcp_call`. In a headless run with the default permission mode, they need a permission rule or a permissive mode.

### Antigravity

[Community integrations: Antigravity](./08-community-plugins.md#antigravity-memory-integration). A plugin with hooks, an MCP proxy, and 5 skills, installed by the unified installer. The Antigravity desktop app and the `agy` CLI load the same plugin from `~/.gemini/config/plugins/openviking-memory`.

- `PreInvocation` runs before every model call, including each call in a tool loop (`invocationNum` above 0) and each reactive wakeup for a background task, subagent message, or `Stop` continuation. Recall runs only when `invocationNum` is 0 and no `MODEL` step has run since the latest `USER_INPUT` step in `transcriptPath`, so a turn injects once.
- Antigravity hooks use their own `hooks.json` format, with each top-level key naming a hook. Context goes back as a `systemMessage` step in `injectSteps`, which stays in the conversation.
- `Stop` carries no message text. Capture reads the finished steps of `transcriptPath`, preferring the untruncated `transcript_full.jsonl` beside it.
- The plugin directory's `mcp_config.json` registers the OpenViking MCP server. When `~/.gemini/config/mcp_config.json` already defines a server named `openviking`, the installer keeps the user's server and does not write the plugin's `mcp_config.json`, so no tool appears twice.
- The skills live in the plugin directory as `skills/<name>/SKILL.md`, and each is also invocable as `/<name>`.
- `PreToolUse` (matcher `*`) runs the URI guard: a file tool whose location argument (an argument whose name ends in `Path`, `File`, or `Directory`) is a `viking://` URI is denied. Shell commands (`run_command`) are not checked and get no notice.
- If `~/.gemini/config/plugins/openviking-memory` exists and OpenViking did not write it, the installer stops and `--uninstall` leaves it in place.

### OpenCode

[OpenCode Plugin](./10-opencode.md). The npm plugin `@openviking/opencode-plugin` runs the shared flow inside OpenCode. The plugin supports OpenCode 2 only; OpenCode 1.x needs `@openviking/opencode-plugin@2026.11.26`, the last release that supports it.

- Recall runs once per prompt and is stored in the user message's metadata; later model steps reuse it without new requests, which keeps the prompt cache stable. The session-start context goes with the first prompt.
- MCP tools keep the `openviking_` prefix, with `codemode: false`. The 5 skills are added only when the plugin registers its MCP server, not in hook-only mode or with `mcp.openviking` disabled.
- The URI guard denies `read`, `glob`, and `grep` calls whose path is a `viking://` URI before they run, with an example call to the matching `openviking_*` tool.

<a id="dsh-deepseek-harness"></a>

### DSH

[DSH Plugin](./17-dsh.md). An in-process Cordis plugin that runs the shared flow; it needs DSH 0.1.7-rc.2 or later. Tools come through `@deepseek-ai/dsh-mcp-client` and the shared stdio proxy.

- `ctx.provide("openvikingMemory")` lets other Cordis plugins build on it.
- The installer uses the `web` profile unless `--dsh-profile` names another. On macOS it installs into DeepSeek Harness Desktop's `desktop` profile as a separate harness, `dsh-desktop`, through Desktop's own `dsh` command ([DeepSeek Harness Desktop](./17-dsh.md#deepseek-harness-desktop)). Every mode except `dev` installs the published npm package.
- Injected context is a plugin message: instructions at session start, recall before a step. Session start waits while the agent is busy and the context goes with the next step.
- Tool results are captured by default (`captureToolResults: true`), so `ov-experience-memory` runs its full loop; turning capture off leaves it able only to find and apply Experience.
- The URI guard denies file tools on `viking://` paths in `tools/pre-execute` and adds a notice to shell commands.
- Credentials named in the Cordis patch override every other source. For behavior settings the patch is the lowest layer.
- `mcpEnabled: false` (in `ovcli.conf` `plugin` or `plugin.dsh`, or in the Cordis patch) mounts neither the MCP tools nor the bundled skills.

<a id="pi-pi-coding-agent-extension"></a>

### pi

[pi Coding Agent Extension](./11-pi.md). A native extension that runs the shared flow, with a `/viking` command. Tools use pi's built-in MCP client.

- Tools are whatever the server's `tools/list` returns, renamed `openviking_<tool>`. A server that adds or drops a tool changes pi's tools at the next session, without an extension release.
- Upgrading from 0.3.x renamed every tool from `viking_*` with no alias period. Update any `--tools` or `--exclude-tools` list, or the tools disappear silently.
- A tool call times out after 15 seconds (`OPENVIKING_TIMEOUT_MS`). A timeout or ESC fails the call only locally; the request already sent keeps running, so a write may still land.
- `tool_call` denies file tools on `viking://` paths and adds a notice to shell commands that contain one.
- Takeover (`takeoverEnabled`, off by default) is described in [pi takeover](#pi-takeover).
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

[Hermes Agent](./05-hermes.md). Depending on its version, Hermes uses the catalog plugin or the built-in provider named `openviking`:

- The **catalog plugin** is maintained in this repository under [`examples/hermes-plugin`](https://github.com/volcengine/OpenViking/tree/main/examples/hermes-plugin). Install it with `hermes plugins install openviking --enable`, then run `hermes memory setup openviking`.
- The **bundled provider** exists in earlier Hermes releases under `plugins/memory/openviking` and needs no installation. The Hermes rows on this page describe that provider at [Hermes commit `989798c`](https://github.com/NousResearch/hermes-agent/tree/989798cd5e691230b54b2ea72e5937b68133014c/plugins/memory/openviking).

A release that still includes the bundled provider loads it first. When an update removes the bundle from a profile already configured for OpenViking, Hermes attempts to install the catalog plugin automatically. The provider name, configuration, and stored data do not change.

The two are separate code bases. Where their behavior differs:

| | Bundled provider (Hermes `989798c`) | External plugin (this repository) |
|---|---|---|
| Commits during a session | Only at session boundaries | A background commit at 20,000 pending tokens (`commit_token_threshold`) |
| Recall digests | Not supported | Optional server digest (`recall_compress`) |
| Mirroring Hermes's built-in memory | Additions only | Additions, replacements, and removals, tracked in a URI registry |
| `viking_forget` | User memory files with an explicit user ID | Also accepts `viking://~/`, rejects user-ID-less layouts, and checks ownership before deleting |
| Cron, subagent, and flush contexts | Not documented at that commit | Recall works. Automatic writes and mirroring are skipped |

Behavior of the pinned bundled provider:

- Recall runs before every model call, through session-aware `search/search`, with `/find` as a fallback. Defaults: 6 results, score threshold 0.15, 4,000 characters, 4 seconds in total and 3 seconds per request.
- `viking_remember` sends the fact unchanged through its own session and commits it. It returns `status: submitted`.
- Commits leave no live messages (`keep_recent_count` 0). Uploads are not durable, but pending-commit markers let a later start commit sessions from a dead run on POSIX.
- Memories are written under `viking://user/<uid>/memories/`, or `viking://user/<uid>/peers/<peer>/memories/` when a peer is set.
- Linking an OpenViking `ovcli.conf` profile clears the five connection variables from the Hermes `.env`. `hermes backup` includes the default or environment-selected `ovcli.conf` under `$HOME`. Back up a YAML-linked file separately.

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

- **`examples/agent-hook-plugin/`** (Node.js) holds the shared library in `lib/` and the flow every host runs in `lib/flow/`: recall with its fallbacks, session-start context, capture, commits, the offline queue, the MCP proxy, session IDs, and credentials. A host is a directory under `hosts/` with a `host.mjs` that maps the host's events to the flow's stages and decodes its payloads and transcript; an in-process plugin passes its events to the same flow with `runStage`.
- **The Agent Plugins package** (`agent-plugins/`) is a portable `plugin.json`, `skills/`, and `mcp.json` with a stdio-to-HTTP proxy and no hooks. Its `plugin.test.mjs` checks conformance with the Agent Plugins specification and can serve as a lint baseline for your own package.

The [plugin development guide](./18-plugin-development.md) covers module ownership, configuration, generated copies, installers, and tests. Whatever you build, keep three rules so it behaves like the existing integrations:

1. Use context-mode recall with a session ID and `dedup_turns`, so the server can expand queries and deduplicate across turns.
2. Do not let the adapter's own timeout cut the deadline the shared code computes.
3. Commit at shutdown, or give sessions a server auto-commit policy with an idle timeout and turn on `memory.session_auto_commit.enabled`. Otherwise a conversation tail stays uncommitted until something else triggers a commit. Then check exit events and completed writes in the real host.

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
- Plugin settings and defaults: `examples/agent-hook-plugin/lib/config-schema.mjs`.
- The flow every plugin host runs: `examples/agent-hook-plugin/lib/flow/`, with the host contract in `host.d.mts`.
- Hosts: `examples/agent-hook-plugin/hosts/<host>/host.mjs` for the hook hosts, and `host.mjs` in `examples/opencode-plugin/`, `examples/dsh-memory-plugin/`, and `examples/pi-coding-agent-extension/`. The [host events](#host-events) table is generated from them by `examples/memory-plugin-shared/host-table.mjs`.
- pi takeover: `examples/pi-coding-agent-extension/lib/takeover-core.mjs`.
- OpenClaw tools and lifecycle hooks: `examples/openclaw-plugin/registries/openviking-tools.ts` and `examples/openclaw-plugin/plugin/openviking-lifecycle-hooks.ts`.
- Hermes: the bundled provider from earlier releases at the pinned commit linked in [Hermes](#hermes), and the catalog plugin's [README](https://github.com/volcengine/OpenViking/blob/main/examples/hermes-plugin/README.md).

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
