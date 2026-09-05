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
  "acl_enabled": true,
  "acl_restricted": false,
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
| `inherited_entries` | Permissions continuously refreshed from the parent, including while restricted |
| `effective_entries` | Direct plus inherited entries normally; direct entries only while restricted |
| `acl_restricted` | When `true`, this node does not use inherited permissions |
| `acl_enabled` | `true` when this node is ACL-controlled; read-only and derived |

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
  "restricted": true
}
```

`entries` and `restricted` are optional, but at least one must be provided. `entries` replaces the full direct ACL. With `restricted=true`, only direct entries are effective; `restricted=false` enables inherited entries again. Omitted fields remain unchanged. Inherited entries continue to refresh while restricted, so disabling the mode applies the latest inherited permissions immediately. Duplicate principals keep their highest level.

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
    "restricted": true
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
    restricted=True,
)
```

The asynchronous client uses the same method name:

```python
report = await client.acl_set(uri, entries, restricted=True)
```

**Go SDK**

```go
report, err := client.SetACL(ctx, "viking://resources/project-a", []openviking.ACLEntry{
    {Principal: "user:bob", Level: "read"},
    {Principal: "group:engineering", Level: "write"},
}, openviking.SetACLOptions{Restricted: openviking.Bool(true)})

// Change only the mode without changing the direct ACL.
report, err = client.SetACLRestricted(ctx, "viking://resources/project-a", true)
```

**CLI**

```bash
ov acl set viking://resources/project-a \
  --restricted true \
  --entry user:bob=read \
  --entry group:engineering=write

# Disable restricted mode only.
ov acl set viking://resources/project-a --restricted false
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

This clears the current node's direct ACL and sets `restricted` to `false`. It does not delete stored inherited entries or direct ACLs on descendants. The node immediately uses the latest inherited permissions.

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
| Request provides neither `entries` nor `restricted`, or includes unknown fields such as `acl_enabled` | `INVALID_ARGUMENT` |

Restricted, direct, and inherited ACL fields are stored in context records. An update changes the target fields and recalculates descendant inherited ACLs in one subtree batch; a failed write restores the previous context ACL fields.

## Related Documentation

- [Resource Access Control (ACL)](../concepts/15-acl.md) - Permissions, inheritance, and retrieval semantics
- [Authentication](../guides/04-authentication.md) - Request identity and account roles
- [Filesystem API](./03-filesystem.md) - ACL-controlled file operations
- [Retrieval API](./06-retrieval.md) - `find/search` endpoints
