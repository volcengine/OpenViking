# ACL API

The ACL API manages direct grants on resource nodes and reports their inherited effective permissions. Every ACL is limited to the current account.

Read [Resource Access Control (ACL)](../concepts/15-acl.md) for the permission and inheritance model.

## Endpoint Summary

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/v1/acl?uri={uri}` | Get direct, inherited, and effective ACLs |
| PUT | `/api/v1/acl` | Replace the node's direct ACL |
| DELETE | `/api/v1/acl?uri={uri}` | Clear the node's direct ACL |
| POST | `/api/v1/acl/grant` | Set one user's direct level |
| POST | `/api/v1/acl/revoke` | Remove one user's direct grant |

Every endpoint requires `manage` on the target node. Account `ADMIN`s implicitly manage public resources, and the user named in a user-resource URI implicitly manages that resource tree.

## Data Structures

### ACL entry

```json
{
  "user_id": "bob",
  "level": "viewer"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `user_id` | string | A user ID in the current account; `*` means any account user |
| `level` | string | `viewer`, `editor`, or `manager` |

### ACL report

```json
{
  "uri": "viking://resources/project-a",
  "acl_enabled": true,
  "direct_entries": [
    {"user_id": "bob", "level": "viewer"}
  ],
  "inherited_entries": [
    {"user_id": "alice", "level": "editor"}
  ],
  "effective_entries": [
    {"user_id": "alice", "level": "editor"},
    {"user_id": "bob", "level": "viewer"}
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
    {"user_id": "bob", "level": "viewer"},
    {"user_id": "ci-bot", "level": "editor"}
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
      {"user_id": "bob", "level": "viewer"},
      {"user_id": "ci-bot", "level": "editor"}
    ]
  }'
```

**Python SDK**

```python
report = client.acl_set(
    "viking://resources/project-a",
    [
        {"user_id": "bob", "level": "viewer"},
        {"user_id": "ci-bot", "level": "editor"},
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
    {UserID: "bob", Level: "viewer"},
    {UserID: "ci-bot", Level: "editor"},
})
```

**CLI**

```bash
ov acl set viking://resources/project-a \
  --entry bob=viewer \
  --entry ci-bot=editor
```

## Set One User's Level

```
POST /api/v1/acl/grant
```

```json
{
  "uri": "viking://resources/project-a",
  "user_id": "bob",
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
    "user_id": "bob",
    "level": "editor"
  }'
```

```python
report = client.acl_grant(
    "viking://resources/project-a",
    user_id="bob",
    level="editor",
)
```

```bash
ov acl grant viking://resources/project-a --user-id bob --level editor
```

## Remove One Direct Grant

```
POST /api/v1/acl/revoke
```

```json
{
  "uri": "viking://resources/project-a",
  "user_id": "bob"
}
```

`revoke` removes only Bob's direct entry on the current node. Any permission inherited by Bob from an ancestor remains effective.

```python
report = client.acl_revoke("viking://resources/project-a", user_id="bob")
```

```bash
ov acl revoke viking://resources/project-a --user-id bob
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
| Invalid `user_id` | `INVALID_ARGUMENT` |
| Level is not `viewer/editor/manager` | `INVALID_ARGUMENT` |
| Request includes unknown fields such as `acl_enabled` | `INVALID_ARGUMENT` |

Direct and inherited ACL fields are both stored in context records. An update changes the target direct ACL and recalculates descendant inherited ACLs in one subtree batch; a failed write restores the previous context ACL fields.

## Related Documentation

- [Resource Access Control (ACL)](../concepts/15-acl.md) - Permissions, inheritance, and retrieval semantics
- [Authentication](../guides/04-authentication.md) - Request identity and account roles
- [Filesystem API](./03-filesystem.md) - ACL-controlled file operations
- [Retrieval API](./06-retrieval.md) - `find/search` endpoints
