# ACL API

ACL is queried and managed through the dedicated `/api/v1/acl` endpoints for shared resources under `viking://resources/...`. See [resource access control](../concepts/15-acl.md) for inheritance rules.

## Endpoints

| Method | Path | Behavior |
|---|---|---|
| GET | `/api/v1/acl?uri={uri}` | Read direct, inherited and effective permissions |
| PUT | `/api/v1/acl` | Set direct entries and/or inheritance mode |
| POST | `/api/v1/acl/grant` | Set one principal's direct level |
| POST | `/api/v1/acl/revoke` | Remove one principal's direct grant |
| DELETE | `/api/v1/acl?uri={uri}` | Clear direct entries and restore inherit mode |

**HTTP**

```http
GET /api/v1/acl?uri={uri}
PUT /api/v1/acl
DELETE /api/v1/acl?uri={uri}
POST /api/v1/acl/grant
POST /api/v1/acl/revoke
```

All ACL operations require `manage` and return 403 without it; account ADMIN has implicit manage.

GET returns the ACL report directly in `result`. The report contains `uri`, `acl_mode`, `direct_entries`, `inherited_entries`, and `effective_entries`. Mutation endpoints return the report directly.

Example `set_acl` body:

```json
{
  "uri": "viking://resources/project-a",
  "acl_mode": "restricted",
  "entries": [{"principal": "user:bob", "level": "read"}]
}
```

- `entries` replaces direct grants; omission preserves them; `[]` clears them.
- `acl_mode`: `inherit` combines direct and inherited grants; `restricted` uses direct grants only. Omission preserves the mode.
- Supply at least one field. Inherited and effective entries are read-only.
- Principals are `user:{id}`, `group:{id}`, or `user:*`; levels are `read`, `write`, or `manage`. Duplicate principals retain the highest level.
- `grant_acl` takes `{uri, principal, level}`, `revoke_acl` takes `{uri, principal}`, and resetting uses `DELETE /api/v1/acl?uri={uri}`. Incremental changes run inside the server's existing lock.

## Creation and content writes

`POST /api/v1/resources`, `POST /api/v1/fs/mkdir`, and `POST /api/v1/content/write` accept a top-level `acl` field, alongside `tags` and `tag_mode` where supported:

```json
{
  "acl": {
    "acl_mode": "restricted",
    "entries": [{"principal": "user:bob", "level": "read"}]
  }
}
```

Omitting ACL gives new nodes empty direct grants and inherited parent permissions; existing nodes retain their ACL. Explicit ACL requires inherited manage for a new node, or the existing node's own manage. Authorization and validation precede content mutation. Write access alone cannot change ACL. Creators receive no extra grants.

Imports apply direct ACL only to the final import root. Descendants inherit it; automatically created parent directories receive no direct grants. Re-importing unchanged content still applies an explicit ACL update.

The account's `acl.enabled` defaults to false: namespace rules apply and ACL is ignored for access decisions. When enabled, the shared root permanently grants `user:* = manage`. Supplying ACL neither enables the account switch nor implicitly selects restricted mode.

ACL stays in the context index and follows existing asynchronous processing and wait semantics. Temporary inconsistency is accepted. Standalone ACL mutations require an existing context record. Vectorless records and empty-file support are outside this change.

**CLI**

```bash
ov acl get viking://resources/project-a
ov acl set viking://resources/project-a --acl-mode restricted --entry user:bob=read
ov acl grant viking://resources/project-a --principal user:bob --level write
ov acl revoke viking://resources/project-a --principal user:bob
ov acl rm viking://resources/project-a

ov mkdir viking://resources/project-a --acl '{"acl_mode":"restricted","entries":[{"principal":"user:bob","level":"read"}]}'
ov add-resource ./docs --to viking://resources/docs --acl '{"acl_mode":"restricted","entries":[]}'
ov write viking://resources/project-a/a.md --content hello --mode create --acl '{"acl_mode":"inherit"}'
```

## SDK

```python
acl = {"acl_mode": "restricted", "entries": [{"principal": "user:bob", "level": "read"}]}
client.mkdir(uri, acl=acl)
client.add_resource("./docs", to=uri, options={"acl": acl})
client.write(file_uri, "hello", options={"acl": acl})
report = client.acl_get(uri)
client.acl_set(uri, [], acl_mode="restricted")
client.acl_grant(uri, "user:bob", "read")
client.acl_revoke(uri, "user:bob")
client.acl_delete(uri)
```

Async Python uses the same method names. TypeScript provides `aclGet(uri)`, `aclSet`, `aclGrant`, `aclRevoke`, and `aclDelete`; creation options accept `acl`, while mkdir accepts it as its third argument. Go provides `ACL(ctx, uri)`, `SetACL`, `SetACLMode`, `GrantACL`, `RevokeACL`, and `DeleteACL`, with `ACLSpec` values for creation.
