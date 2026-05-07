# OpenClaw + OpenViking Context-Engine Plugin

Use [OpenViking](https://github.com/volcengine/OpenViking) as the long-term memory backend for [OpenClaw](https://github.com/openclaw/openclaw). In OpenClaw, this plugin is registered as the `openviking` context engine.

This document is not an installation guide. It is an implementation-focused design note for integrators and engineers. It describes how the plugin works today based on the code under `examples/openclaw-plugin`, not a future refactor target.

## Documentation

- Install and upgrade: [INSTALL.md](./INSTALL.md)
- Chinese design and install guide: [INSTALL-ZH.md](./INSTALL-ZH.md)
- Agent-oriented operator guide: [INSTALL-AGENT.md](./INSTALL-AGENT.md)

## Design Positioning

- OpenClaw still owns the agent runtime, prompt orchestration, and tool execution.
- OpenViking owns long-term memory retrieval, session archiving, archive summaries, and memory extraction.
- `examples/openclaw-plugin` is not a narrow "memory lookup" plugin. It is an integration layer that spans the OpenClaw lifecycle.

In the current implementation, the plugin plays four roles at once:

- `context-engine`: implements `assemble`, `afterTurn`, and `compact`
- hook layer: handles `session_start`, `session_end`, `agent_end`, and `before_reset`
- tool provider: registers memory/archive tools plus OpenViking resource and skill import tools
- runtime manager: connects to and monitors a remote OpenViking service

## Overall Architecture

![Overall OpenClaw and OpenViking plugin architecture](./images/openclaw-plugin-engine-overview.png)

The diagram above reflects the current implementation boundary:

- OpenClaw remains the primary runtime on the left. The plugin does not take over agent execution.
- The middle layer combines hooks, the context engine, tools, and runtime management in one plugin registration.
- All HTTP traffic goes through `OpenVikingClient`, which centralizes `X-OpenViking-*` headers and routing logs.
- The OpenViking service owns sessions, memories, archives, and Phase 2 extraction, with storage under `viking://user/*`, `viking://agent/*`, and `viking://session/*`.

That split lets OpenClaw stay focused on reasoning and orchestration while OpenViking becomes the source of truth for long-lived context.

## Identity and Routing

The plugin does not send one fixed agent ID to OpenViking. It tries to keep OpenClaw session identity and OpenViking routing aligned.

The main rules are:

- reuse `sessionId` directly when it is already a UUID
- prefer `sessionKey` when deriving a stable `ovSessionId`
- normalize unsafe path characters, or fall back to a stable SHA-256 when needed
- resolve `X-OpenViking-Agent` per session, not per process
- when `plugins.entries.openviking.config.agent_prefix` is non-empty, prefix the session agent as `<agent_prefix>_<sessionAgent>`
- when OpenClaw does not provide a session agent, use its default agent `main`
- send `X-OpenViking-Agent` on OpenViking requests, including startup health checks
- only add `X-OpenViking-Account` / `X-OpenViking-User` when `accountId` / `userId` are explicitly configured

This matters because the plugin is built to support multi-agent and multi-session OpenClaw usage without mixing memories across sessions.

The recommended remote-mode configuration only needs:

- `baseUrl`
- `apiKey`
- `agent_prefix`

In this setup:

- `apiKey` should usually be a user key
- `accountId` / `userId` are advanced options only when the deployment needs explicit identity headers, such as root-key or trusted-server flows
- `isolateUserScopeByAgent` / `isolateAgentScopeByUser` must match the server-side account namespace policy when using the PR #1356 canonical namespace model
- `agentScopeMode` is a deprecated compatibility alias for older hash-based routing and should only be used against older servers

### Canonical namespace policy

For OpenViking servers that include PR #1356, the plugin no longer treats agent or user scope as a locally computed hash. Instead it expands shorthand aliases into canonical URIs using the configured namespace policy:

- `viking://user/memories`
  - `viking://user/<user_id>/memories` when `isolateUserScopeByAgent=false`
  - `viking://user/<user_id>/agent/<agent_id>/memories` when `isolateUserScopeByAgent=true`
- `viking://agent/memories`
  - `viking://agent/<agent_id>/memories` when `isolateAgentScopeByUser=false`
  - `viking://agent/<agent_id>/user/<user_id>/memories` when `isolateAgentScopeByUser=true`

The plugin cannot auto-discover this policy today because `/api/v1/system/status` does not expose it. Configure the two booleans explicitly so they stay aligned with the server-side account policy.

## assemble Recall Flow

![Automatic recall flow before prompt build](./images/openclaw-plugin-recall-flow.png)

Auto-recall now runs through `assemble()`. OpenClaw calls the same context engine method in two shapes, and the plugin assigns different responsibilities to each shape:

1. Preflight assemble: params include `prompt`; `messages` is still old history. The plugin reads archive/session context back from OpenViking and rebuilds history.
2. transformContext assemble: params do not include `prompt`; the latest `messages` entry is already the current user turn. The plugin only runs long-term recall and prepends the memory block to that user message content.

During recall, the plugin:

1. Extracts query text from the latest user message.
2. Resolves the agent routing for the current `sessionId/sessionKey`.
3. Runs a quick availability precheck so model requests do not stall when OpenViking is unavailable.
4. Queries both `viking://user/memories` and `viking://agent/memories` in parallel.
5. Deduplicates, threshold-filters, reranks, and trims the results under a token budget.
6. Prepends the selected memories as a `<relevant-memories>` block to the current user message; it does not append a standalone synthetic user message.

The reranking logic is not pure vector-score sorting. The current implementation also considers:

- whether a result is a leaf memory with `level == 2`
- whether it looks like a preference memory
- whether it looks like an event memory
- lexical overlap with the current query

## Session Lifecycle

![Session lifecycle and compaction boundary](./images/openclaw-plugin-session-lifecycle.png)

Session handling is the main axis of this design. In the current implementation it covers history assembly, incremental append, asynchronous commit, and blocking compaction readback.

### What `assemble()` does

During preflight, `assemble()` is not just replaying old chat history. It reads session context back from OpenViking under a token budget, then rebuilds OpenClaw-facing messages:

- `latest_archive_overview` becomes `[Session History Summary]`
- `pre_archive_abstracts` becomes `[Archive Index]`
- active session messages stay in message-block form
- assistant tool parts become `toolCall` (input compatible: `toolUse`/`input` is normalized to `toolCall`/`arguments`)
- tool output becomes separate `toolResult`
- the final message list goes through a tool-use/result pairing repair pass

That means OpenClaw sees "compressed history summary + archive index + active messages", not an ever-growing raw transcript.

### What `afterTurn()` does

`afterTurn()` has a narrower job: append only the new turn into the OpenViking session.

- it slices only the newly added messages
- it keeps only `user` / `assistant` capture text
- it preserves `toolCall` / `toolResult` content in the serialized turn text
- it strips injected `<relevant-memories>` blocks and metadata noise before capture
- it appends the sanitized turn text into the OpenViking session

After that, the plugin checks `pending_tokens`. Once the session crosses `commitTokenThreshold`, it triggers `commit(wait=false)`:

- archive generation and Phase 2 memory extraction continue asynchronously on the server
- the current turn is not blocked waiting for extraction
- if `logFindRequests` is enabled, the logs include the task id and follow-up extraction detail

### What `compact()` does

`compact()` is the stricter synchronous boundary:

- it calls `commit(wait=true)` and blocks for completion
- when an archive exists, it re-reads `latest_archive_overview`
- it returns updated token estimates, the latest archive id, and summary content
- if the summary is too coarse, the model can call `ov_archive_expand` to reopen a specific archive

So `afterTurn()` is closer to "incremental append plus threshold-triggered async commit", while `compact()` is the explicit "wait for archive and compaction to finish" boundary.

## Tools and Expandability

Beyond automatic behavior, the plugin exposes six tools directly:

- `memory_recall`: explicit long-term memory search
- `memory_store`: write text into an OpenViking session and trigger commit
- `memory_forget`: delete by URI, or search first and remove a single strong match
- `ov_archive_expand`: expand a concrete archive back into raw messages
- `ov_import`: import a resource or skill; defaults to resource and uses `kind: "skill"` for skills
- `ov_search`: search OpenViking resources and skills, especially after importing them

They serve different roles:

- automatic recall covers the default case where the model does not know what to search yet
- `memory_recall` gives the model an explicit follow-up search path
- `memory_store` is for immediately persisting clearly important information
- `ov_archive_expand` is the "go back to archive detail" escape hatch when summaries are not enough
- `ov_import` lets the agent complete explicit import requests without asking the user to remember slash commands
- `ov_search` closes the loop after import by letting the user or agent confirm and consume resources and skills

`ov_archive_expand` is especially important because `assemble()` normally returns archive summaries and indexes, not the full raw transcript.

### Resource and Skill Import

Resource and skill imports are intentionally separate because they land in different OpenViking namespaces and use different server APIs:

- resources go through `/api/v1/resources` and land under `viking://resources/...`
- skills go through `/api/v1/skills` and land under `viking://agent/skills/...`

The plugin also registers explicit slash commands for manual imports:

```text
/ov-import ./README.md --to viking://resources/openviking-readme --wait
/ov-import ./skills/install-openviking-memory --kind skill --wait
/ov-search "OpenViking install" --uri viking://resources/openviking-readme
/ov-search "memory install skill" --uri viking://agent/skills
```

Resource import supports remote URLs, Git URLs, local files, local directories, and uploaded zip files. OpenViking's built-in parsers cover common documents and media such as Markdown, text, PDF, HTML, Word, PowerPoint, Excel, EPUB, images, audio, and video. Directory imports also accept common code, documentation, and config file extensions such as `.py`, `.js`, `.ts`, `.go`, `.rs`, `.java`, `.cpp`, `.json`, `.yaml`, `.toml`, `.csv`, `.rst`, `.proto`, `.tf`, and `.vue`.

For HTTP safety, the plugin never sends a direct local filesystem path to the OpenViking server. Local files and directories are first uploaded through `/api/v1/resources/temp_upload`; directories are zipped locally with a pure JavaScript zip implementation before upload.

## Runtime Mode

![Runtime modes and routing behavior](./images/openclaw-plugin-runtime-routing.png)

The plugin operates exclusively in remote mode as a pure HTTP client:

- `baseUrl` and optional `apiKey` come from plugin config
- no local subprocess is started or managed
- session context, memory find/read, commit, and archive expansion behavior stays the same

The OpenViking service must be deployed and running independently before the plugin can connect to it.

## Relationship to the Older Design Draft

The repo also contains a more future-looking design draft at `docs/design/openclaw-context-engine-refactor.md`. It is important not to conflate the two:

- this README describes current implemented behavior
- the older draft discusses a stronger future move into context-engine-owned lifecycle control
- in the current version, the main automatic recall path lives in `assemble()`: preflight rebuilds history, transformContext injects long-term memories
- in the current version, `afterTurn()` already appends to the OpenViking session, but commit remains threshold-triggered and asynchronous on that path
- in the current version, `compact()` already uses `commit(wait=true)`, but it is still focused on synchronous commit plus readback rather than owning every orchestration concern

That distinction matters, otherwise the future design draft is easy to misread as already shipped behavior.

## Operator and Debugging Surfaces

If you need to debug this plugin, start with these entry points.

### Inspect the current setup

```bash
ov-install --current-version
openclaw config get plugins.entries.openviking.config
openclaw config get plugins.slots.contextEngine
```

### Watch logs

OpenClaw plugin logs:

```bash
openclaw logs --follow
```

OpenViking service logs:

```bash
cat ~/.openviking/data/log/openviking.log
```

### Web Console

```bash
python -m openviking.console.bootstrap --host 0.0.0.0 --port 8020 --openviking-url http://127.0.0.1:1933
```

### `ov tui`

```bash
ov tui
```

### Common things to check

| Symptom | More likely cause | First check |
| --- | --- | --- |
| `plugins.slots.contextEngine` is not `openviking` | The plugin slot was never set, or another plugin replaced it | `openclaw config get plugins.slots.contextEngine` |
| Cannot connect to OpenViking service | `baseUrl` is wrong or the service is down | Check `baseUrl` in config and test connectivity manually |
| recall behaves inconsistently across sessions | Routing identity is not what you expected | Enable `logFindRequests`, then inspect `openclaw logs --follow` |
| long chats stop extracting memory | `pending_tokens` never crosses the threshold, or Phase 2 fails server-side | Check plugin config and `~/.openviking/data/log/openviking.log` |
| summaries are too coarse for detailed questions | You need archive-level detail, not just summary | Use an ID from `[Archive Index]` with `ov_archive_expand` |

---

For installation, upgrade, and uninstall operations, use [INSTALL.md](./INSTALL.md).
