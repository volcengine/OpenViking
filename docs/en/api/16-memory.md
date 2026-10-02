# Memory

Memory is produced by session commit or explicit extraction, stored in the user memory namespace, and consumed through the content, file-system, and retrieval APIs.

## Built-in Memory Types

| Category | Location | Description |
|----------|----------|-------------|
| profile | `viking://user/{user_id}/memories/profile.md` | User profile information |
| preferences | `viking://user/{user_id}/memories/preferences/` | User preferences by topic |
| entities | `viking://user/{user_id}/memories/entities/` | Important entities (people, projects) |
| events | `viking://user/{user_id}/memories/events/` | Significant events |
| identity | `viking://user/{user_id}/memories/identity.md` | Assistant identity and self-introduction |
| soul | `viking://user/{user_id}/memories/soul.md` | Assistant principles, boundaries, style, and continuity |
| cases | `viking://user/{user_id}/memories/cases/` | Trainable and evaluable task cases |
| trajectories | `viking://user/{user_id}/memories/trajectories/` | Reusable operation contracts distilled from agent task trajectories |
| experiences | `viking://user/{user_id}/memories/experiences/` | Reusable execution insights |
| tools | `viking://user/{user_id}/memories/tools/` | Tool usage knowledge and best practices |
| skills | `viking://user/{user_id}/memories/skills/` | Skill execution knowledge and workflow strategies |

These are the bundled types. `tools` and `skills` are disabled by default; extraction also depends on the effective memory policy and Agent Evolution settings. The table shows Self paths. Peer-enabled types can also be stored under `viking://user/{user_id}/peers/{peer_id}/memories/`. Deployment templates can extend or override the registry; the account template API permits only its documented fields and types.

---

## Memory Recall

Use [`search(mode="context")`](06-retrieval.md#search-mode-context) to retrieve memories and assemble an injection-ready block. The server MCP exposes the same operation as `search(mode="context")`; there is no separate `recall` tool.

```http
POST /api/v1/search/recall
Content-Type: application/json
```

`/api/v1/search/recall` is deprecated. It is a preset over `search(mode="context")` with `purpose="coding"` and the v1 defaults, kept for existing callers; responses carry a `Deprecation: true` header. Do not use it for new integrations.

## Related Documentation

- [Sessions](05-sessions.md) - commit and extract
- [Retrieval](06-retrieval.md) - search memory
- [Content](12-content.md) - read memory content
