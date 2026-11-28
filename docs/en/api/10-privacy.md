# Privacy Configs

Privacy configs manage sensitive values in Skills and Sessions (such as `api_key` and `token`) by `category + target_key` in the current user's space. Changed values create a version snapshot. You can query history and switch the active version. Submitting the same values as the current version does not create a new version.

> You can manage Privacy configs and their versions even when automatic processing is disabled in [User settings](08-admin.md#user-settings).

These examples use the current user’s API key. In trusted mode, supply the identity headers required by your deployment. Value-reading endpoints and CLI commands return stored values; tree and field-metadata endpoints omit them. `***` below is an example placeholder, not automatic redaction.

Activating a version changes only the values stored in OpenViking. It does not rotate, revoke, or restore credentials at the external provider.

Mutations of the same `category + target_key` for the same user require the target's write lock. If another operation holds the lock, the endpoint returns HTTP 409 (`CONFLICT`) without waiting for release or automatically retrying the mutation.

## Endpoint Summary

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/privacy-configs` | List privacy config categories |
| GET | `/api/v1/privacy-configs/tree` | List current targets and field metadata as a tree without values |
| POST | `/api/v1/privacy-configs/scan` | Scan or repair registered values in stored content |
| POST | `/api/v1/privacy-configs/seal` | Replace registered secrets in caller-supplied texts; optionally enroll new ones |
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
- Keep secrets out of model requests (see [Seal Texts](#seal-texts))

---

## Scan Stored Content

Explicitly scan one persisted file or directory. The default is a dry run and never returns the matched value. With `fix=true`, matches are replaced and affected content is refreshed asynchronously.

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
By default, a fixed order selects a restorable reference. Set `OPENVIKING_PRIVACY_AMBIGUOUS_STRICT=1` to reject such matching operations.

---

## Seal Texts

Replace the caller's registered values (in the `credential` and `auto` categories) in caller-supplied texts with their canonical references, and optionally store new tokens it recognizes, for example before the texts are sent to a model provider. The result does not depend on the user's privacy `mode`; it also works when `mode` is `off`. Intended for clients such as the gateway and agent plugins. For the gateway, see [Seal secrets](../gateway/14-seal-secrets.md).

```text
POST /api/v1/privacy-configs/seal
```

```json
{
  "texts": [
    {"text": "My new GitHub token is ghp_..., use it from now on", "enroll": true},
    {"text": "(tool output)"}
  ]
}
```

```json
{
  "status": "ok",
  "result": {
    "revision": "4f0c...-9a1b...",
    "texts": [
      {
        "text": "My new GitHub token is {{ov_privacy:alice:viking://~/privacy/auto/github-k3j2m8#/values/token}}, use it from now on",
        "replaced": [
          {"start": 23, "end": 63, "reference": "{{ov_privacy:alice:viking://~/privacy/auto/github-k3j2m8#/values/token}}", "created": true, "temporary": true}
        ]
      },
      null
    ]
  }
}
```

**Request**

| Field | Type | Required | Meaning |
|-------|------|----------|---------|
| `texts` | array | Yes | 1 to 1024 items whose UTF-8 sizes sum to at most 16 MiB |
| `texts[].text` | string | Yes | The text to seal |
| `texts[].enroll` | bool | No (default `false`) | Also store unregistered tokens it recognizes; see **Enrollment** below |

**Response**

| Field | Meaning |
|-------|---------|
| `result.revision` | Opaque string that changes after every write to `credential` or `auto` and every change to `never_mask` or `custom_rules`. It already includes what the call enrolled. Compare it for equality only, for example as part of a cache key |
| `result.texts[i]` | `null` when nothing in that text was replaced; keep the original text |
| `result.texts[i].text` | The text with references in place of the values |
| `result.texts[i].replaced[].start`, `end` | Unicode code point offsets into the original text; `end` is exclusive |
| `result.texts[i].replaced[].reference` | The canonical reference that replaced the span |
| `result.texts[i].replaced[].temporary` | True exactly when the reference is in the `auto` category, that is, the value has not been saved under a proper name yet. It depends only on the vault at the returned `revision`, so callers may cache it under that revision |
| `result.texts[i].replaced[].created` | True for every occurrence, in any text of the request, of a value enrolled by this call. A statistic about this one call; do not cache it or base decisions on it |

**What is matched**

- Matching follows the storage-path rules: string values of at least 6 characters, `never_mask` excluded, existing <code v-pre>{{ov_privacy:...}}</code> spans skipped, and the same ambiguity handling.
- Registered values are those of the `credential` category and of the `auto` category, which holds automatically enrolled values (see **Enrollment**). The storage-path sanitizer does not look at `auto`.
- Unlike the storage paths, seal also matches the values of earlier stored versions of each target, so a rotated token that is still in a conversation's history keeps being replaced. An earlier value gets the reference of its field, the same one the current value gets. When a value is held by several fields, a `credential` field wins over an `auto` one, and within a category a current value wins over an earlier one. Deleting a target removes its history, and its values are no longer sealed.

**Enrollment**

- With `enroll: true`, tokens recognized by the built-in detectors and the user's `custom_rules` (minus `never_mask`) that are not registered yet are stored as one new target each in the `auto` category, named `<rule>-<random suffix>` with field `token` and label `{"ov.rule": "<rule>"}`. Their reference is <code v-pre>{{ov_privacy:&lt;owner&gt;:viking://~/privacy/auto/&lt;target&gt;#/values/token}}</code>.
- The same value always yields the same reference, also across workers. Every occurrence of a value enrolled by this call, in any text of the request, reports `created: true`.
- At most 1024 new values can be enrolled per call; a request with more returns `RESOURCE_EXHAUSTED` and enrolls none of them. Because enrollment is idempotent, a caller that timed out can simply retry.

To give an enrolled entry a proper name, write its reference as a value (see [upsert_privacy_config()](#upsert-privacy-config)); the `auto` entry is then removed. Entries in `auto` otherwise stay and accumulate; they can be listed, resolved and deleted like any other target. Only the seal endpoint writes into `auto`: upsert and patch with `category=auto` are rejected with `INVALID_ARGUMENT`.

**Errors**

| Condition | Error |
|-----------|-------|
| `texts` has fewer than 1 or more than 1024 items | `INVALID_ARGUMENT` |
| The texts' UTF-8 sizes sum to more than 16 MiB | `RESOURCE_EXHAUSTED` |
| More than 1024 new values to enroll | `RESOURCE_EXHAUSTED`; none of them is enrolled |
| More than 10,000 replacements in total | `RESOURCE_EXHAUSTED` |

- An error never comes back as unchanged text.
- Callers should treat any non-`ok` outcome, including HTTP 404 from an older server, as a failure and not forward the original text.

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

Only categories with stored configs are returned, including `auto` once something was enrolled; a Session with no extracted values does not create a privacy config.

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
The HTTP route and `ov privacy tree` list every category. The MCP `privacy_tree` tool
leaves the `auto` category out unless it is called with `category="auto"`.

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
- A top-level string value that is, as a whole, a reference to a field in the caller's own `credential` or `auto` category stores that field's current value instead of the reference text. Writing to a new target renames the credential; writing to an existing target rotates it to a new version. A source in `auto` is deleted after the write succeeds (if that cleanup fails, the write still stands); a source in `credential` is kept. The destination may not be in `auto`
- When at least one value in the request was such a reference, the response's `values` holds each field's own canonical reference in place of every stored value, so the copied secret is never echoed (also by `ov privacy upsert`, in table and JSON output). Requests with only plain values get the stored values back as before
- Any other top-level string value that contains a reference is rejected with `INVALID_ARGUMENT`: references to another category or user, to a missing target or field, and references inside a longer string. References nested inside object or array values are not inspected and are stored as written. This also applies to PATCH, the MCP `privacy_upsert` tool, and the CLI
- A target can carry a short description in the label `description`. `list_privacy_tree()` returns it on each target that has one; other labels are not returned there. The MCP `privacy_upsert` tool takes an optional `description` (at most 500 characters) that sets this label and keeps the other labels; without it, labels stay as they are. From the CLI, pass `--labels-json '{"description":"..."}'`

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

A whole-value reference to a `credential` or `auto` field is copied the same way as for upsert, so a model that only holds a reference can rename or rotate a credential without seeing it. As with upsert, the response then shows every value as its field's reference. For example, this body saves an automatically enrolled token as `pat`:

```json
{"values": {"pat": "{{ov_privacy:alice:viking://~/privacy/auto/github-k3j2m8#/values/token}}"}}
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

### Run external commands with Privacy references

`ov privacy run` lets callers or Agents run local external commands (such as `gh` or `curl`) directly with Privacy references (<code v-pre>{{ov_privacy:...}}</code>, obtained via `ov privacy reference` or `ov privacy tree --references`). Before launching the external command, `ov privacy run` resolves references in environment variables or arguments and injects the sensitive values; in the command's `stdout`/`stderr` output, matching sensitive values are replaced back with their corresponding Privacy references in real time.

> Output replacement applies only to the external command's `stdout` and `stderr`, not to files it writes or data it sends over the network.

```bash
# 1. Pass via environment variable (recommended to keep sensitive values out of argv)
ov privacy run --env GITHUB_TOKEN='{{ov_privacy:alice:viking://~/privacy/credential/github-prod#/values/api_key}}' -- gh api user

# 2. Load from a file (one NAME={{ov_privacy:...}} entry per line; supports matching quotes and # comments)
ov privacy run --env-file ./privacy.env -- gh api user

# 3. Embed directly inside an argument of the external command
ov privacy run -- curl -H 'Authorization: Bearer {{ov_privacy:alice:viking://~/privacy/credential/github-prod#/values/api_key}}' https://api.example.com

# 4. Invoke sh -c explicitly for shell pipelines or redirections, passing references via --env
ov privacy run --env GITHUB_TOKEN='{{ov_privacy:alice:viking://~/privacy/credential/github-prod#/values/api_key}}' -- \
  sh -c 'curl -sS -H "Authorization: Bearer $GITHUB_TOKEN" https://api.example.com | jq .login'
```

> In examples 3 and 4 above, the sensitive value still ends up in `curl`'s process arguments (via `ov privacy run` expanding the argument reference in example 3, or `sh` expanding `$GITHUB_TOKEN` in example 4), which may be visible to other local processes, other containers, or the host. Prefer `--env` / `--env-file` so the target program reads environment variables directly (as in examples 1 and 2), and set `OPENVIKING_PRIVACY_RUN_STRICT=1` to reject references in `ov privacy run` command arguments.

---

## Related Documentation

- [Skills](04-skills.md) - Skill write/read APIs
- [Sessions](05-sessions.md) - Session creation and privacy config
- [Privacy Configs](../concepts/13-privacy.md) - Sensitive value extraction and restore
- [File System](03-filesystem.md) - `read`/`write`/`ls`
- [System](07-system.md) - System status and observability
- [Secret sealing in the gateway](../gateway/14-seal-secrets.md) - Seal secrets in model requests
