# ACL API

The ACL API manages direct grants on resource nodes and reports their inherited effective permissions. Every ACL is limited to the current account.

Read [Resource Access Control (ACL)](../concepts/15-acl.md) for the permission and inheritance model.

## Endpoint Summary

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/acl?uri={uri}` | Get direct, inherited, and effective ACLs |
| PUT | `/api/v1/acl` | Replace the node's direct ACL |
| DELETE | `/api/v1/acl?uri={uri}` | Clear the node's direct ACL |
| POST | `/api/v1/acl/grant` | Set one principal's direct level |
| POST | `/api/v1/acl/revoke` | Remove one principal's direct grant |

Every endpoint requires `manage` on the target node. Account `ADMIN`s implicitly manage public resources, and the user named in a user-resource URI implicitly manages that resource tree.

## Data Structures

### ACL entry

```json
{
  "principal": "user:bob",
  "level": "viewer"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `principal` | string | `user:{user_id}`, `group:{group_id}`, or `user:*` |
| `level` | string | `viewer`, `editor`, or `manager` |

The [Admin API](./08-admin.md#groups) generates immutable, non-reusable group IDs. After a group is deleted, an old group principal left in an ACL no longer matches any request.

### ACL report

```json
{
  "uri": "viking://resources/project-a",
  "acl_enabled": true,
  "direct_entries": [
    {"principal": "user:bob", "level": "viewer"}
  ],
  "inherited_entries": [
    {"principal": "group:grp_engineering", "level": "editor"}
  ],
  "effective_entries": [
    {"principal": "group:grp_engineering", "level": "editor"},
    {"principal": "user:bob", "level": "viewer"}
  ]
}
```

| Field | Description |
|-------|-------------|
| `direct_entries` | Entries set directly on this node |
| `inherited_entries` | Merged direct ACLs from all ancestors |
| `effective_entries` | The merged direct and inherited entries |
| `acl_enabled` | `true` when this node or an ancestor has a direct ACL; read-only and derived |

Implicit managers are not included in these lists.

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

## Replace a Direct ACL

```
PUT /api/v1/acl
```

Request body:

```json
{
  "uri": "viking://resources/project-a",
  "entries": [
    {"principal": "user:bob", "level": "viewer"},
    {"principal": "group:grp_engineering", "level": "editor"}
  ]
}
```

`entries` completely replaces this node's direct ACL without changing direct ACLs on ancestors or descendants. Duplicate principals keep their highest level. An empty list is equivalent to deleting this node's direct ACL.

```bash
curl -X PUT http://localhost:1933/api/v1/acl \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "uri": "viking://resources/project-a",
    "entries": [
      {"principal": "user:bob", "level": "viewer"},
      {"principal": "group:grp_engineering", "level": "editor"}
    ]
  }'
```

**Python SDK**

```python
report = client.acl_set(
    "viking://resources/project-a",
    [
        {"principal": "user:bob", "level": "viewer"},
        {"principal": "group:grp_engineering", "level": "editor"},
    ],
)
```

The asynchronous client uses the same method name:

```python
report = await client.acl_set(uri, entries)
```

**Go SDK**

```go
report, err := client.SetACL(ctx, "viking://resources/project-a", []openviking.ACLEntry{
    {Principal: "user:bob", Level: "viewer"},
    {Principal: "group:grp_engineering", Level: "editor"},
})
```

**CLI**

```bash
ov acl set viking://resources/project-a \
  --entry user:bob=viewer \
  --entry group:grp_engineering=editor
```

## Set One Principal's Level

```
POST /api/v1/acl/grant
```

```json
{
  "uri": "viking://resources/project-a",
  "principal": "user:bob",
  "level": "editor"
}
```

This sets Bob's direct level on the current node to `editor`. It updates an existing direct entry without changing other principals.

```bash
curl -X POST http://localhost:1933/api/v1/acl/grant \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "uri": "viking://resources/project-a",
    "principal": "user:bob",
    "level": "editor"
  }'
```

```python
report = client.acl_grant(
    "viking://resources/project-a",
    principal="user:bob",
    level="editor",
)
```

```bash
ov acl grant viking://resources/project-a --principal user:bob --level editor
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

This does not remove direct ACLs on descendants. The current node is recalculated from its ancestors, while each descendant continues to combine its own direct ACL with its ancestors.

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
| Caller lacks manage | `PERMISSION_DENIED` |
| Authorized caller targets a URI that does not exist | `NOT_FOUND` |
| ACL mutation targets a URI without a context record | `INVALID_ARGUMENT`; index it first |
| Invalid `principal` syntax or `group:*` | `INVALID_ARGUMENT` |
| Level is not `viewer/editor/manager` | `INVALID_ARGUMENT` |
| Request includes unknown fields such as `acl_enabled` | `INVALID_ARGUMENT` |

Direct and inherited ACL fields are both stored in context records. An update changes the target direct ACL and recalculates descendant inherited ACLs in one subtree batch; a failed write restores the previous context ACL fields.

## Related Documentation

- [Resource Access Control (ACL)](../concepts/15-acl.md) - Permissions, inheritance, and retrieval semantics
- [Authentication](../guides/04-authentication.md) - Request identity and account roles
- [Filesystem API](./03-filesystem.md) - ACL-controlled file operations
- [Retrieval API](./06-retrieval.md) - `find/search` endpoints
