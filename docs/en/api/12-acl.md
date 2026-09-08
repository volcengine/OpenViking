# ACL API

The ACL API manages direct grants and restricted mode on shared `viking://resources/...` nodes and reports their inherited effective permissions. Private resources do not accept ACLs and must be moved into the shared scope to be shared.

Read [Resource Access Control (ACL)](../concepts/15-acl.md) for the permission and inheritance model.

## Endpoint Summary

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/acl?uri={uri}` | Get direct, inherited, and effective ACLs |
| PUT | `/api/v1/acl` | Update the node's direct ACL or restricted mode |
| DELETE | `/api/v1/acl?uri={uri}` | Clear the direct ACL and restricted mode |
| POST | `/api/v1/acl/grant` | Set one principal's direct level |
| POST | `/api/v1/acl/revoke` | Remove one principal's direct grant |

Every endpoint requires `manage` on the target node. Account `ADMIN`s implicitly manage shared resources.

`viking://resources` is a fixed shared scope and cannot carry a direct ACL. The
account setting `acl.enabled` defaults to `false`. While disabled, shared
resources use the original public behavior and ACL authorization is skipped.
When enabled, newly created shared files, directories, and `add-resource` roots
grant the creator direct `manage` and inherit the parent ACL. Existing content
without an ACL remains public. Descendants within an `add-resource` import only
inherit the root grant.

## Data Structures

### ACL entry

```json
{
  "principal": "user:bob",
  "level": "read"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `principal` | string | `user:{user_id}`, `group:{group_id}`, or `user:*` |
| `level` | string | `read`, `write`, or `manage` |

The caller supplies the account-unique, stable `group_id` through the [Admin API](./08-admin.md#groups). A group has no separate display name. After a group is deleted, its old principal no longer matches any request unless the same `group_id` is created again.

### ACL report

```json
{
  "uri": "viking://resources/project-a",
  "acl_mode": "inherit",
  "direct_entries": [
    {"principal": "user:bob", "level": "read"}
  ],
  "inherited_entries": [
    {"principal": "group:engineering", "level": "write"}
  ],
  "effective_entries": [
    {"principal": "group:engineering", "level": "write"},
    {"principal": "user:bob", "level": "read"}
  ]
}
```

| Field | Description |
|-------|-------------|
| `direct_entries` | Entries set directly on this node |
| `inherited_entries` | The parent's current effective permissions, refreshed even while restricted |
| `effective_entries` | Direct plus inherited grants in inherit mode; direct grants only in restricted mode |
| `acl_mode` | `none`: not ACL-controlled; `inherit`: direct and inherited grants apply; `restricted`: only direct grants apply |

The account `ADMIN` implicit `manage` permission is not included in these lists.

## Get an ACL

```
GET /api/v1/acl?uri={uri}
```

GET can report an existing target that has no context record: `direct_entries` is empty and inherited permissions are resolved from existing ancestor contexts. Mutating ACL endpoints require a context record for the target.

```bash
curl "http://localhost:1933/api/v1/acl?uri=viking%3A%2F%2Fresources%2Fproject-a" \
  -H "X-API-Key: your-key"
```

**Python SDK**

```python
report = client.acl_get("viking://resources/project-a")
```

**Go SDK**

```go
report, err := client.ACL(ctx, "viking://resources/project-a")
```

## Update a Direct ACL or Restricted Mode

```
PUT /api/v1/acl
```

Request body:

```json
{
  "uri": "viking://resources/project-a",
  "entries": [
    {"principal": "user:bob", "level": "read"},
    {"principal": "group:engineering", "level": "write"}
  ],
  "acl_mode": "restricted"
}
```

Provide `entries`, `acl_mode`, or both. `entries` replaces the full direct ACL. `acl_mode` accepts `restricted` (direct grants only) or `inherit` (resume inheritance). Omitted fields remain unchanged. Inherited grants continue to refresh while restricted and apply immediately when inheritance resumes. Duplicate principals keep their highest level.

Setting `none` directly is not allowed, as it would bypass the parent's ACL. After resuming inheritance or deleting the ACL, the system returns `none` if the node has no direct grants and its parent is not ACL-controlled.

```bash
curl -X PUT http://localhost:1933/api/v1/acl \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "uri": "viking://resources/project-a",
    "entries": [
      {"principal": "user:bob", "level": "read"},
      {"principal": "group:engineering", "level": "write"}
    ],
    "acl_mode": "restricted"
  }'
```

**Python SDK**

```python
report = client.acl_set(
    "viking://resources/project-a",
    [
        {"principal": "user:bob", "level": "read"},
        {"principal": "group:engineering", "level": "write"},
    ],
    acl_mode="restricted",
)
```

The asynchronous client uses the same method name:

```python
report = await client.acl_set(uri, entries, acl_mode="restricted")
```

**Go SDK**

```go
report, err := client.SetACL(ctx, "viking://resources/project-a", []openviking.ACLEntry{
    {Principal: "user:bob", Level: "read"},
    {Principal: "group:engineering", Level: "write"},
}, openviking.SetACLOptions{ACLMode: "restricted"})

// Change only the mode without changing the direct ACL.
report, err = client.SetACLMode(ctx, "viking://resources/project-a", "restricted")
```

**CLI**

```bash
ov acl set viking://resources/project-a \
  --acl-mode restricted \
  --entry user:bob=read \
  --entry group:engineering=write

# Disable restricted mode only.
ov acl set viking://resources/project-a --acl-mode inherit
```

## Set One Principal's Level

```
POST /api/v1/acl/grant
```

```json
{
  "uri": "viking://resources/project-a",
  "principal": "user:bob",
  "level": "write"
}
```

This sets Bob's direct level on the current node to `write`. It updates an existing direct entry without changing other principals.

```bash
curl -X POST http://localhost:1933/api/v1/acl/grant \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "uri": "viking://resources/project-a",
    "principal": "user:bob",
    "level": "write"
  }'
```

```python
report = client.acl_grant(
    "viking://resources/project-a",
    principal="user:bob",
    level="write",
)
```

```bash
ov acl grant viking://resources/project-a --principal user:bob --level write
```

## Remove One Direct Grant

```
POST /api/v1/acl/revoke
```

```json
{
  "uri": "viking://resources/project-a",
  "principal": "user:bob"
}
```

`revoke` removes only Bob's direct entry on the current node. Any permission inherited by Bob from an ancestor remains effective.

```python
report = client.acl_revoke("viking://resources/project-a", principal="user:bob")
```

```bash
ov acl revoke viking://resources/project-a --principal user:bob
```

## Clear the Node's Direct ACL

```
DELETE /api/v1/acl?uri={uri}
```

This clears the node's direct ACL and exits restricted mode without deleting stored inherited entries or descendant direct ACLs. The latest inherited permissions apply immediately; if the parent is not ACL-controlled either, `acl_mode` returns to `none`.

```bash
curl -X DELETE \
  "http://localhost:1933/api/v1/acl?uri=viking%3A%2F%2Fresources%2Fproject-a" \
  -H "X-API-Key: your-key"
```

```python
report = client.acl_delete("viking://resources/project-a")
```

```bash
ov acl rm viking://resources/project-a
```

## Errors

The API checks manage permission before confirming existence to an authorized caller, preventing resource discovery through error types.

| Scenario | Error |
|----------|-------|
| URI is outside `viking://resources/...` | `INVALID_ARGUMENT` |
| Caller lacks manage | `PERMISSION_DENIED` |
| Authorized caller targets a URI that does not exist | `NOT_FOUND` |
| ACL mutation targets a URI without a context record | `INVALID_ARGUMENT`; index it first |
| Invalid `principal` syntax or `group:*` | `INVALID_ARGUMENT` |
| Level is not `read/write/manage` | `INVALID_ARGUMENT` |
| `acl_mode` is not `inherit/restricted`, or the request includes read-only inherited fields | `INVALID_ARGUMENT` |

ACL mode, direct grants, and inherited grants are stored in context records. An update changes the target fields and recalculates descendant inherited ACLs in one subtree batch; a failed write restores the previous context ACL fields.

## Related Documentation

- [Resource Access Control (ACL)](../concepts/15-acl.md) - Permissions, inheritance, and retrieval semantics
- [Authentication](../guides/04-authentication.md) - Request identity and account roles
- [Filesystem API](./03-filesystem.md) - ACL-controlled file operations
- [Retrieval API](./06-retrieval.md) - `find/search` endpoints
