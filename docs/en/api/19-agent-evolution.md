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

## Related Documentation

- [Sessions](05-sessions.md) - Commit sessions and generate Agent Evolution memories
- [Memory](16-memory.md) - Read and recall memories
