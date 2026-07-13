# Resource Access Control (ACL)

OpenViking ACL shares resource directories or files with selected users inside one account. ACL never changes the account boundary: every grant is limited to the current account.

ACL uses a collaborative-document inheritance model. A directory grant continuously applies to its descendants, while child directories and files can add direct grants. A child ACL does not replace grants inherited from ancestors.

## Supported URIs

ACL applies to these resource scopes:

```text
viking://resources/...
viking://user/{user_id}/resources/...
```

- An account `ADMIN` is an implicit manager of `viking://resources/...`.
- The `{user_id}` in `viking://user/{user_id}/resources/...` is an implicit manager of that user resource tree.

Implicit management is not stored as an ACL entry and cannot be removed by ACL changes. It ensures that public and user resources always have an identity that can establish or recover permissions.

## Principals and Levels

An ACL principal is a raw `user_id` in the current account. The reserved value `*` means any user in the current account.

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
viewer bob   on viking://resources/A
editor alice on viking://resources/A/B
viewer carol on viking://resources/A/B/C/report.md
```

The effective permissions on `report.md` are:

- Bob: `viewer`
- Alice: `editor`
- Carol: `viewer`

Removing Alice's direct ACL from `A/B` does not remove entries from `A` or `report.md`. Descendants only lose the permissions contributed by that entry.

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
| read, list, tree, find, search, grep, glob, relations | read |
| write, create, mkdir, set tags | write |
| delete, manage ACL | manage |
| move source | manage |
| move destination parent | write |

An ACL grant on a directory is inherited by every descendant. `list`, `tree`, and other batch results still check every returned node because an ACL-free directory may be visible under legacy URI rules while one of its descendants has entered the ACL-controlled domain through its own ACL.

When a file or directory moves, its direct ACL moves with it. Permissions inherited from the old ancestors do not move, and ACLs on the new ancestors are recalculated into the effective permissions.

Recursive tag updates, directory deletion, and directory moves validate the complete affected subtree first. The operation stops if any node lacks the required capability or the subtree cannot be scanned completely.

## Retrieval Filtering

ACL data exists only in the context collection. Each context record stores direct and inherited permissions in native scalar fields:

```text
acl_enabled
acl_direct_read_user_ids
acl_direct_write_user_ids
acl_direct_manage_user_ids
acl_inherited_read_user_ids
acl_inherited_write_user_ids
acl_inherited_manage_user_ids
```

`acl_direct_*` is the ACL assigned to the current node. `acl_inherited_*` is the union of all ancestor direct ACLs. Effective permission is their union; there is no separate ACL collection.

`find/search` filters directly in the vector database by `account_id`, URI scope, `acl_direct_read_user_ids`, and `acl_inherited_read_user_ids`. Legacy records without ACL fields are treated as `acl_enabled=false`, so they do not require a full data backfill.

A retrieval target URI is only a search scope; the caller does not need to read the target node itself. A user can discover a deeply shared file even when intermediate directories are not readable.

Every context write preserves an existing direct ACL for the same URI and derives inherited ACL fields for new nodes from their parent. Re-embedding and ordinary replacement writes cannot reset controlled records to default visibility or modify ACLs through regular context fields.

## Example

Grant Bob read-only access to a directory:

```bash
ov acl grant viking://resources/project-a --user-id bob --level viewer
```

Bob can read and retrieve descendants, but cannot write or delete them. Upgrade the grant to editor:

```bash
ov acl grant viking://resources/project-a --user-id bob --level editor
```

Remove Bob's direct grant from this node:

```bash
ov acl revoke viking://resources/project-a --user-id bob
```

If an ancestor still grants Bob access, that inherited permission remains effective.

## Related Documentation

- [ACL API](../api/12-acl.md) - HTTP, SDK, and CLI interfaces
- [Multi-Tenant](./11-multi-tenant.md) - Account, user, and role boundaries
- [Viking URI](./04-viking-uri.md) - URI namespaces
- [Retrieval](./07-retrieval.md) - Hierarchical retrieval flow
