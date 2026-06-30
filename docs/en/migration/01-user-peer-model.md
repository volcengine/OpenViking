# OpenViking 0.3.x to 0.4.0 Upgrade Guide

This guide is for users already running OpenViking 0.3.x. It explains what to do before and after upgrading to 0.4.0, which legacy usage remains compatible, how to migrate data, and how to move application code to the new model.

## Upgrade Decision

If you stay on 0.3.x, existing `agent_id`, `viking://agent/...`, and `viking://session/...` behavior does not change. But you also cannot use the 0.4.0 model and tools:

- No User / Peer data model.
- No legacy agent/session migration or cleanup command.
- No request-level `actor_peer_id` peer view.
- Future fixes and features around the new model are not backported to the old model.

If you upgrade to 0.4.0, you do not need to migrate data immediately. 0.4.0 keeps runtime compatibility so old data remains readable:

- `agent_id` can still be configured temporarily. In current HTTP SDK clients it maps to request-level `actor_peer_id`; true legacy agent mode is selected only when requests send `X-OpenViking-Agent`.
- `viking://agent/...` can still read old agent data, but is read-only.
- `viking://session/...` can still read old session data and merges with the new session view.
- `find` / `search` read unmigrated legacy agent data only for true legacy requests that still send `X-OpenViking-Agent`; HTTP SDK `agent_id` now selects only the actor peer view.

Recommended order:

```text
Back up
  -> Upgrade server / CLI / SDK
  -> Verify old data is still readable
  -> Run data migration
  -> Verify new paths
  -> Gradually update application usage
  -> Optionally run cleanup
```

## Back Up First

Create a backup with a 0.3.x-compatible package. `0.3.24` is recommended:

```bash
pip install openviking==0.3.24 --upgrade --force-reinstall
ov backup ./backups/openviking-before-0.4.0.ovpack
```

Check the current version:

```bash
python -c "import openviking; print(openviking.__version__)"
ov version
```

Do not run `ov --sudo admin migrate` on 0.3.x. The migration command is only available in 0.4.0 or later.

## Upgrade Server and Clients

Install 0.4.0, restart the server, and make sure CLI / SDK clients are on the same version line:

```bash
pip install openviking==0.4.0 --upgrade --force-reinstall
openviking-server --config ov.conf
```

If you use the Rust `ov` CLI from the repository, rebuild or reinstall it. Otherwise the `ov` binary on PATH may still be the old one.

After upgrading, validate config and legacy reads:

```bash
ov config validate
ov ls viking://agent
ov ls viking://session
ov session list
```

The `viking://session` merged compatibility view is implemented on the server. Upgrading only the CLI does not change server-side reads.

## Compatibility Matrix

| Legacy usage | 0.4.0 behavior |
| --- | --- |
| Client config `agent_id` | Supported. In current HTTP SDK clients it maps to request-level `actor_peer_id`; it no longer triggers legacy agent mode by itself. |
| `ov ls viking://agent` | Supported for reads. If `agent_id` / `actor_peer_id` is set, only the current actor peer's legacy agent is shown. |
| Read `viking://agent/<agent_id>/...` | Supported for old data. |
| Write `viking://agent/...` | Not supported. New writes should go to `viking://user/<user_id>/peers/<peer_id>/...`. |
| `ov ls viking://session` | Supported for reads. It merges new and old sessions. |
| Read `viking://session/<session_id>/...` | Supported. New path first, legacy path as fallback. |
| Write `viking://session/...` | Not supported. New sessions are written to `viking://user/<user_id>/sessions/...`. |
| `find` / `search` with HTTP SDK `agent_id` | Supported. It searches the selected actor peer view only; old agent data is searched only by true legacy requests that send `X-OpenViking-Agent`. |
| `find` / `search` body `peer_id` | Not supported. Use `actor_peer_id` or `X-OpenViking-Actor-Peer` for the new peer view. |
| Configure both `actor_peer_id` and `agent_id` | Not supported. The client/request fails. |
| Explicit message `peer_id` while using HTTP SDK `agent_id` | Supported. The explicit message `peer_id` wins for that message; legacy fallback applies only when no message `peer_id` is provided. |
| `role_id` memory isolation | Not supported. It is ignored after upgrade. |

## Run Migration

After confirming that legacy data is readable, run migration:

```bash
ov --sudo admin migrate --output json
```

The response returns a task id:

```json
{
  "task_id": "..."
}
```

Check task status:

```bash
ov --sudo task status <task_id>
ov --sudo task list --task-type legacy_migration
```

HTTP API:

```http
POST /api/v1/admin/migrate
X-API-Key: <root-key>
```

The request body can be empty. It is equivalent to:

```json
{
  "action": "migrate"
}
```

Query the task:

```http
GET /api/v1/tasks/{task_id}
X-API-Key: <root-key>
```

ROOT can query migration tasks without normal account/user filtering. Migration creates one root-level task for the whole storage, not one task per account.

## Migration Rules

0.4.0 uses the User / Peer model:

```text
User = natural person or business user
Peer = interaction identity under a User
Session = conversation state under a User
Skill = executable skill under a User
```

Migration targets:

| Legacy data | New location |
| --- | --- |
| `viking://agent/<agent_id>/memories/...` | `viking://user/<user_id>/peers/<agent_id>/memories/...` |
| `viking://agent/<agent_id>/resources/...` | `viking://user/<user_id>/peers/<agent_id>/resources/...` |
| `viking://agent/<agent_id>/skills/<skill>/...` | `viking://user/<user_id>/skills/<skill>/...` |
| `viking://session/<session_id>/...` | `viking://user/<user_id>/sessions/<session_id>/...` |

Shared legacy agent data is copied into each target user's peer directory. If the legacy path already identifies a user owner, it is migrated only to that user.

Migration also handles existing vector records: for memory / resource / skill files or directories that were actually copied, it reads the old record's `vector` / `sparse_vector` and scalar fields, rewrites the URI, and writes a new record. Migration does not re-embed content and does not automatically call `reindex`. When shared legacy agent data is copied to multiple users, vector records are copied once per target user URI.

Old scalar-only records without a vector payload are skipped and counted in `migrated.skipped_vector_records`. Session migration copies file state only and does not handle vector records.

Session owner is resolved in this order:

1. `.meta.json.created_by_user_id`
2. `.meta.json.user_id`, `.meta.json.owner_user_id`, or `.meta.json.created_by`
3. A user hint in the legacy path, such as `/session/alice/sess-001`
4. The only registered user in a single-user account

In a multi-user account, a legacy session without an identifiable owner fails preflight. Runtime compatibility can keep that old session readable temporarily, but owner metadata should be fixed before migration.

Legacy agent instructions are not migrated:

```text
viking://agent/<agent_id>/instructions
```

Migration records a warning and does not create a replacement directory.

## Preflight Checks

Preflight fails before creating a task for:

- Legacy data under a physical account that is not present in the API key user registry.
- A legacy session in a multi-user account with no identifiable owner.
- A session owner that is present but is not a valid OpenViking user id.

Preflight records warnings or skips, then continues, for:

- A target user already has a skill with the same name. The legacy skill is skipped and the existing skill is not overwritten.
- Legacy agent instructions are found. Instructions are not migrated.
- A shared legacy agent exists but no target users exist in the account.

If migration finds legacy data owned by a user missing from the registry, it registers that user automatically. The task result records created users but does not return plaintext user keys.

If `api_key_hashing` is enabled, plaintext keys cannot be recovered from storage. Regenerate the key:

```bash
ov --sudo admin regenerate-key <account_id> <user_id>
```

## Verify Migration

Check the task result:

```bash
ov --sudo task status <task_id>
```

Review:

- `migrated.files` / `migrated.directories`
- `migrated.vector_records` / `migrated.skipped_vector_records`
- `migrated.operations`
- `skipped`
- `warnings`
- `created_users`

Verify new paths:

```bash
ov ls viking://user/<user_id>/peers/<agent_id>/memories
ov ls viking://user/<user_id>/skills
ov ls viking://user/<user_id>/sessions
```

Migration copies data and does not delete legacy paths or old vector records. Re-running it is idempotent: existing target files and skills are skipped instead of overwritten. If retrieval after migration is not as expected, run `reindex` manually on the new paths; the migration flow itself never triggers reindex.

## Application Migration

### Client Config

Legacy config can keep working during the migration window:

```json
{
  "agent_id": "legacy-agent"
}
```

Recommended new config:

```json
{
  "actor_peer_id": "legacy-agent"
}
```

Do not configure both:

```json
{
  "actor_peer_id": "customer-a",
  "agent_id": "legacy-agent"
}
```

This fails.

### File Paths

Legacy paths:

```text
viking://agent/code-agent/memories/profile.md
viking://session/sess-001/messages.jsonl
```

New paths:

```text
viking://user/alice/peers/code-agent/memories/profile.md
viking://user/alice/sessions/sess-001/messages.jsonl
```

`viking://session/<session_id>` can remain a read alias for the current user's session, but new writes and long-term references should use `viking://user/<user_id>/sessions/<session_id>`.

### find / search

During the migration window, if you still need unmigrated old agent data, use a true legacy request path that sends `X-OpenViking-Agent`. Current HTTP SDK `agent_id` is an actor-peer alias and no longer makes `find` / `search` include old agent data:

```json
{
  "query": "deployment notes",
  "agent_id": "legacy-agent"
}
```

After migration, when old agent data is no longer needed, move all clients to client/request-level `actor_peer_id`.

### Session Messages

A legacy request without an explicit assistant-message `peer_id` can still fall back to its legacy agent id. Current HTTP SDK clients should not rely on `agent_id` for message attribution; use message `peer_id` when you need to identify the speaker.

## Deferring Data Migration

It is acceptable as a short-term transition, with these limits:

- Old agent/session data is readable, but legacy namespaces are not writable.
- New sessions and new resources are written to the new namespace, so data can exist in both old and new paths for a while.
- True legacy retrieval searches both old and new data when the request sends `X-OpenViking-Agent`; HTTP SDK `agent_id` and pure `actor_peer_id` do not search old `viking://agent` by default.
- In multi-user accounts, old sessions without clear owner metadata may be readable at runtime but will fail formal migration preflight.
- Legacy directories and old vector records remain until cleanup.

This is intended as a migration window, not a long-term state.

## Optional Cleanup

After verifying migration, delete legacy namespaces:

```bash
ov --sudo admin migrate --cleanup --output json
ov --sudo task status <cleanup_task_id>
```

HTTP request body:

```json
{
  "action": "cleanup"
}
```

Cleanup deletes only:

```text
/local/<account>/agent
/local/<account>/session
/local/<account>/user/<user>/agent
```

Cleanup deletes old vector records under those legacy URI scopes before deleting the matching AGFS directories. If vector read or delete fails, that directory is skipped so files are not removed while stale index records remain. Cleanup does not delete files or vector records under the new user / peer paths.

It does not delete new model directories:

```text
/local/<account>/user/<user>/peers
/local/<account>/user/<user>/sessions
/local/<account>/user/<user>/skills
```

After cleanup, `viking://agent/...` is no longer the way to read migrated peer data. Use the new paths. `viking://session/...` can still be used as an alias for the current user's new sessions.

## FAQ

### ov ls viking://agent shows only one agent

If `agent_id` or `actor_peer_id` is configured, this is expected. The `viking://agent` root is filtered to the current actor peer.

### ov ls viking://session is still empty

Make sure the server has been restarted on 0.4.0. The `viking://session` merged read view runs on the server; upgrading only the CLI is not enough.

### Config has both actor_peer_id and agent_id

This is not allowed. Keep `agent_id` for legacy mode, or remove it and use `actor_peer_id`.

### Preflight reports an unknown account

The physical storage contains legacy data for an account missing from the API key registry. Restore or recreate that account before migrating.

### Preflight reports an unresolved session owner

Add owner metadata to the legacy session `.meta.json`, or move the session under a path that clearly identifies the owner, then run migration again.

### A skill did not migrate

Check the task `skipped` list. The usual reason is that the target user already had a skill with the same name. Migration does not overwrite existing skills.
