# Resource Access Control (ACL)

OpenViking ACL shares directories or files from the shared resource scope with users or groups inside one account. ACL never changes the account boundary: every grant is limited to the current account.

ACL uses a collaborative-document inheritance model. A directory grant continuously applies to its descendants, while child directories and files can add direct grants. A child ACL does not replace grants inherited from ancestors.

## Supported URIs

ACL applies only to shared resources:

```text
viking://resources/...
```

- An account `ADMIN` is an implicit manager of `viking://resources/...`.
- `viking://user/{user_id}/resources/...` is private and does not accept ACLs. To share it, move the resource into a writable shared directory and inherit that directory's ACL.

Implicit management is not stored as an ACL entry and cannot be removed by ACL changes. It ensures that shared resources always have an identity that can establish or recover permissions.

## Principals and Levels

ACL entries use typed principals:

- `user:{user_id}`: a user in the current account.
- `group:{group_id}`: a server-generated group ID in the current account.
- `user:*`: any user in the current account.

`group:*` is not supported. Groups are flat. Membership changes do not rewrite resource ACL or context records; they take effect when the next request builds `RequestContext.group_ids`.
Asynchronous parse and semantic tasks created by a request carry the same group identity, so one authorized operation uses consistent permissions across its foreground and background stages.

| Level | Allowed operations |
|-------|--------------------|
| `viewer` | Read, list, and `find/search/grep` |
| `editor` | `viewer` capabilities plus write, create, and tag updates |
| `manager` | `editor` capabilities plus delete, move, and ACL management |

Each higher level includes the lower levels. A `manager` therefore has read, write, and manage capabilities.

## Inheritance

A node's effective ACL is the union of every ancestor's direct ACL and the node's own direct ACL:

```text
effective(node) = UNION(direct_acl(each ancestor), direct_acl(node))
```

For example:

```text
viewer user:bob   on viking://resources/A
editor group:grp_engineering on viking://resources/A/B
viewer user:carol on viking://resources/A/B/C/report.md
```

The effective permissions on `report.md` are:

- Bob: `viewer`
- Members of `grp_engineering`: `editor`
- Carol: `viewer`

Removing the group's direct ACL from `A/B` does not remove entries from `A` or `report.md`. Descendants only lose the permissions contributed by that entry.

## Default Behavior and `acl_enabled`

If neither a node nor any ancestor has a direct ACL, OpenViking keeps the existing URI namespace visibility and write behavior.

When the node or any ancestor has a direct ACL, the node enters the ACL-controlled domain:

```text
acl_enabled = true
```

`acl_enabled` is derived by the system and cannot be set by an API caller. It returns to `false` automatically after the last applicable direct ACL is removed.

## File Operations

All filesystem APIs use the same permission mapping:

| Operation | Required capability |
|-----------|---------------------|
| read, stat, list, tree, find, search, grep, glob, relations | read |
| write, create, mkdir, set tags | write |
| delete, manage ACL | manage |
| move source | manage |
| move destination parent | write |

An ACL grant on a directory is inherited by every descendant. `list`, `tree`, and other batch results still check every returned node because an ACL-free directory may be visible under legacy URI rules while one of its descendants has entered the ACL-controlled domain through its own ACL.

Within the shared scope, a moved node keeps its direct ACL and recalculates inherited permissions from its new ancestors. A private resource moved into the shared scope carries no ACL and inherits the destination directory; a shared resource moved back to a private area has its ACL cleared.

Recursive tag updates, directory deletion, and directory moves validate the complete affected subtree first. The operation stops if any node lacks the required capability or the subtree cannot be scanned completely.

For a directory, `stat.count` uses the same path and ACL scalar filter and reports the number of context records visible to the caller.

## Retrieval Filtering

ACL data exists only in the context collection. Each context record stores direct and inherited permissions in native scalar fields:

```text
acl_enabled
acl_direct_read_principal_ids
acl_direct_write_principal_ids
acl_direct_manage_principal_ids
acl_inherited_read_principal_ids
acl_inherited_write_principal_ids
acl_inherited_manage_principal_ids
```

`acl_direct_*` is the ACL assigned to the current node. `acl_inherited_*` is the union of all ancestor direct ACLs. Effective permission is their union; there is no separate ACL collection.

The request principals are `user:{ctx.user_id}`, `user:*`, and one `group:{group_id}` for each ID in `ctx.group_ids`. Within the `viking://resources` scope, `find/search` uses native `list<string>` filters over `acl_direct_read_principal_ids` and `acl_inherited_read_principal_ids`; private resources remain isolated by URI owner. Legacy records without ACL fields are treated as `acl_enabled=false`, so they do not require a full data backfill.

A retrieval target URI is only a search scope; the caller does not need to read the target node itself. A user can discover a deeply shared file even when intermediate directories are not readable.

Shared-scope context writes preserve an existing direct ACL for the same URI and derive inherited ACL fields for new nodes from their parent. Re-embedding and ordinary replacement writes cannot reset controlled records to default visibility or modify ACLs through regular context fields.

## Example

Grant Bob read-only access to a directory:

```bash
ov acl grant viking://resources/project-a --principal user:bob --level viewer
```

Bob can read and retrieve descendants, but cannot write or delete them. Upgrade the grant to editor:

```bash
ov acl grant viking://resources/project-a --principal user:bob --level editor
```

Remove Bob's direct grant from this node:

```bash
ov acl revoke viking://resources/project-a --principal user:bob
```

If an ancestor still grants Bob access, that inherited permission remains effective.

## Related Documentation

- [ACL API](../api/12-acl.md) - HTTP, SDK, and CLI interfaces
- [Multi-Tenant](./11-multi-tenant.md) - Account, user, and role boundaries
- [Viking URI](./04-viking-uri.md) - URI namespaces
- [Retrieval](./07-retrieval.md) - Hierarchical retrieval flow
