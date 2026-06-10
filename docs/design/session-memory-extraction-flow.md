# Session Memory Extraction Flow

This document records the current implementation. It is meant to be used as a
code-modification reference, so it avoids proposed or removed flows.

## Policy

`memory_policy` carries target switches plus an optional global memory type
whitelist:

```json
{
  "self": { "enabled": true },
  "peer": { "enabled": false },
  "memory_types": ["profile", "preferences"]
}
```

When `memory_types` is omitted or `null`, all enabled schemas from
`MemoryTypeRegistry` are allowed, including custom prompt/schema types. When it
is set, extraction is limited to those names for both self and peer writes.

## Memory Type Groups

| Group | Types | Target |
| --- | --- | --- |
| Long-term memory extraction | Enabled registry schemas without `agent_only` | Self and peer |
| Execution memory extraction | Execution-derived schemas, currently `trajectories`, `experiences` | Self only |
| Session skills | `SESSION_SKILL_MEMORY_TYPE` output | Self only |

Trajectory/experience extraction is controlled by `memory_types`: omitted or
`null` allows both, while an explicit list must include those names. Session
skill extraction also requires self memory to be enabled and only runs when the
execution memory extraction phase has work.

## Commit Flow

Implemented in `openviking/session/session.py`:

1. Load the session-level policy from session metadata.
2. Archive the current message batch.
3. Hydrate tool outputs for extraction.
4. If peer memory is enabled, collect safe `message.peer_id` values from the
   archived batch into `allowed_peer_ids`.
5. Start archive summary generation.
6. If long-term memory extraction is enabled and allowed by `memory_types`, call
   `SessionCompressorV2.extract_long_term_memories` once with the full archived
   batch, `allow_self_memory`, and `allowed_peer_ids`.
7. If trajectory/experience extraction has work, call
   `SessionCompressorV2.extract_execution_memories` once with the full archived
   batch. When session skill extraction is enabled, it runs inside this execution
   phase instead of starting a separate phase by itself.

The current flow does not build separate buckets such as
`self_identity_messages`, `self_experience_messages`,
`peer_user_message_groups`, or `peer_assistant_message_groups`.

## Long-Term Routing

Implemented in `openviking/session/memory/memory_isolation_handler.py`.

`MemoryIsolationHandler.calculate_memory_uris` resolves each extracted operation
independently:

| Operation fields | Result |
| --- | --- |
| No `peer_id`, no `ranges` | Write self if self memory is enabled |
| Safe `peer_id` in `allowed_peer_ids` | Write that peer |
| Unsafe `peer_id` | Skip |
| Safe but unallowed `peer_id` | Skip |
| `ranges` present | Read the message range; no-peer messages route to self, allowed peer messages route to peer |
| Only disabled targets found | Skip |

The router does not rewrite message roles. A `role=user` message remains user
content, a `role=assistant` message remains assistant content, and tool parts
stay on the message where they were recorded.

## Storage Targets

For current user space `viking://user/<user_id>`:

| Target | Storage space |
| --- | --- |
| Self | `viking://user/<user_id>/...` |
| Peer | `viking://user/<user_id>/peers/<peer_id>/...` |

Peer-only extraction does not initialize self default files. Default self files
are initialized only when `allow_self_memory` is true.

## Practical Invariants

- Long-term extraction sees the full archived batch once.
- The extractor may emit self and peer operations in the same response.
- Final write targets are decided per operation by the isolation handler.
- Peer writes require safe peer IDs observed in the archived batch.
- `trajectories`, `experiences`, and session skills never write peer memory.
