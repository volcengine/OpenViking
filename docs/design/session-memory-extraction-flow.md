# Session Memory Extraction Flow

This document records the current implementation. It is meant to be used as a
code-modification reference, so it avoids proposed or removed flows.

## Policy

`memory_policy` carries target switches plus an optional global memory type
whitelist, and can disable per-archive Working Memory summaries:

```json
{
  "self": { "enabled": true },
  "peer": { "enabled": false },
  "working_memory": { "enabled": false },
  "memory_types": ["profile", "preferences"]
}
```

When `memory_types` is omitted or `null`, all enabled schemas from
`MemoryTypeRegistry` are allowed, including custom prompt/schema types. When it
is set, extraction is limited to those names for both self and peer writes.
When `working_memory.enabled` is `false`, commit still archives messages and
runs configured memory extraction, but skips the archive summary.

## Memory Type Groups

| Group | Types | Target |
| --- | --- | --- |
| User-memory extraction | Enabled registry schemas with `stage: user`, including `cases` | Self and peer, subject to schema policy |
| Case-driven training | `trajectories`, `experiences` | Self only |
| Executable session skills | Optional output of case-driven training | Self only |

Memory schemas default to `stage: user` and `peer_enabled: true`. Set
`peer_enabled: false` for user-stage schemas that should ignore `peer_id` and
`ranges` peer targets and remain under the current user space (for example
`cases`). Execution-derived types are not exposed to the ordinary user-memory
extractor.

`SessionCompressorV3.extract_long_term_memories` is the only public extraction
entry. It trains trajectories, experiences, and optional executable session
skills only when ordinary extraction produces at least one case. An explicit
execution-only `memory_types` policy does not invoke ordinary extraction, so it
cannot create a case and does not trigger training.

## Commit Flow

Implemented in `openviking/session/session.py`:

1. Load the session-level policy from session metadata.
2. Archive the current message batch.
3. Hydrate tool outputs for extraction.
4. If peer memory is enabled, collect safe `message.peer_id` values from the
   archived batch into `allowed_peer_ids`.
5. Start archive summary generation.
6. Remove execution-derived types from the schema whitelist passed to ordinary
   extraction. If enabled user-memory types remain, call
   `SessionCompressorV3.extract_long_term_memories` once with the full archived
   batch, `allow_self_memory`, and `allowed_peer_ids`.
7. V3 applies ordinary memory operations and collects extracted `cases`. When
   at least one case exists, V3 runs streaming training for trajectories and
   experiences and, when enabled, an executable session skill. With no case,
   all three training outputs are skipped.

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
| Schema has `peer_enabled: false` | Ignore `peer_id` and `ranges` peer targets; write self if self memory is enabled |
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

- V3 user-memory extraction sees the full archived batch once.
- The extractor may emit self and peer operations in the same response.
- Final write targets are decided per operation by the isolation handler.
- Peer writes require safe peer IDs observed in the archived batch.
- `trajectories`, `experiences`, and executable session skills are trained only
  from an extracted case and never write peer memory.
