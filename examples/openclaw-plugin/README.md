# OpenClaw + OpenViking Context-Engine Plugin

Use [OpenViking](https://github.com/volcengine/OpenViking) as the long-term memory backend for [OpenClaw](https://github.com/openclaw/openclaw). In OpenClaw, this plugin is registered as the `openviking` context engine.

## Documentation

- Install and upgrade: [INSTALL.md](./INSTALL.md)
- Chinese install guide: [INSTALL-ZH.md](./INSTALL-ZH.md)
- Agent-oriented operator guide: [INSTALL-AGENT.md](./INSTALL-AGENT.md)

## Technical Architecture

### Plugin Responsibilities

This plugin is not only a memory retriever. In code it plays four roles at once:

- a `context-engine` plugin that implements `assemble`, `afterTurn`, and `compact`
- a hook layer that runs `before_prompt_build`, `session_start`, `session_end`, `agent_end`, and `before_reset`
- a tool provider that registers `memory_recall`, `memory_store`, `memory_forget`, and `ov_archive_expand`
- a runtime manager that can start and monitor a local OpenViking subprocess in `local` mode

OpenClaw still owns the agent runtime and prompt orchestration. OpenViking owns long-term memory retrieval, session archiving, and memory extraction.

### Identity and Routing

The plugin does not send a single global agent ID to OpenViking. It keeps OpenClaw session identity and OpenViking routing aligned:

- `sessionId` or `sessionKey` is converted into an OpenViking-safe session ID
- UUID session IDs are reused directly; other IDs fall back to a stable SHA-256 form when needed
- `X-OpenViking-Agent` is resolved per session, not per process
- if `plugins.entries.openviking.config.agentId` is not `default`, it becomes a prefix like `<configAgentId>_<sessionAgent>`
- routing and tenant headers are emitted by the client layer, and can be logged with `logFindRequests`

This matters because the plugin supports multi-agent and multi-session OpenClaw flows without mixing memory across sessions.

### Session Lifecycle

Session handling is the core design, and it spans both hooks and the context engine.

1. OpenClaw session identity is mapped to an OpenViking session ID.
2. `assemble()` asks OpenViking for session context with a token budget.
3. Returned archive summaries are rewritten into `[Session History Summary]` and `[Archive Index]`.
4. Active OpenViking messages are converted back into OpenClaw messages.
5. Tool calls and tool results are repaired so transcript structure stays provider-safe.
6. `afterTurn()` extracts only the new turn, sanitizes it, and appends it to the OpenViking session.
7. Once pending session tokens cross `commitTokenThreshold`, the plugin commits the OpenViking session and lets Phase 2 memory extraction continue asynchronously.
8. `compact()` performs a blocking commit, waits for archive generation, then re-reads compacted session context.

The result is a session model where OpenClaw sees a reduced working context, while OpenViking keeps the long-form archive and extracted memories.

### What `assemble()` Actually Builds

The assembled context is more than “old chat history”.

- archive overviews become a compact session summary block
- earlier archives are exposed as an ordered archive index
- active session messages stay uncompressed
- when precise details are missing, the model can call `ov_archive_expand` to reopen a specific archive

This is why the plugin can survive long sessions: it does not keep replaying the entire raw transcript into OpenClaw.

### Recall and Capture Pipeline

There are two memory loops around each turn.

Before generation:

- `before_prompt_build` extracts the latest user text
- it searches both `viking://user/memories` and `viking://agent/memories`
- results are deduplicated, reranked, and trimmed to a token budget
- the selected memories are injected as a `<relevant-memories>` block

After generation:

- `afterTurn` formats the new turn into capture text
- assistant text, `toolUse`, and `toolResult` content are preserved
- metadata noise and injected memory blocks are stripped before capture
- OpenViking session commit triggers archive + extraction

The reranking logic is query-aware. It boosts preferences, events, leaf memories, and lexical overlap instead of trusting vector score alone.

### Transcript Ingest Assist

The plugin also has a special path for transcript-like user input.

- multi-speaker text is detected with speaker-turn and length thresholds
- command text, metadata blocks, and pure question prompts are filtered out
- when a transcript-like ingest is detected, the plugin prepends a lightweight instruction so the model returns a short usable reply instead of `NO_REPLY`

This is aimed at memory ingestion workflows where the user pastes chat transcripts or conversation dumps.

### Local and Remote Runtime

#### Local Mode

In `local` mode, the plugin starts OpenViking itself.

- resolves Python from `OPENVIKING_PYTHON`, env files, or system defaults
- prepares the port before boot
- kills stale OpenViking processes on the same port
- auto-picks the next free port if the configured port is occupied by another process
- waits for `/health` before treating the service as ready
- caches the local client so multiple OpenClaw plugin contexts do not spawn duplicate runtimes

#### Remote Mode

In `remote` mode, the plugin only uses the HTTP API.

- no subprocess is started
- `baseUrl` and optional `apiKey` come from OpenClaw plugin config
- the same session, recall, archive, and tool flow still applies

### Tools and Operator Surfaces

The plugin exposes more than automatic memory behavior.

- `memory_recall`: search long-term memory explicitly
- `memory_store`: write text into an OpenViking session and force extraction
- `memory_forget`: delete a known memory URI or search and delete a strong single match
- `ov_archive_expand`: reopen a compressed session archive when the summary is not enough

For operators, the main surfaces are:

- Web Console: inspect OpenViking files and memory state
- `ov tui`: browse local OpenViking data in terminal
- `ov-install --current-version`: show installed plugin version and OpenViking version
- `openclaw config get plugins.entries.openviking.config`: inspect current plugin config

## Tools

### Web Console

OpenViking includes a Web Console for inspecting stored files, debugging ingestion, and checking memory state.

Example startup:

```bash
python -m openviking.console.bootstrap --host 0.0.0.0 --port 8020 --openviking-url http://127.0.0.1:1933
```

### `ov tui`

Use the terminal UI to browse OpenViking files locally:

```bash
ov tui
```

### Version Inspection

Show the installed plugin version and OpenViking version:

```bash
ov-install --current-version
```

### Plugin Config Inspection

Print the full current OpenClaw plugin config:

```bash
openclaw config get plugins.entries.openviking.config
```

### Logs

OpenClaw plugin log stream:

```bash
openclaw logs --follow
```

OpenViking service log, default local path:

```bash
cat ~/.openviking/data/log/openviking.log
```

## Troubleshooting

| Symptom | Likely Cause | What to Check |
| --- | --- | --- |
| `plugins.slots.contextEngine` is not `openviking` | Plugin slot was not set or was replaced | `openclaw config get plugins.slots.contextEngine` |
| Local mode does not start correctly | Python path, env file, or `ov.conf` is wrong | `source ~/.openclaw/openviking.env && openclaw gateway restart` |
| `port occupied` | Local OpenViking port is already used | Change `plugins.entries.openviking.config.port` or free the port |
| Recall works inconsistently across sessions | Agent/session routing is not what you expected | Enable `logFindRequests` and check `openclaw logs --follow` |
| Memory is not being captured after long chats | Pending tokens stay below `commitTokenThreshold` or extraction failed server-side | Check plugin config, OpenClaw logs, and `~/.openviking/data/log/openviking.log` |
| Session summaries are too lossy | You need archive expansion instead of summary-only context | Use `ov_archive_expand` with an ID from `[Archive Index]` |
| Installed versions are not what you expected | Plugin and OpenViking runtime versions diverged | Run `ov-install --current-version` |

## Usage Tutorials

### Inspect the Current Setup

Use these commands together:

```bash
ov-install --current-version
openclaw config get plugins.entries.openviking.config
openclaw config get plugins.slots.contextEngine
```

### Follow Recall and Capture

Watch the OpenClaw side:

```bash
openclaw logs --follow
```

Then inspect the OpenViking side:

```bash
cat ~/.openviking/data/log/openviking.log
```

This is the fastest way to see session commit, archive generation, memory recall, and memory extraction.

### Switch to Remote Mode

If you already have a remote OpenViking service:

```bash
openclaw config set plugins.entries.openviking.config.mode remote
openclaw config set plugins.entries.openviking.config.baseUrl http://your-server:1933
openclaw config set plugins.entries.openviking.config.apiKey your-api-key
openclaw config set plugins.entries.openviking.config.agentId your-agent-id
openclaw gateway restart
```

### Inspect Archived Session Details

When the model only has a session summary, reopen a concrete archive:

```text
Use ov_archive_expand with an archive ID from [Archive Index]
```

### Browse Stored Memory

Use the local terminal UI:

```bash
ov tui
```

Or use Web Console if you want a browser-based view of the stored memory data.

---

For installation, upgrade, and uninstall operations, use [INSTALL.md](./INSTALL.md).
