---
name: openviking-context-database
description: >
  Use OpenViking from OpenClaw after @openviking/openclaw-plugin is installed:
  long-term memory, session archives, resource and skill import, semantic search,
  recall trace debugging, and externalized tool-result recovery.
version: 2026.6.12
metadata:
  openclaw:
    requires:
      plugin: "@openviking/openclaw-plugin"
tags:
  - openviking
  - context-engine
  - memory
  - resources
  - recall-trace
---

# OpenViking Context Database

Use this skill after `@openviking/openclaw-plugin` is installed and configured. For first-time setup, use `install-openviking-memory` instead.

## Safety Rules

- The plugin is remote-only. It talks to an existing OpenViking HTTP server and does not start the server.
- Do not invent OpenViking REST endpoints. Use the OpenClaw tools and slash commands exposed by the plugin.
- `add_resource` is disabled by default. Use manual `/add-resource`, or use `add_resource` only when it is explicitly enabled and the user explicitly asks to import, upload, save, or index a resource.
- Do not use `add_resource` during search, retrieval, URI reading, or search-result optimization. Use `ov_search`, `ov_list`, `ov_read`, and `ov_multi_read`.
- `viking://...` values are OpenViking virtual URIs, not local file paths. Do not read them with filesystem tools.
- Never echo API keys or tenant credentials.

## Configuration

Useful inspection commands:

```bash
openclaw openviking status --json
openclaw config get plugins.entries.openviking.config
openclaw config get plugins.slots.contextEngine
```

Common config fields:

| Field | Default | Purpose |
|---|---:|---|
| `baseUrl` | `http://127.0.0.1:1933` | OpenViking server URL. |
| `apiKey` | empty | Optional API key, also available through `OPENVIKING_API_KEY`. |
| `peer_role` | `none` | Controls peer-scoped writes/searches: `none`, `assistant`, or `person`. |
| `peer_prefix` | empty | Optional prefix for assistant peer IDs. |
| `autoCapture` | `true` | Capture new turns into the OpenViking session. |
| `autoRecall` | `true` | Inject relevant memory before replies. |
| `recallTargetTypes` | `user,agent` | Default recall targets. Allowed: `user`, `agent`, `session`, `resource`. |
| `recallResources` | `false` | Deprecated compatibility shortcut that appends `resource` when `recallTargetTypes` is unset. |
| `traceRecall` | `false` | Record recall/search trace entries in memory. |
| `traceRecallPersist` | `false` | Persist recall traces as JSONL. |
| `enabledTools` | `default` | Agent-visible tool allowlist. Accepts tool names or groups. |
| `disabledTools` | `add_resource` | Agent-visible tool blocklist applied after `enabledTools`. |
| `enableAddResourceTool` | `false` | Explicitly expose `add_resource` to agents. |

Tool groups for `enabledTools` and `disabledTools`: `default`, `all`, `memory`, `resource_query`, `import`, `recall_trace`, `archive`, `tool_result`.

## Tool Selection

| User intent | Use |
|---|---|
| Recall preferences, durable facts, or prior decisions | `memory_recall` |
| Store an important fact immediately | `memory_store` |
| Delete a known memory | `memory_forget` |
| Search imported resources or skills | `ov_search` |
| Inspect folders or sibling chunks from a search hit | `ov_list` |
| Read one exact `viking://...` URI | `ov_read` |
| Read several exact `viking://...` URIs | `ov_multi_read` |
| Recover exact details from archived session history | `ov_archive_search`, then `ov_archive_expand` |
| Explain why recall/search returned results | `ov_recall_trace` |
| Import a resource | `/add-resource`, or `add_resource` if explicitly enabled |
| Import an Agent Skill | `add_skill` |
| Recover externalized large tool output | `openviking_tool_result_list`, `openviking_tool_result_search`, `openviking_tool_result_read` |

## Recall Workflow

1. Use `memory_recall` when the user asks about known preferences, previous decisions, or durable facts.
2. Leave `targetUri` unset for normal recall. The plugin uses `resourceTypes` from the tool call or configured `recallTargetTypes`.
3. Use `resourceTypes: ["resource"]` when the user explicitly wants shared imported knowledge instead of personal memory.
4. Use `resourceTypes: ["session"]` only when an active OpenClaw/OpenViking session identity is available.
5. If the answer requires exact original source text, follow returned URIs with `ov_read` or `ov_multi_read`.

## Resource Workflow

1. Search with `ov_search`.
2. If the hit appears to be part of a split document, inspect the parent with `ov_list`.
3. Read exact hits with `ov_read` or several related hits with `ov_multi_read`.
4. Do not treat `viking://...` as a local path.

## Archive Workflow

Use archive tools when current summaries are not detailed enough:

1. Extract 2-3 concrete keyword variants from the user question.
2. Run `ov_archive_search` with one keyword or short phrase at a time.
3. If a match identifies the right archive, use `ov_archive_expand`.
4. Only conclude that old detail is unavailable after trying distinct keyword variants.

## Recall Trace Workflow

Recall tracing is off by default. If `traceRecall=true`, use `ov_recall_trace` to inspect recent traces.

Useful filters:

- `source`: `auto_recall`, `memory_recall`, `ov_search`, or `ov_archive_search`.
- `turn`: `latest` or `all`.
- `resourceTypes`: `user`, `agent`, `session`, `resource`.
- `includeContent`: read selected/displayed URI previews on demand.

Use recall trace to answer diagnostic questions such as:

- why an auto-recall item was injected
- which target URIs were searched
- whether a search failed or was skipped
- whether a candidate was filtered by score, leaf-level, dedupe, or budget

## Import Guidance

For resources, prefer slash command import:

```text
/add-resource /path/to/docs --parent viking://resources/project --wait
```

Use `add_skill` when the user wants an Agent Skill registered into OpenViking. Good skill files have precise frontmatter, trigger-oriented descriptions, clear scope boundaries, and concrete operating steps.
