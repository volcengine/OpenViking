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

## API Reference

### recall()

Search each memory type independently and assemble a bounded memory block that can be injected directly into Agent context. By default, recall searches `events`, `entities`, and `preferences`; the `experiences` quota defaults to `0` and must be enabled explicitly.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `query` | string | Yes | - | Recall query |
| `quotas` | object | No | `events=10, entities=10, preferences=3, experiences=0` | Maximum results for each type |
| `max_chars` | integer | No | `6500` | Maximum rendered memory-block length |
| `min_score` | number | No | `0.1` | Minimum relevance score |
| `peer_scope` | string | No | `all` | `actor` searches only the current actor peer; `all` also searches global and other-peer memory |
| `other_peer_penalty` | number/object | No | Per-type defaults | Score penalty applied to results from other peers |
| `render` | boolean | No | `true` | Whether to produce the `rendered` memory block |

**HTTP API**

```http
POST /api/v1/search/recall
Content-Type: application/json
```

```bash
curl -X POST http://localhost:1933/api/v1/search/recall \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "query":"OpenViking API documentation preferences",
    "quotas":{"events":5,"entities":5,"preferences":3,"experiences":2},
    "max_chars":6500,
    "peer_scope":"all"
  }'
```

**MCP**

```text
recall(
  query="OpenViking API documentation preferences",
  quotas={"events": 5, "entities": 5, "preferences": 3, "experiences": 2},
  max_chars=6500,
  peer_scope="all"
)
```

**Response**

The response contains `entries`, `rendered`, and `stats`. `entries` preserves structured matches; `rendered` is the bounded text intended for direct context injection.

The public Python, TypeScript, and Go SDKs and the `ov` CLI do not currently wrap type-quota recall, so this section shows only the HTTP tab and the existing MCP call.

## Related Documentation

- [Sessions](05-sessions.md) - commit and extract
- [Retrieval](06-retrieval.md) - search memory
- [Content](12-content.md) - read memory content
