# Memory

Memory is produced by session commit or explicit extraction, stored in the user memory namespace, and consumed through the content, file-system, and retrieval APIs.

## Built-in Memory Types

| Category | Location | Description |
|----------|----------|-------------|
| profile | `user/memories/profile.md` | User profile information |
| preferences | `user/memories/preferences/` | User preferences by topic |
| entities | `user/memories/entities/` | Important entities (people, projects) |
| events | `user/memories/events/` | Significant events |
| identity | `user/memories/identity.md` | Assistant identity and self-introduction |
| soul | `user/memories/soul.md` | Assistant principles, boundaries, style, and continuity |
| cases | `user/memories/cases/` | Trainable and evaluable task cases |
| trajectories | `user/memories/trajectories/` | Reusable operation contracts |
| experiences | `user/memories/experiences/` | Reusable execution insights |
| tools | `user/memories/tools/` | Tool usage knowledge and best practices |
| skills | `user/memories/skills/` | Skill execution knowledge and workflow strategies |

These are the enabled built-in types. Deployments can extend or override them with custom memory templates.

---

## Retrieving Memory

Use [`POST /api/v1/search/search` with `mode="context"`](06-retrieval.md#searchmodecontext)
to assemble an injection-ready context block across memories, resources, and
skills. The same capability is available through the MCP `search` tool with
`mode="context"`.

The deprecated `/api/v1/search/recall` compatibility endpoint, which accepted
POST requests, has been removed. Clients that still use its v1 fields must
migrate them explicitly:

| Removed v1 field | Context search field | Migration |
|------------------|----------------------|-----------|
| `max_chars` | `max_tokens` | Divide by 4 and clamp to at least 64 tokens |
| `min_score` | `score_threshold` | Set `0.1` explicitly to preserve the old endpoint default |
| partial `quotas` | `quotas` | Overlay the partial map on the old bucket defaults before sending |
| `render: "compact"` | `detail: "abstract"` | Pins every returned category to the abstract tier |
| `render: false` | — | Ignore `rendered` and consume `entries` only |

To preserve the old preset completely, also send `purpose="coding"`,
`query_expansion="auto"`, and `dedup_turns=5` when a `session_id` is present.
If `quotas` were omitted before, specify the old bucket defaults explicitly:
`events=10`, `entities=10`, `preferences=3`, and `experiences=0`.

## Related Documentation

- [Sessions](05-sessions.md) - commit and extract
- [Retrieval](06-retrieval.md) - search memory
- [Content](12-content.md) - read memory content
