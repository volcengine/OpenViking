# Session Memory Extraction Flow

This document records the current implementation. It is meant to be used as a
code-modification reference, so it avoids proposed or removed flows.

## Policy

`memory_policy` only carries target switches:

```json
{
  "self": { "enabled": true },
  "peer": { "enabled": false }
}
```

Legacy `types` fields are ignored. Type selection is not controlled by
`memory_policy`.

## Memory Type Groups

| Group | Types | Target |
| --- | --- | --- |
| Long-term memory | `profile`, `preferences`, `entities`, `events`, `cases`, `patterns`, `tools`, `skills`, `identity`, `soul` | Self and peer |
| Agent memory | `trajectories`, `experiences` | Self only |
| Session skills | `SESSION_SKILL_MEMORY_TYPE` output | Self only |

Agent memory is enabled only when `config.memory.agent_memory_enabled` is true.
Session skill extraction also requires self memory to be enabled.

## Commit Flow

Implemented in `openviking/session/session.py`:

1. Merge session-level and commit-level policy with `MemoryPolicy.merge`.
2. Archive the current message batch.
3. Hydrate tool outputs for extraction.
4. If peer memory is enabled, collect safe `message.peer_id` values from the
   archived batch into `allowed_peer_ids`.
5. Start archive summary generation.
6. If long-term extraction is enabled, call
   `SessionCompressorV2.extract_long_term_memories` once with the full archived
   batch, `allow_self_memory`, and `allowed_peer_ids`.
7. If self agent extraction is enabled, call
   `SessionCompressorV2.extract_agent_memories` once with the full archived
   batch.

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
