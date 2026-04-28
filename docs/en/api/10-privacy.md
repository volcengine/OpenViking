# Privacy Configs

Privacy configs manage sensitive values by `category + target_key` (for example, a skill's `api_key` and `base_url`).

Each update creates a version snapshot. You can query history and switch the active version.

## Endpoint Summary

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/privacy-configs` | List privacy config categories |
| GET | `/api/v1/privacy-configs/{category}` | List targets under a category |
| GET | `/api/v1/privacy-configs/{category}/{target_key}` | Get active config (`meta + current`) |
| POST | `/api/v1/privacy-configs/{category}/{target_key}` | Upsert and activate a new/current version |
| GET | `/api/v1/privacy-configs/{category}/{target_key}/versions` | List version numbers |
| GET | `/api/v1/privacy-configs/{category}/{target_key}/versions/{version}` | Get a specific version snapshot |
| POST | `/api/v1/privacy-configs/{category}/{target_key}/activate` | Activate a specific version |

Detailed sections are below.

---

## Common Use Cases

- Store sensitive settings for a skill
- Rotate keys by writing a new version
- Roll back to an old version
- Restore placeholders in skill content at read time

---

## Data Structures

### current (active version snapshot)

```json
{
  "version": 3,
  "category": "skill",
  "target_key": "byted-viking-search-knowledgebase",
  "values": {
    "api_key": "***",
    "base_url": "https://example.com"
  },
  "created_at": "2026-04-27T10:00:00+08:00",
  "created_by": "alice",
  "change_reason": "rotate key"
}
```

### meta

```json
{
  "category": "skill",
  "target_key": "byted-viking-search-knowledgebase",
  "active_version": 3,
  "latest_version": 5,
  "created_at": "2026-04-21T10:00:00+08:00",
  "updated_at": "2026-04-27T10:00:00+08:00",
  "updated_by": "alice",
  "last_accessed_at": "2026-04-27T10:00:00+08:00",
  "labels": {
    "env": "prod"
  }
}
```

---

## API Reference

### list_privacy_categories()

List categories that have privacy configs for the current user.

**HTTP API**

```
GET /api/v1/privacy-configs
```

```bash
curl -X GET http://localhost:1933/api/v1/privacy-configs \
  -H "X-API-Key: your-key" \
  -H "X-OpenViking-Account: default" \
  -H "X-OpenViking-User: alice"
```

**Response**

```json
{
  "status": "ok",
  "result": ["skill"],
  "time": 0.01
}
```

---

### list_privacy_targets()

List target keys under a category.

**HTTP API**

```
GET /api/v1/privacy-configs/{category}
```

```bash
curl -X GET http://localhost:1933/api/v1/privacy-configs/skill \
  -H "X-API-Key: your-key" \
  -H "X-OpenViking-Account: default" \
  -H "X-OpenViking-User: alice"
```

**Response**

```json
{
  "status": "ok",
  "result": ["byted-viking-search-knowledgebase"],
  "time": 0.01
}
```

---

### get_privacy_current()

Get active config for a target (`meta + current`).

**HTTP API**

```
GET /api/v1/privacy-configs/{category}/{target_key}
```

```bash
curl -X GET "http://localhost:1933/api/v1/privacy-configs/skill/byted-viking-search-knowledgebase" \
  -H "X-API-Key: your-key" \
  -H "X-OpenViking-Account: default" \
  -H "X-OpenViking-User: alice"
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "meta": {
      "category": "skill",
      "target_key": "byted-viking-search-knowledgebase",
      "active_version": 3,
      "latest_version": 5
    },
    "current": {
      "version": 3,
      "category": "skill",
      "target_key": "byted-viking-search-knowledgebase",
      "values": {
        "api_key": "***",
        "base_url": "https://example.com"
      }
    }
  },
  "time": 0.01
}
```

> Returns `NOT_FOUND` if the target does not exist.

---

### upsert_privacy_config()

Write a new version and set it as active.

**Behavior**

- `values` is written as a full snapshot for that version
- New keys are allowed and persisted
- If `values` is identical to the current version, no new version is created

**HTTP API**

```
POST /api/v1/privacy-configs/{category}/{target_key}
```

**Request Body**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| values | object | Yes | - | Privacy key-value pairs |
| change_reason | string | No | "" | Reason for change |
| labels | object | No | null | Labels stored in meta |

```bash
curl -X POST "http://localhost:1933/api/v1/privacy-configs/skill/byted-viking-search-knowledgebase" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -H "X-OpenViking-Account: default" \
  -H "X-OpenViking-User: alice" \
  -d '{
    "values": {
      "api_key": "secret-2",
      "base_url": "https://example.com",
      "region": "cn"
    },
    "change_reason": "rotate key",
    "labels": {
      "env": "prod"
    }
  }'
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "version": 4,
    "category": "skill",
    "target_key": "byted-viking-search-knowledgebase",
    "values": {
      "api_key": "secret-2",
      "base_url": "https://example.com",
      "region": "cn"
    },
    "change_reason": "rotate key"
  },
  "time": 0.02
}
```

---

### list_privacy_versions()

List all version numbers for a target.

**HTTP API**

```
GET /api/v1/privacy-configs/{category}/{target_key}/versions
```

```bash
curl -X GET "http://localhost:1933/api/v1/privacy-configs/skill/byted-viking-search-knowledgebase/versions" \
  -H "X-API-Key: your-key" \
  -H "X-OpenViking-Account: default" \
  -H "X-OpenViking-User: alice"
```

**Response**

```json
{
  "status": "ok",
  "result": [1, 2, 3, 4],
  "time": 0.01
}
```

> Returns `NOT_FOUND` if the target does not exist.

---

### get_privacy_version()

Get one version snapshot.

**HTTP API**

```
GET /api/v1/privacy-configs/{category}/{target_key}/versions/{version}
```

```bash
curl -X GET "http://localhost:1933/api/v1/privacy-configs/skill/byted-viking-search-knowledgebase/versions/2" \
  -H "X-API-Key: your-key" \
  -H "X-OpenViking-Account: default" \
  -H "X-OpenViking-User: alice"
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "version": 2,
    "category": "skill",
    "target_key": "byted-viking-search-knowledgebase",
    "values": {
      "api_key": "secret-1",
      "base_url": "https://example.com"
    }
  },
  "time": 0.01
}
```

> Returns `NOT_FOUND` if target/version does not exist.

---

### activate_privacy_version()

Switch active version.

**HTTP API**

```
POST /api/v1/privacy-configs/{category}/{target_key}/activate
```

**Request Body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| version | int | Yes | Version number to activate |

```bash
curl -X POST "http://localhost:1933/api/v1/privacy-configs/skill/byted-viking-search-knowledgebase/activate" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -H "X-OpenViking-Account: default" \
  -H "X-OpenViking-User: alice" \
  -d '{"version": 2}'
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "version": 2,
    "category": "skill",
    "target_key": "byted-viking-search-knowledgebase",
    "values": {
      "api_key": "secret-1",
      "base_url": "https://example.com"
    }
  },
  "time": 0.01
}
```

> Returns `NOT_FOUND` if target/version does not exist.

---

## CLI Quick Operations

```bash
# Categories and targets
openviking privacy categories
openviking privacy list skill

# Active config (shortcut supported)
openviking privacy get skill byted-viking-search-knowledgebase
openviking privacy skill byted-viking-search-knowledgebase

# Upsert with full JSON snapshot
openviking privacy upsert skill byted-viking-search-knowledgebase \
  --values-json '{"api_key":"secret-2","base_url":"https://example.com"}'

# Partial key update (CLI merges with current first)
openviking privacy upsert skill byted-viking-search-knowledgebase \
  --key-api_key secret-3

# Version query and activation
openviking privacy versions skill byted-viking-search-knowledgebase
openviking privacy version skill byted-viking-search-knowledgebase 2
openviking privacy activate skill byted-viking-search-knowledgebase 2
```

---

## Related Documentation

- [Skills](04-skills.md) - Skill write/read APIs
- [File System](03-filesystem.md) - `read`/`write`/`ls`
- [System](07-system.md) - System status and observability
