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

> **Deprecated**: `/api/v1/search/recall` is now a thin preset over [`/api/v1/search/search` with `mode="context"`](06-retrieval.md#searchmodecontext) and carries no assembly logic of its own. New integrations should target the context face directly; the v1 field aliases are accepted only here and will be removed in the next minor release. Responses carry a `Deprecation: true` header.

Search each memory type independently and assemble a bounded memory block that can be injected directly into Agent context. Relative to the context face, `/recall` overlays `purpose="coding"`, the v1-compatible `score_threshold=0.1`, `dedup_turns=5` when a `session_id` is present, and `query_expansion="auto"`. Coding Agent plugins explicitly send `score_threshold=0.35`; the public `/recall` default remains `0.1` so an unchanged request does not silently lose results after upgrading. Omitting `quotas` keeps v1's bucket defaults (`events=10, entities=10, preferences=3, experiences=0`); sending `"quotas": null` explicitly opts into the `purpose` preset ratios instead.

**v1 field folding**

| v1 field | Folds into | Notes |
|----------|------------|-------|
| `max_chars` | `max_tokens = max_chars / 4` | `6500` → `1625`; an explicit `max_tokens` wins |
| `min_score` | `score_threshold` | When neither is sent, the v1-compatible default `0.1` applies |
| `render: true` | No `detail` pin | Default behavior: each category takes its default tier |
| `render: false` | Returns `entries` only, `rendered` empty | |
| `render: "compact"` | `detail="abstract"` | The prototype-era compact mode; pins every category |
| v1 `quotas` keys | Overlaid on the v1 bucket defaults | Key names unchanged; a partial map keeps the other buckets |

Context-face parameters (`max_tokens`, `detail`, `dedup_turns`, `session_id`, `query_expansion`, `exclude_uris`, `purpose`, `rewrite`, `rewrite_max_bullets`) are also accepted here, so plugins can transition smoothly on deployments that have not been upgraded yet.

**HTTP API**

```http
POST /api/v1/search/recall
Content-Type: application/json
```

```bash
curl -X POST http://localhost:1933/api/v1/search/recall \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $OPENVIKING_API_KEY" \
  -d '{
    "query":"API documentation preferences",
    "quotas":{"events":5,"entities":5,"preferences":3,"experiences":2},
    "max_chars":6500,
    "peer_scope":"all"
  }'
```

**MCP**

```text
recall(
  query="API documentation preferences",
  quotas={"events": 5, "entities": 5, "preferences": 3, "experiences": 2},
  max_chars=6500,
  peer_scope="all"
)
```

**Response**

The response shape matches the context face (flat entries, flat XML in `rendered`):

```json
{
  "status": "ok",
  "result": {
    "entries": [
      {
        "uri": "viking://user/default/memories/preferences/api-docs.md",
        "category": "preferences",
        "score": 0.82,
        "detail": "full",
        "text": "User prefers API docs to show HTTP, SDK and CLI examples together.",
        "origin": "self"
      }
    ],
    "rendered": "<memory uri=\"viking://user/default/memories/preferences/api-docs.md\" type=\"preferences\" score=\"0.82\" detail=\"full\">\nUser prefers API docs to show HTTP, SDK and CLI examples together.\n</memory>",
    "digest": "",
    "stats": {
      "quotas": {"events": 5, "entities": 5, "preferences": 3, "experiences": 2},
      "candidates": 4,
      "returned": 1,
      "dropped": 0,
      "max_tokens": 1625,
      "used_tokens": 96,
      "tier_counts": {"full": 1},
      "peer_scope": "all",
      "origins": {"actor_peer": 0, "self": 1, "other_peer": 0},
      "deprecated": {
        "endpoint": "/api/v1/search/recall",
        "successor": "/api/v1/search/search?mode=context",
        "aliases_used": ["max_chars"]
      }
    }
  }
}
```

See [Retrieval - search(mode="context")](06-retrieval.md#searchmodecontext) for field meanings. Shape changes relative to v1: `type` → `category`, `mode` → `detail`, `content`/`summary` → `text`, `rendered` moves from three-level nesting to flat `<memory>` tags, and `rank` is no longer returned.

The public Python, TypeScript, Go SDKs and the `ov` CLI do not wrap this endpoint yet, so this section shows only the HTTP tab plus the MCP call that does exist.

## Related Documentation

- [Sessions](05-sessions.md) - commit and extract
- [Retrieval](06-retrieval.md) - search memory
- [Content](12-content.md) - read memory content
