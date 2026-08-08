# Agent Evolution

The Agent Evolution API reports trajectories that consumed a specific Experience and their outcome distribution. These operations are currently available through HTTP only.

## API Reference

### List Experience application trajectories

Return a paginated list of trajectories that successfully read the specified Experience. The query is restricted to Experiences and trajectories owned by the current user.

**Code Entry Points**:

- `openviking/server/routers/agent_evolution.py:list_experience_trajectories` - HTTP route
- `openviking/service/agent_evolution_service.py:AgentEvolutionService.list_trajectories_by_experience` - Core implementation

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| experience_uri | string | Yes | - | Experience file URI in the current user space |
| limit | integer | No | 50 | Page size from 1 through 1000 |
| offset | integer | No | 0 | Zero-based result offset |

**HTTP API**

```
GET /api/v1/agent-evolution/experiences/trajectories?experience_uri={experience_uri}&limit=50&offset=0
```

```bash
curl -X GET "http://localhost:1933/api/v1/agent-evolution/experiences/trajectories?experience_uri=viking://user/default/memories/experiences/exchange.md&limit=50&offset=0" \
  -H "X-API-Key: your-key"
```

**Response Example**

```json
{
  "status": "ok",
  "result": {
    "experience_uri": "viking://user/default/memories/experiences/exchange.md",
    "items": [
      {
        "uri": "viking://user/default/memories/trajectories/exchange_20260805020000.md",
        "name": "exchange_20260805020000.md",
        "description": "Handle an exchange request",
        "created_at": "2026-08-05T02:00:00Z",
        "updated_at": "2026-08-05T02:00:00Z"
      }
    ],
    "total": 1,
    "limit": 50,
    "offset": 0,
    "has_more": false
  },
  "time": 0.01
}
```

Each item contains only the indexed fields that are present among `uri`, `name`, `description`, `created_at`, and `updated_at`.

---

### Get Experience outcome distribution

Count trajectories that consumed the specified Experience across the five supported outcomes. The query uses exact scalar-tag aggregation and does not load every trajectory file.

**Code Entry Points**:

- `openviking/server/routers/agent_evolution.py:get_experience_outcome_distribution` - HTTP route
- `openviking/service/agent_evolution_service.py:AgentEvolutionService.get_experience_outcome_distribution` - Core implementation

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| experience_uri | string | Yes | - | Experience file URI in the current user space |

**HTTP API**

```
GET /api/v1/agent-evolution/experiences/outcomes?experience_uri={experience_uri}
```

```bash
curl -X GET "http://localhost:1933/api/v1/agent-evolution/experiences/outcomes?experience_uri=viking://user/default/memories/experiences/exchange.md" \
  -H "X-API-Key: your-key"
```

**Response Example**

```json
{
  "status": "ok",
  "result": {
    "experience_uri": "viking://user/default/memories/experiences/exchange.md",
    "outcome_distribution": [
      {"outcome": "success", "count": 4},
      {"outcome": "failure", "count": 1},
      {"outcome": "partial", "count": 0},
      {"outcome": "unknown", "count": 0},
      {"outcome": "unfinished", "count": 0}
    ]
  },
  "time": 0.01
}
```

The response always includes `success`, `failure`, `partial`, `unknown`, and `unfinished`. Trajectories created by older versions and not yet re-indexed do not carry outcome tags and are therefore excluded.

## MCP tool contract

Both queries above are fed by the MCP tools the agent actually calls during a session. The tools are served by the server's `/mcp` endpoint, so every harness connected to OpenViking MCP gets them without any plugin-side implementation.

After a session is committed, the server attributes usage from the recorded tool calls: each result in a `search_experience` output becomes one `memory.recalled` event, each successful `read_experience` becomes one `memory.injected` event and tags the trajectory with its source Experience. The tool names and JSON payload shapes are therefore a fixed contract — changing them zeroes out the statistics. Attribution strips the namespace prefix a harness adds to MCP tools (for example `mcp__openviking__`), so bare and prefixed names both count.

### `search_experience`

| Field | Type | Description |
|-------|------|-------------|
| `query` | string | Required. The task or situation to search for. |
| `limit` | integer | Optional. Clamped to `[1, 20]`, defaults to `5`. |

The search is pinned to the current user's `viking://user/<user>/memories/experiences/` and applies no score threshold.

```json
{"results": [{"uri": "viking://user/alice/memories/experiences/no-order-exchange.md", "title": "no-order-exchange", "score": 0.61, "snippet": "The customer wants an exchange without an order number..."}]}
```

Every `uri` is canonical and owned by the current user; internal files such as `.abstract.md`, `.overview.md`, and `.relations.json` never appear. `snippet` is truncated to 120 characters.

### `read_experience`

| Field | Type | Description |
|-------|------|-------------|
| `uri` | string | Required. A canonical URI returned by `search_experience`, owned by the current user. |

```json
{"uri": "viking://user/alice/memories/experiences/no-order-exchange.md", "content": "## Situation\n..."}
```

A non-canonical URI (for example one carrying a `?` or `#` suffix), another user's URI, or a non-Experience URI raises a tool error rather than returning an empty result — a failed call must not be counted as an injection.

## Related Documentation

- [Sessions](05-sessions.md) - Commit sessions and generate Agent Evolution memories
- [Memory](16-memory.md) - Read and recall memories
