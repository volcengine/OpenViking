# ACL attributes

ACL is managed through `attrs` for shared resources under `viking://resources/...`. See [resource access control](../concepts/15-acl.md) for inheritance rules. The old `/api/v1/acl`, `ov acl`, and standalone SDK ACL methods have been removed without compatibility aliases.

## Endpoints

| Method | Path | Behavior |
|---|---|---|
| GET | `/api/v1/fs/attrs?uri={uri}&key=acl` | Read direct, inherited and effective permissions |
| POST | `/api/v1/fs/attrs/set_acl` | Set direct entries and/or inheritance mode |
| POST | `/api/v1/fs/attrs/grant_acl` | Set one principal's direct level |
| POST | `/api/v1/fs/attrs/revoke_acl` | Remove one principal's direct grant |
| POST | `/api/v1/fs/attrs/reset_acl` | Clear direct entries and restore inherit mode |

All ACL operations require `manage`; account ADMIN has implicit manage. GET without `key` returns only visible attributes and omits ACL for non-managers. An explicit `key=acl` request returns 403 without manage.

GET returns `result = {uri, context_type, attrs: {acl: report}}`. The report contains `uri`, `acl_mode`, `direct_entries`, `inherited_entries`, and `effective_entries`. Mutation endpoints return the report directly.

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
- `grant_acl` takes `{uri, principal, level}`, `revoke_acl` takes `{uri, principal}`, and `reset_acl` takes `{uri}`. Incremental changes run inside the server's existing lock.

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
ov attrs get viking://resources/project-a acl
ov attrs set-acl viking://resources/project-a --acl-mode restricted --entry user:bob=read
ov attrs grant-acl viking://resources/project-a --principal user:bob --level write
ov attrs revoke-acl viking://resources/project-a --principal user:bob
ov attrs reset-acl viking://resources/project-a

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
report = client.attrs(uri, key="acl")["attrs"]["acl"]
client.attrs_set_acl(uri, [], acl_mode="restricted")
client.attrs_grant_acl(uri, "user:bob", "read")
client.attrs_revoke_acl(uri, "user:bob")
client.attrs_reset_acl(uri)
```

Async Python uses the same method names. TypeScript provides `attrs(uri, "acl")`, `attrsSetAcl`, `attrsGrantAcl`, `attrsRevokeAcl`, and `attrsResetAcl`; creation options accept `acl`, while mkdir accepts it as its third argument. Go provides `Attrs(ctx, uri, "acl")`, `AttrsSetACL`, `AttrsGrantACL`, `AttrsRevokeACL`, and `AttrsResetACL`, with `ACLSpec` values for creation.
