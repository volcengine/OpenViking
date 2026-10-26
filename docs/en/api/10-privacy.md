# Privacy Configs

Privacy configs manage sensitive values in Skills and Sessions (such as `api_key` and `token`) by `category + target_key` in the current user's space. Changed values create a version snapshot. You can query history and switch the active version. Submitting the same values as the current version does not create a new version.

These examples use the current user’s API key. In trusted mode, supply the identity headers required by your deployment. Value-reading endpoints and CLI commands return stored values; tree and field-metadata endpoints omit them. `***` below is an example placeholder, not automatic redaction.

Activating a version changes only the values stored in OpenViking. It does not rotate, revoke, or restore credentials at the external provider.

## Endpoint Summary

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/privacy-configs` | List privacy config categories |
| GET | `/api/v1/privacy-configs/tree` | List current targets and field metadata as a tree without values |
| POST | `/api/v1/privacy-configs/scan` | Scan or repair registered values in stored content |
| GET | `/api/v1/privacy-configs/{category}` | List targets under a category |
| GET | `/api/v1/privacy-configs/{category}/{target_key}` | Get active config (`meta + current`) |
| GET | `/api/v1/privacy-configs/{category}/{target_key}/fields` | List safe active-field metadata without values |
| GET | `/api/v1/privacy-configs/{category}/{target_key}/field?field={field}` | Get a field reference; optionally reveal its value |
| POST | `/api/v1/privacy-configs/resolve` | Explicitly reveal a field by reference or URI |
| POST | `/api/v1/privacy-configs/{category}/{target_key}` | Upsert and activate a new/current version |
| PATCH | `/api/v1/privacy-configs/{category}/{target_key}` | Atomically merge fields; create a missing target |
| DELETE | `/api/v1/privacy-configs/{category}/{target_key}` | Delete a target and every version |
| DELETE | `/api/v1/privacy-configs/{category}/{target_key}/field?field={field}` | Remove a field from the active config |
| DELETE | `/api/v1/privacy-configs/selector` | Delete by target URI or field reference/URI |
| GET | `/api/v1/privacy-configs/{category}/{target_key}/versions` | List version numbers |
| GET | `/api/v1/privacy-configs/{category}/{target_key}/versions/{version}` | Get a specific version snapshot |
| POST | `/api/v1/privacy-configs/{category}/{target_key}/activate` | Activate a specific version |

---

## Common Use Cases

- Store sensitive settings for a Skill or Session
- Rotate keys by writing a new version
- Roll back to an old version
- Restore values from placeholders when reading Skill or Session content

---

## Scan Stored Content

Explicitly scan one persisted file or directory. The default is a dry run and never returns the matched value. With `fix=true`, unambiguous matches are replaced and affected content is refreshed asynchronously.

```text
POST /api/v1/privacy-configs/scan
```

```json
{
  "uri": "viking://~/resources/project-a",
  "fix": false,
  "node_limit": 1000
}
```

`uri` is required. Broad scope-based scans are intentionally unsupported. `node_limit` must be between 1 and 1000. The server also enforces one scan per process, 20 file reads per second, 1 MiB per file, 16 MiB total input, 1,000 replacements per file, at most 32 reference details per matched file, at most 100 repaired files, and a cooperative 60-second scan budget. A second concurrent request receives `RESOURCE_EXHAUSTED`. Repair never waits for semantic/vector refresh work. Responses report skipped files, truncation, the stop reason, and the effective limits. When one value maps to multiple Privacy fields, the result reports `ambiguous` and never chooses a reference arbitrarily.

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

### Input format and capacity limits

The server applies the same validation to HTTP, CLI, Embedded, and internal writes:

| Item | Limit |
|------|------|
| `category` / `target_key` | Non-empty, at most 255 ASCII characters; only letters, digits, `_`, `-`, `.`, and `@`; `.` and `..` are forbidden and at most one `@` is allowed |
| Session `target_key` | Use the Session ID when it meets the limits above; otherwise use `_` followed by the first 24 hexadecimal characters of its SHA-256 hash (the original Session ID is automatically recorded in `labels.session_id`) |
| Top-level values fields | At most 1024 for `category=session`; at most 128 for other categories |
| Field name | Non-empty, at most 255 UTF-8 bytes, no control characters |
| Individual string | At most 64 KiB UTF-8 |
| Serialized values | At most 1 MiB |
| JSON nesting | At most 16 levels; at most 4096 items in an individual object or array |
| labels | At most 64 entries and 64 KiB serialized |
| `change_reason` | At most 1024 characters |

Violations return `INVALID_ARGUMENT`. Error `details` identify the field, reason, actual size, and limit without including the sensitive value itself.

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
  -H "X-API-Key: your-key"
```

**Response**

```json
{
  "status": "ok",
  "result": ["session", "skill"]
}
```

Only categories with stored configs are returned; a Session with no extracted values does not create a privacy config.

---

### list_privacy_tree()

List the current user's categories, targets, active version numbers and field metadata
in one request. Historical versions and stored values are never returned. Targets
without a current snapshot are skipped.

**HTTP API**

```text
GET /api/v1/privacy-configs/tree
```

The optional `category` query parameter limits the tree to one category. An invalid
identifier returns `INVALID_ARGUMENT`. An empty store returns `categories: []`;
a missing filtered category returns that category with `targets: []`.

```bash
curl "http://localhost:1933/api/v1/privacy-configs/tree?category=credential" \
  -H "X-API-Key: your-key"
```

**Response**

```json
{
  "status": "ok",
  "result": {
    "owner_user_id": "alice",
    "categories": [{
      "category": "credential",
      "targets": [{
        "target_key": "github",
        "version": 3,
        "fields": [{
          "field": "pat",
          "configured": true,
          "reference": "{{ov_privacy:alice:viking://~/privacy/credential/github#/values/pat}}"
        }]
      }]
    }]
  }
}
```

`configured` is false for `null` or an empty string; other values, including `false`
and `0`, are configured. References are bound to the current owner and always
included in the API response. The CLI shows them in table output only when
`--references` is set; JSON output preserves all metadata.

---

### list_privacy_targets()

List target keys under a category.

**HTTP API**

```
GET /api/v1/privacy-configs/{category}
```

```bash
curl -X GET http://localhost:1933/api/v1/privacy-configs/skill \
  -H "X-API-Key: your-key"
```

**Response**

```json
{
  "status": "ok",
  "result": ["byted-viking-search-knowledgebase"]
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
  -H "X-API-Key: your-key"
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
  }
}
```

> Returns `NOT_FOUND` if the target does not exist.

---

### list_privacy_fields()

List active field names, configuration status, and owner-bound references without returning plaintext values. The CLI uses this endpoint when `reference` or the category form of `resolve` omits the field. It infers the field only when exactly one exists; multiple fields are listed to the user and require an explicit choice.

```text
GET /api/v1/privacy-configs/{category}/{target_key}/fields
```

```json
{
  "status": "ok",
  "result": {
    "owner_user_id": "alice",
    "category": "credential",
    "target_key": "github-prod",
    "version": 1,
    "fields": [
      {
        "field": "pat",
        "configured": true,
        "reference": "{{ov_privacy:alice:viking://~/privacy/credential/github-prod#/values/pat}}"
      }
    ]
  }
}
```

---

### get_privacy_field()

Get one field from the active version. By default the endpoint returns an owner-bound reference and configuration status without returning the value. Add `reveal=true` to explicitly read that one field for immediate use.

```text
GET /api/v1/privacy-configs/{category}/{target_key}/field?field={field}&reveal=false
```

```json
{
  "status": "ok",
  "result": {
    "owner_user_id": "alice",
    "category": "credential",
    "target_key": "github-prod",
    "field": "api_key",
    "version": 1,
    "configured": true,
    "reference": "{{ov_privacy:alice:viking://~/privacy/credential/github-prod#/values/api_key}}"
  }
}
```

With `reveal=true`, the response also contains that field's `value`. Sibling fields are never returned. A missing target or field returns `NOT_FOUND`.

An owner-bound reference or field URI can instead be sent to the selector endpoint:

```text
POST /api/v1/privacy-configs/resolve
```

```json
{"selector":"{{ov_privacy:alice:viking://~/privacy/credential/github-prod#/values/api_key}}"}
```

Field URIs may use `viking://~/privacy/...#/values/...` or an explicit `viking://user/{user_id}/privacy/...#/values/...`. An explicit owner that differs from the current identity returns `PERMISSION_DENIED`.

References generated, stored, and returned by OpenViking always use the whitespace-free canonical form. The parser accepts spaces or tabs immediately inside the outer braces, such as <code>&#123;&#123; ov_privacy:alice:... &#125;&#125;</code>, then normalizes the result to <code>&#123;&#123;ov_privacy:alice:...&#125;&#125;</code>. Whitespace is not accepted inside the owner, URI, or path tokens.

---

### upsert_privacy_config()

Write a new version and set it as active.

**Behavior**

- `values` replaces the complete value map for that version; omitted keys are absent from the new snapshot
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
  }
}
```

---

### patch_privacy_config()

Merge `values` into the current configuration while holding the server-side target lock. A missing target starts from an empty object and creates version 1. An existing target keeps untouched fields and creates a new version. Read, merge, and write happen under the same lock, so concurrent updates to distinct fields do not suffer from client-side read-modify-write loss.

```text
PATCH /api/v1/privacy-configs/{category}/{target_key}
```

```json
{
  "values": {"pat": "github_pat_..."},
  "change_reason": "configure GitHub PAT"
}
```

Canonical CLI `--field` and `--field-*` flags use this endpoint without reading current values in the CLI. `--key` and `--key-*` remain backward-compatible aliases:

```bash
ov privacy upsert credential github --field-pat 'github_pat_...'
```

---

### delete_privacy_config()

Delete a target's current value, metadata, and complete version history. Repeated deletion is safe: a missing target returns `deleted: false`.

```text
DELETE /api/v1/privacy-configs/{category}/{target_key}
```

```json
{
  "status": "ok",
  "result": {
    "category": "credential",
    "target_key": "github-prod",
    "deleted": true
  }
}
```

Field deletion affects only the active configuration and does not expose a version parameter. If fields remain, it publishes a new active snapshot without that field and keeps historical snapshots. Removing the final field deletes the complete target, including current values, metadata, and all history, and returns `target_deleted: true`.

```text
DELETE /api/v1/privacy-configs/{category}/{target_key}/field?field={field}
```

Target URIs, field URIs, and owner-bound references can also be deleted through:

```text
DELETE /api/v1/privacy-configs/selector
```

```json
{"selector":"viking://~/privacy/credential/github-prod#/values/api_key"}
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
  -H "X-API-Key: your-key"
```

**Response**

```json
{
  "status": "ok",
  "result": [1, 2, 3, 4]
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
  -H "X-API-Key: your-key"
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
  }
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
  }
}
```

> Returns `NOT_FOUND` if target/version does not exist.

---

## CLI Quick Operations

```bash
# Categories and targets
ov privacy categories
ov privacy list skill
ov privacy list session

# Tree of current targets and fields, without values
ov privacy tree
ov privacy tree --category credential --references
ov privacy tree --output json

# Active config (shortcut supported)
ov privacy get skill byted-viking-search-knowledgebase
ov privacy skill byted-viking-search-knowledgebase

# Get the owner-bound reference without returning the value.
# The field may be omitted only when the target has exactly one field.
ov privacy reference credential github-prod
ov privacy reference credential github-prod api_key

# Explicitly reveal one field in the active version (same inference rule)
ov privacy resolve credential github-prod
ov privacy resolve credential github-prod api_key
ov privacy resolve '{{ov_privacy:alice:viking://~/privacy/credential/github-prod#/values/api_key}}'
ov privacy resolve 'viking://~/privacy/credential/github-prod#/values/api_key'

# Upsert with full JSON snapshot
ov privacy upsert skill byted-viking-search-knowledgebase \
  --values-json '{"api_key":"secret-2","base_url":"https://example.com"}'

# Read values from a local JSON file instead of command arguments
ov privacy upsert skill byted-viking-search-knowledgebase --values-file ./privacy-values.json

# Update one field; creates a missing target, otherwise merges atomically on the server
ov privacy upsert skill byted-viking-search-knowledgebase \
  --field-api_key secret-3

# Version query and activation
ov privacy versions skill byted-viking-search-knowledgebase
ov privacy version skill byted-viking-search-knowledgebase 2
ov privacy activate skill byted-viking-search-knowledgebase 2

# Delete the target and every version; non-interactive use requires --yes
ov privacy delete credential github-prod --yes
ov privacy delete 'viking://~/privacy/credential/github-prod' --yes

# Remove the active field; historical snapshots remain
ov privacy delete credential github-prod api_key --yes
ov privacy delete '{{ov_privacy:alice:viking://~/privacy/credential/github-prod#/values/api_key}}' --yes

# Stored-content scans require one URI and are dry-run unless --fix is passed
ov privacy scan --uri viking://~/resources/project-a
ov privacy scan --uri viking://~/resources/project-a --fix --node-limit 500
```

---

## Related Documentation

- [Skills](04-skills.md) - Skill write/read APIs
- [Sessions](05-sessions.md) - Session creation and privacy config
- [Privacy Configs](../concepts/13-privacy.md) - Sensitive value extraction and restore
- [File System](03-filesystem.md) - `read`/`write`/`ls`
- [System](07-system.md) - System status and observability
