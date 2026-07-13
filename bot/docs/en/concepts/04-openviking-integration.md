# VikingBot and OpenViking Integration

OpenViking is VikingBot's long-term context layer. VikingBot handles real-time conversations, model inference, and tool execution. OpenViking provides unified storage and retrieval for Resources, Memories, and Skills, and consolidates reusable memories and experiences from Sessions.

## Integration Goals

```text
OpenViking → VikingBot
  Resource: provide knowledge and file context for tasks
  Memory: provide the current user/Peer's Profile, preferences, entities, and events
  Experience: provide methods the Agent used to complete similar tasks in the past
  Session: provide compressed history and conversation archives

VikingBot → OpenViking
  Add Resources
  Record session messages and context usage
  Commit Sessions to trigger summary, memory, and experience extraction
  Explicitly commit information that the user asks to remember long term
```

Together, the two systems form a context loop: recall → execute → receive feedback → consolidate → recall again.

## Connection Modes

VikingBot resolves its OpenViking connection from the same `ov.conf` and supports three topologies:

| Mode | Configuration source | Behavior |
|------|----------------------|----------|
| **Inherited** | Inherit the root-level `server` | The Bot runs with the current OpenViking Server |
| **Explicit** | `bot.ov_server.server_url` | The Bot connects to another OpenViking Server |
| **Standalone** | No available Server URL | Basic chat remains available, while OpenViking capabilities are degraded |

`openviking-server --with-bot` uses **Inherited** mode: the Server starts a managed VikingBot Gateway and passes its own connection information to the Bot. The configuration example below is also Inherited mode: the root-level `server` defines the current OpenViking Server, while `bot.ov_server` supplies only the credentials used by the Bot and does not set `server_url`. To use **Explicit** mode with another OpenViking Server, configure both its target URL and credentials under `bot.ov_server`.

Example:

```json
{
  "server": {
    "auth_mode": "api_key",
    "host": "127.0.0.1",
    "port": 1933
  },
  "bot": {
    "ov_server": {
      "api_key": "<openviking-user-api-key>",
      "account_id": "default"
    }
  }
}
```

## Authentication and Identity Model

OpenViking connections support User keys and Root keys:

| `api_key_type` | Typical scenario | Meaning |
|----------------|------------------|---------|
| `user` | `api_key` / `dev` auth mode | Access OpenViking as a User |
| `root` | `trusted` auth mode | The Gateway uses a Root key and forwards trusted identity headers |

If `api_key_type` is not configured explicitly, VikingBot derives it from the effective auth mode of OpenViking Server in the same `ov.conf`.

In the current User/Peer model:

- the principal that owns the Bot API key is the User;
- the current message sender is represented as a Peer under that User;
- `actor_peer_id` is the trusted Peer identifier for the current sender;
- Peer Profile and long-term memory recall center on `actor_peer_id`.

A Gateway request may carry a request-scoped `openviking_connection` containing the account, user, agent, actor peer, role, and namespace policy. Only a trusted Server proxy may supply this field. An ordinary client request body cannot establish this identity by itself.

## Client Selection

OpenViking access primarily uses `VikingClient`:

```text
Request-scoped openviking_connection is present
  → Create a temporary VikingClient for the request
  → Use the authenticated request identity
  → Close it when the call finishes

No request-scoped connection
  → Use the global bot.ov_server configuration
  → Reuse clients by workspace and event loop
```

The request-level connection takes priority so a multi-user Gateway does not accidentally use the Bot's global identity. Global clients are also isolated by asyncio event loop, preventing training or multithreaded runs from reusing a connection object bound to another loop.

## Workspace Mapping

VikingBot uses SandboxManager to compute the workspace ID:

| Sandbox mode | OpenViking workspace ID |
|--------------|-------------------------|
| `shared` | `shared` |
| `per-session` | The safe SessionKey name |
| `per-channel` | `type__channel_id` |

This ID separates OpenViking clients, Sessions, and experience context associated with Bot workspaces. Identity isolation still follows OpenViking account/user/agent/peer rules; a workspace ID never replaces authentication.

## Automatic Context Recall

ContextBuilder assembles OpenViking context before the first model call for each user message. Later tool iterations in the same turn reuse this base context and may append Experiences when a write tool or Skill Hook triggers.

### Peer Profile

VikingBot first reads the current `actor_peer_id` Profile and injects it into the system prompt as information about the current sender. A channel's `memory_peer` configuration or request metadata may add more Peers for recall.

The legacy `memory_user` field remains only for owner-user query compatibility. New configurations should use `memory_peer`.

### User and Peer Memories

Recall uses type quotas by default:

| Type | Default count | Content |
|------|---------------|---------|
| `events` | 10 | Historical events and decisions related to the current task |
| `entities` | 10 | People, projects, organizations, and other entities |
| `preferences` | 3 | User preferences and constraints |

Profile uses a separate read path and does not consume a search candidate slot. `memory_recall_max_chars` controls the total injected character budget. Results are deduplicated and sorted, then progressively degraded from full content to summary or URI so relevant memories are not dropped completely when the budget is tight.

### Experiences

Experiences store reusable methods learned from tasks the Agent completed in the past. VikingBot supports two recall points:

1. retrieve Experiences directly from the current task;
2. after the Agent reads a Skill, use the Skill name or description in the `tool.post_call` Hook to retrieve related Experiences and append them to the Skill content.

`exp_recall_limit` controls the number of results, and `exp_recall_max_chars` controls the injected budget. With `recall_exp_first_round_only=true`, Experiences are injected only on the first turn. This is useful for one-shot tasks or evaluation but not for long conversations.

### Experience Reminders Before Writes

`exp_write_tools` specifies which tool calls trigger additional experience recall. The defaults are `write_file` and `edit_file`. AgentLoop uses recent user messages to retrieve Experiences and adds the result to the current context before the write occurs.

This setting controls only the Bot-side recall timing. Whether OpenViking generates Experiences is governed by the Session memory policy.

## OpenViking Tools

When a channel enables `ov_tools_enable`, the Agent can use:

| Tool | Capability |
|------|------------|
| `openviking_list` | Browse a Viking URI directory |
| `openviking_search` | Semantically search Resources, Memories, and Skills |
| `openviking_grep` | Run regular-expression searches over OpenViking content |
| `openviking_glob` | Search URI paths with glob patterns |
| `openviking_multi_read` | Read the full content of multiple URIs concurrently |
| `openviking_add_resource` | Add a URL, local file, or code resource |
| `openviking_memory_commit` | Explicitly commit long-term memory from the current Session |

OpenViking tools obtain the current actor peer and request-scoped connection through ToolContext. Retrieval covers Resources, Peer Memories, and Skill paths accessible to the current identity.

`openviking_add_resource` starts asynchronous resource processing and is not registered in `readonly` mode. `openviking_memory_commit` is intended for cases where the user explicitly asks the Agent to remember something.

## Local Sessions and OpenViking Sessions

The two Session types have different responsibilities:

| Session | Storage | Responsibility |
|---------|---------|----------------|
| VikingBot Session | Local JSONL | Runtime history, channel state, tool events, replies, and feedback |
| OpenViking Session | OpenViking Server | Message archival, compressed summaries, memory extraction, and experience extraction |

VikingBot Session metadata tracks OpenViking synchronization state:

- the OpenViking session ID;
- the last synchronized local message index;
- the last committed message index;
- the current pending token count;
- the latest synchronization status and error.

## Incremental Synchronization and Automatic Commit

```text
Read unsynchronized messages from the local Session
  → append_messages to the OpenViking Session
  → Update last_synced_local_index
  → Query pending_tokens
  → Reach a token/message threshold or force a commit
  → commit_session
  → Update last_commit_local_index
```

The `message.compact` Hook performs this synchronization. Important settings include:

| Setting | Purpose |
|---------|---------|
| `agents.commit_token_threshold` | Commit when pending tokens reach this value |
| `agents.commit_keep_recent_count` | Number of recent messages retained after commit |
| `agents.memory_window` | Local history window, also used as a message-count commit threshold |

Messages use local indexes for incremental synchronization, avoiding repeated appends on every turn. A synchronization failure is stored in metadata and logged, but optional memory functionality does not block basic chat.

## Compressed Session Context

By default, model history comes from the most recent `memory_window` messages in the local Session. With `agents.session_context_enabled=true`, VikingBot can load compressed history from OpenViking Session and limit it with `session_context_token_budget`.

Before a new turn, if history reaches the threshold, AgentLoop first synchronizes and commits the OpenViking Session, then builds the new prompt context. This prevents long conversations from growing without bound.

## Explicit Memory Commit

When the user explicitly asks to remember information long term, the Agent invokes `openviking_memory_commit`:

```text
Current Bot Session messages
  → Append to OpenViking Session
  → Commit
  → Wait for or query the background task
  → Return created/updated/deleted Memory URIs
```

VikingBot does not perform active memory consolidation in `readonly` mode or when the channel disables OpenViking tools.

## Experience Loop

The complete loop is:

```text
Current task
  → Retrieve Resource / Peer Memory / Experience
  → Agent uses Skills and tools to complete the task
  → Local Session records messages, tools, and outcome
  → Incrementally synchronize and commit the OpenViking Session
  → OpenViking extracts memories and experiences
  → Recall them in a later task
```

Resources provide external knowledge, Peer Memories provide information about the current user, and Experiences describe how the Agent completed similar tasks in the past. These context types have different responsibilities but share Viking URI and the OpenViking retrieval interface.

## Gateway Proxy

After an OpenViking Server is configured, VikingBot Gateway proxies `/api/v1/{path}` to the upstream service. The proxy:

1. validates the Gateway Token or local request boundary;
2. calls upstream `/health` to confirm the effective auth mode;
3. resolves a User key or trusted identity;
4. removes hop-by-hop headers;
5. forwards authentication headers and preserves the response status.

Bot Chat and OpenViking APIs can therefore share one Gateway address, while OpenViking Server still performs the final identity validation.

## Degradation and Error Boundaries

| Condition | Behavior |
|-----------|----------|
| No OpenViking Server configured | Basic Bot chat continues; OpenViking recall and tools are unavailable or skipped |
| Automatic memory recall fails | Log the failure and continue the model call |
| Session synchronization fails | Record the synchronization error and preserve the local Session |
| Request-scoped identity is untrusted | Gateway rejects the request |
| Upstream auth mode differs from configuration | Gateway rejects the proxy or chat request |
| `ov_tools_enable=false` | Do not inject OpenViking memory or expose OpenViking tools |

## Optional FUSE Mount

`openviking_mount` also provides optional FUSE mounting that maps OpenViking content into a local directory and creates or removes mount points by Session. This is not part of the default AgentLoop path. By default, the Bot accesses OpenViking through VikingClient and `openviking_*` tools.

## Implementation Locations

| Area | Path |
|------|------|
| Connection configuration and merge logic | `vikingbot/config/loader.py`, `schema.py` |
| VikingClient adapter | `vikingbot/openviking_mount/ov_server.py` |
| Automatic recall | `vikingbot/agent/memory.py`, `context.py` |
| OpenViking tools | `vikingbot/agent/tools/ov_file.py` |
| Session synchronization state | `vikingbot/openviking_mount/session_state.py` |
| Compact and Experience Hooks | `vikingbot/hooks/builtins/openviking_hooks.py` |
| Gateway proxy and identity resolution | `vikingbot/channels/openapi.py` |
| Optional mounting | `vikingbot/openviking_mount/manager.py`, `session_integration.py` |

## Related Documentation

- [VikingBot Architecture](./01-architecture.md)
- [Agent Capabilities](./02-agent-capabilities.md)
- [Channels, Gateway, and Operations](./03-channels-and-gateway.md)
- [OpenViking Architecture](../../../../docs/en/concepts/01-architecture.md)
- [OpenViking Context Types](../../../../docs/en/concepts/02-context-types.md)
- [OpenViking Session Management](../../../../docs/en/concepts/08-session.md)
