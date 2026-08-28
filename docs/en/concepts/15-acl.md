# Resource Access Control (ACL)

OpenViking ACL shares directories or files from the shared resource scope with users or groups inside one account. ACL never changes the account boundary: every grant is limited to the current account.

ACL uses a collaborative-document inheritance model. A directory grant continuously applies to its descendants, while child directories and files can add direct grants. A child ACL does not replace grants inherited from ancestors.

## Supported URIs

ACL applies only to shared resources:

```text
viking://resources/...
```

- An account `ADMIN` implicitly has `manage` on `viking://resources/...`.
- `viking://resources` is a fixed shared scope and cannot carry a direct ACL. ACLs start on files and directories below it.
- `viking://user/{user_id}/resources/...` is private and does not accept ACLs. To share it, move the resource into a writable shared directory and inherit that directory's ACL.

Implicit management is not stored as an ACL entry and cannot be removed by ACL changes. It ensures that shared resources always have an identity that can establish or recover permissions.

## Principals and Levels

ACL entries use typed principals:

- `user:{user_id}`: a user in the current account.
- `group:{group_id}`: a caller-supplied, account-unique group ID.
- `user:*`: any user in the current account.

`group:*` is not supported. Groups are flat. Membership changes do not rewrite resource ACL or context records; they take effect when the next request builds `RequestContext.group_ids`.
Asynchronous parse and semantic tasks created by a request carry the same group identity. After an `add-resource` destination write has been authorized, automatic semantic maintenance preserves that identity and uses an explicit internal ACL bypass instead of changing the caller's role.

| Level | Allowed operations |
|-------|--------------------|
| `read` | Read, list, and `find/search/grep` |
| `write` | `read` capabilities plus write, create, file delete/move, and tag updates |
| `manage` | `write` capabilities plus directory delete/move and ACL management |

Each higher level includes the lower levels. A `manage` grant therefore includes `read` and `write`.

## Inheritance

A node's effective ACL is the union of every ancestor's direct ACL and the node's own direct ACL:

```text
effective(node) = UNION(direct_acl(each ancestor), direct_acl(node))
```

For example:

```text
read user:bob   on viking://resources/A
write group:engineering on viking://resources/A/B
read user:carol on viking://resources/A/B/C/report.md
```

The effective permissions on `report.md` are:

- Bob: `read`
- Members of `engineering`: `write`
- Carol: `read`

Removing the group's direct ACL from `A/B` does not remove entries from `A` or `report.md`. Descendants only lose the permissions contributed by that entry.

## Default Behavior and `acl_enabled`

The account-level `resource_acl.auto_protect_new_content` setting is disabled by
default. While disabled, creating a file or directory under an ACL-free parent
keeps the existing URI namespace visibility and write rules. Under an
ACL-controlled parent, the creator receives direct `manage` and inherits the
parent permissions.

When enabled, a newly created shared file or directory grants its creator direct
`manage` on its first context record even under an ACL-free parent; parent
permissions are still merged as inherited ACL. Existing content is not migrated
or modified, and disabling the setting again affects only later creations.
`add-resource` treats only the generated import root (or the root file with
`no_split`) as the created node: the root gets the direct creator grant and
descendants only inherit it. Re-embedding or replacing an existing context record
does not change its direct ACL.

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
| delete or move a file | write |
| delete or move a directory | manage on the directory and complete subtree |
| manage ACL | manage |
| move destination parent | write |

The server canonicalizes the URI, then uses one authorization entry point for account/owner/actor-peer boundaries, the effective ACL or legacy fallback, and write/delete namespace guards.

Under an ACL-controlled parent, or when the account enables
`auto_protect_new_content`, a new shared node is bootstrapped by its creator's
direct `manage` grant. Otherwise only the shared scope's implicit `manage` identity can
establish the first ACL. Later ACL changes require effective `manage` capability.

An ACL grant on a directory is inherited by every descendant. `list`, `tree`, and other batch results still check every returned node because an ACL-free directory may be visible under legacy URI rules while one of its descendants has entered the ACL-controlled domain through its own ACL.

Within the shared scope, a moved node keeps its direct ACL and recalculates inherited permissions from its new ancestors. A private resource moved into the shared scope carries no ACL and inherits the destination directory; a shared resource moved back to a private area has its ACL cleared.

Recursive tag updates, directory deletion, and directory moves validate the complete affected subtree first. The operation stops if any node lacks the required capability or the subtree cannot be scanned completely.

For a directory, `stat.count` uses the same path and ACL scalar filter and reports the number of context records visible to the caller.

## Retrieval Filtering

ACL data exists only in the context collection. Each context record stores direct and inherited permissions in native scalar fields:

```text
acl_enabled
acl_direct_grants
acl_inherited_grants
```

`acl_direct_grants` is the ACL assigned to the current node. `acl_inherited_grants` is the union of all ancestor direct ACLs. Each principal stores only its highest level as `{mask}:{principal}`: `1` means `read`, `3` means `write`, and `7` means `manage`. For example, `3:group:dev` gives `group:dev` `write` and therefore also `read`. Effective permission is the union of the two fields; there is no separate ACL collection.

The request principals are `user:{ctx.user_id}`, `user:*`, and one `group:{group_id}` for each ID in `ctx.group_ids`. For reads, `find/search` matches the `1`, `3`, and `7` tokens for each principal against both native `list<string>` grant fields within the `viking://resources` scope; private resources remain isolated by URI owner. Legacy records without ACL fields are treated as `acl_enabled=false`, so they do not require a full data backfill.

A retrieval target URI is only a search scope; the caller does not need to read the target node itself. A user can discover a deeply shared file even when intermediate directories are not readable.

Shared-scope context writes preserve an existing direct ACL for the same URI.
Under an ACL-controlled parent, or when the account enables
`auto_protect_new_content`, a newly created node receives direct `manage` for its
creator and derives inherited ACL fields from its parent; otherwise it remains
`acl_enabled=false`. Descendants created by `add-resource` only inherit from the
import root. Re-embedding and ordinary replacement writes cannot reset controlled
records to default visibility or modify ACLs through regular context fields.

## Example

Grant Bob read-only access to a directory:

```bash
ov acl grant viking://resources/project-a --principal user:bob --level read
```

Bob can read and retrieve descendants, but cannot write or delete them. Upgrade the grant to `write`:

```bash
ov acl grant viking://resources/project-a --principal user:bob --level write
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
