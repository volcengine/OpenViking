# OpenViking for OpenClaw

Use [OpenViking](https://github.com/volcengine/OpenViking) as OpenClaw's long-term context engine: automatic recall, session archive, memory extraction, semantic search, and RAG over a remote OpenViking server.

## Quick Start

```bash
openclaw plugins install clawhub:@openviking/openclaw-plugin
openclaw openviking setup --base-url http://my-server:1933 --api-key sk-xxx --json
openclaw gateway restart
openclaw openviking status --json
```

That's it. The `setup` command activates the context-engine slot and validates the connection.

### Or ask your agent

> Install the OpenClaw plugin @openviking/openclaw-plugin for OpenViking remote memory. My server is at `http://my-server:1933` and my API key is `sk-xxx`.

The agent runs install → setup → restart → verify automatically. See [INSTALL-AGENT.md](./INSTALL-AGENT.md).

## How It Works

| Stage | What happens |
|-------|-------------|
| **Every turn** (`afterTurn`) | New messages are appended to an OpenViking session; commit/extraction is threshold-triggered |
| **Explicit remember** (`memory_store`) | Important long-term facts can be written and committed immediately |
| **On `/compact`** (`compact`) | Pending session messages are committed and extracted into long-term memories |
| **Before each reply** (`assemble`) | Relevant memories are auto-retrieved and injected into context |

## Tools

Once installed, the plugin provides these agent tools:

| Tool | Purpose |
|------|---------|
| `memory_recall` | Explicit long-term memory search |
| `memory_store` | Persist explicit long-term facts immediately |
| `memory_forget` | Delete memories by URI or query |
| `ov_archive_search` | Search across archives by keyword |
| `ov_archive_expand` | Expand an archive back to raw messages |
| `ov_recall_trace` | Inspect why recall/search returned or injected specific results |
| `add_resource` | Import documents, URLs, or Git repos when explicitly enabled |
| `add_skill` | Import OpenViking skills |
| `ov_search` | Search imported resources and skills |
| `ov_read` | Read the full original content of one exact OpenViking URI |
| `ov_multi_read` | Read the full original content of multiple OpenViking URIs |
| `ov_list` | List OpenViking directories after search to inspect sibling chunks and overview files |
| `openviking_tool_result_read` | Restore the full original content of an externalized tool result |
| `openviking_tool_result_search` | Search inside an externalized tool result by keyword |
| `openviking_tool_result_list` | List externalized tool results in the current session |

`add_resource` is hidden from agents by default (`enableAddResourceTool=false`), while manual `/add-resource` remains available. Configure `recallTargetTypes` to choose default recall targets (`user`, `agent`, `resource`); legacy `recallResources=true` appends `resource` only when `recallTargetTypes` is unset.

## Data Flow & Privacy

- **What is sent**: User/assistant message text from each turn (after stripping injected memory blocks and metadata noise).
- **Where it goes**: Your configured OpenViking server (`baseUrl`). The plugin only sends data to that server; downstream model/provider data handling (embedding, VLM) depends on the server's configuration.
- **Storage**: All data lives on your OpenViking server under `viking://user/*` (including `viking://user/sessions/*`) and `viking://resources/*`.
- **API Key**: Sent as `X-API-Key` header over your configured connection. Never logged or forwarded.
- **Multi-tenant isolation**: Supports `accountId` and `userId`. Optional `peer_role` / `peer_prefix` controls whether OpenClaw speakers are written as OpenViking `peer_id`.

## Verify

```bash
openclaw openviking status --json     # one-shot health check
openclaw config get plugins.slots.contextEngine  # should output: openviking
```

## Documentation

| Doc | Description |
|-----|-------------|
| [INSTALL.md](./INSTALL.md) | Full install, upgrade, and uninstall guide |
| [INSTALL-ZH.md](./INSTALL-ZH.md) | Chinese install guide |
| [INSTALL-AGENT.md](./INSTALL-AGENT.md) | Agent-oriented operator guide |
| [docs/openviking-openclaw-plugin-guide.md](./docs/openviking-openclaw-plugin-guide.md) | Comprehensive Chinese guide for usage, configuration, debugging, testing, build, release, deployment, and rollback |

> **Plugin vs Skill**: This page is for `@openviking/openclaw-plugin` (the context-engine plugin). Do **not** use `clawhub install openviking` — that installs a different AgentSkill.

---

<details>
<summary><b>Technical Overview (for integrators and engineers)</b></summary>

This plugin is registered as the `openviking` context engine in OpenClaw.

## Design Positioning

- OpenClaw still owns the agent runtime, prompt orchestration, and tool execution.
- OpenViking owns long-term memory retrieval, session archiving, archive summaries, and memory extraction.
- `examples/openclaw-plugin` is not a narrow "memory lookup" plugin. It is an integration layer that spans the OpenClaw lifecycle.

In the current implementation, the plugin plays four roles at once:

- `context-engine`: implements `assemble`, `afterTurn`, and `compact`
- hook layer: handles `session_start`, `session_end`, and `before_reset`
- tool provider: registers memory/archive tools plus OpenViking resource and skill import tools
- runtime manager: connects to and monitors a remote OpenViking service

## Overall Architecture

![Overall OpenClaw and OpenViking plugin architecture](./images/openclaw-plugin-engine-overview.png)

The diagram above reflects the current implementation boundary:

- OpenClaw remains the primary runtime on the left. The plugin does not take over agent execution.
- The middle layer combines hooks, the context engine, tools, and runtime management in one plugin registration.
- All HTTP traffic goes through `OpenVikingClient`, which centralizes tenant headers and routing logs.
- The OpenViking service owns sessions, memories, archives, and Phase 2 extraction, with storage under `viking://user/*` (including `viking://user/sessions/*`) and `viking://resources/*`.

That split lets OpenClaw stay focused on reasoning and orchestration while OpenViking becomes the source of truth for long-lived context.

## Identity and Routing

The plugin keeps OpenClaw session identity in session and peer metadata. It does not send an OpenViking agent identity or create an agent namespace.

The main rules are:

- reuse `sessionId` directly when it is already a UUID
- prefer `sessionKey` when deriving a stable `ovSessionId`
- normalize unsafe path characters, or fall back to a stable SHA-256 when needed
- `peer_role=none` is the default and does not write `peer_id` on session messages
- `peer_role=assistant` writes assistant messages with `peer_id=<sessionAgent>`; if `peer_prefix` is set, the value becomes `<peer_prefix>_<sessionAgent>`
- `peer_role=person` writes user messages with `peer_id` derived from OpenClaw sender identity; assistant messages do not get `peer_id`
- recall/search requests also send the same resolved `peer_id` when `peer_role` is `assistant` or `person`
- when OpenClaw does not provide a session agent, use its default agent `main` for local session and assistant peer metadata
- only add `X-OpenViking-Account` / `X-OpenViking-User` when `accountId` / `userId` are explicitly configured

This matters because OpenViking tenant identity is account/user-scoped, while OpenClaw agent identity is runtime metadata.

The recommended remote-mode configuration only needs:

- `baseUrl`
- `apiKey`
- optionally `peer_role`
- optionally `peer_prefix` when `peer_role=assistant`

In this setup:

- `apiKey` should usually be a user key
- new installs default to `peer_role=none`
- `accountId` / `userId` are advanced options only when the deployment needs explicit identity headers, such as root-key or trusted-server flows

### Canonical user namespace

The plugin writes and searches user-scoped memory. `viking://user/memories` is expanded to `viking://user/<user_id>/memories` using the identity resolved from the configured user key. `viking://agent/...` is deprecated by OpenViking and is not used by the plugin.

## assemble Recall Flow

![Automatic recall flow before prompt build](./images/openclaw-plugin-recall-flow.png)

Auto-recall now runs through `assemble()`. OpenClaw calls the same context engine method in two shapes, and the plugin assigns different responsibilities to each shape:

1. Preflight assemble: params include `prompt`; `messages` is still old history. The plugin reads archive/session context back from OpenViking and rebuilds history.
2. transformContext assemble: params do not include `prompt`; the latest `messages` entry is already the current user turn. The plugin only runs long-term recall and prepends the memory block to that user message content.

During recall, the plugin:

1. Extracts query text from the latest user message.
2. Resolves the agent routing for the current `sessionId/sessionKey`.
3. Runs a quick availability precheck so model requests do not stall when OpenViking is unavailable.
4. Queries the configured `recallTargetTypes` (`user,agent` by default; optionally `resource`; use `ov_archive_search` and `ov_archive_expand` for session history).
5. Deduplicates, threshold-filters, reranks, and trims the results under a token budget.
6. Prepends the selected memories as a `## Long-term Memories` section inside `<openviking-context>` to the current user message; it does not append a standalone synthetic user message.

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
- it strips injected `<openviking-context>` blocks, historical `<relevant-memories>` blocks, and metadata noise before capture
- it appends the sanitized turn text into the OpenViking session

After that, the plugin checks `pending_tokens`. Once the session crosses `commitTokenThreshold`, it triggers `commit(wait=false)`:

- archive generation and Phase 2 memory extraction continue asynchronously on the server
- the current turn is not blocked waiting for extraction
- if `logFindRequests` is enabled, the logs include the task id and follow-up extraction detail

This automatic path is best-effort and commit-dependent. Short but important facts can stay only in the live session until a threshold commit, `/compact`, or an explicit store happens.

### Explicit long-term memory writes

When the user explicitly asks the agent to remember, save, or store an important long-term fact, preference, project, or decision, prefer `memory_store` over waiting for normal auto-capture. `memory_store` writes the text to an OpenViking session and calls `commit(wait=true)`, so it is the reliable integration-side path for facts that should be available as long-term memory as soon as possible.

Use it as a complement to auto-capture, not a replacement:

- auto-capture still preserves ordinary conversation flow and batches extraction for cost and latency
- `memory_store` is for explicit durable-memory intent such as "remember my main project is X" or "save this preference"
- if `memory_store` commits but extracts 0 memories, check the OpenViking server extraction/model configuration; the explicit path triggered extraction, but the extractor did not produce a memory

### What `compact()` does

`compact()` is the stricter synchronous boundary:

- it calls `commit(wait=true)` and blocks for completion
- when an archive exists, it re-reads `latest_archive_overview`
- it returns updated token estimates, the latest archive id, and summary content
- if the summary is too coarse, the model can call `ov_archive_expand` to reopen a specific archive

So `afterTurn()` is closer to "incremental append plus threshold-triggered async commit", while `compact()` is the explicit "wait for archive and compaction to finish" boundary.

## Tools and Expandability

Beyond automatic behavior, the plugin exposes these tools directly:

- `memory_recall`: explicit long-term memory search
- `memory_store`: write explicit long-term facts into an OpenViking session and trigger commit
- `memory_forget`: delete by URI, or search first and remove a single strong match
- `ov_archive_expand`: expand a concrete archive back into raw messages
- `ov_recall_trace`: inspect recent recall/search trace records when `traceRecall` is enabled
- `add_resource`: import a document, directory, URL, or Git repository as an OpenViking resource when explicitly enabled
- `add_skill`: import or register an OpenViking skill
- `ov_search`: search OpenViking resources and skills, especially after importing them
- `ov_read`: read one exact `viking://` URI returned by `ov_search` or `ov_list`
- `ov_multi_read`: read multiple exact `viking://` URIs, useful for an overview plus sibling chunks
- `ov_list`: list a hit's parent directory after `ov_search` to recover sibling chunks, `.overview.md`, and related split-document context

They serve different roles:

- automatic recall covers the default case where the model does not know what to search yet
- `memory_recall` gives the model an explicit follow-up search path
- `memory_store` is for immediately persisting clearly important information when the user expresses durable-memory intent
- `ov_archive_expand` is the "go back to archive detail" escape hatch when summaries are not enough
- `add_resource` lets the agent save explicit document or repository import requests without asking the user to remember slash commands
- `add_skill` imports skills into OpenViking, while `add_resource` imports resources
- `ov_search` closes the loop after import by letting the user or agent confirm and consume resources and skills
- `ov_read` turns a ranked hit into original evidence before answering precise documentation, codebase, configuration, or procedural questions
- `ov_multi_read` reads overview and sibling chunks together when a split document needs more context than a single hit
- `ov_list` complements `ov_search` when a ranked hit is only one chunk of a larger procedure or document

`ov_archive_expand` is especially important because `assemble()` normally returns archive summaries and indexes, not the full raw transcript.

### Resource and Skill Import

Resource and skill imports are intentionally separate because they land in different OpenViking namespaces and use different server APIs:

- resources go through `/api/v1/resources` and land under `viking://resources/...`
- skills go through `/api/v1/skills` and land under `viking://user/skills/...`

The plugin also registers explicit slash commands for manual imports:

```text
/add-resource ./README.md --to viking://resources/openviking-readme --wait
/add-skill ./skills/install-openviking-memory --wait
/ov-search "OpenViking install" --uri viking://resources/openviking-readme
/ov-search "memory install skill" --uri viking://user/skills
```

Resource import supports remote URLs, Git URLs, local files, local directories, and uploaded zip files. OpenViking's built-in parsers cover common documents and media such as Markdown, text, PDF, HTML, Word, PowerPoint, Excel, EPUB, images, audio, and video. Directory imports also accept common code, documentation, and config file extensions such as `.py`, `.js`, `.ts`, `.go`, `.rs`, `.java`, `.cpp`, `.json`, `.yaml`, `.toml`, `.csv`, `.rst`, `.proto`, `.tf`, and `.vue`.

For HTTP safety, the plugin never sends a direct local filesystem path to the OpenViking server. Local files and directories are first uploaded through `/api/v1/resources/temp_upload`; directories are zipped locally with a pure JavaScript zip implementation before upload.

## Runtime Mode

![Runtime modes and routing behavior](./images/openclaw-plugin-runtime-routing.png)

The plugin operates exclusively in remote mode as a pure HTTP client:

- `baseUrl` and optional `apiKey` come from plugin config
- no local subprocess is started or managed
- session context, memory search/read, commit, and archive expansion behavior stays the same

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
openclaw openviking status --json
openclaw plugins list
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

</details>
