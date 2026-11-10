# Access control

ACL determines who can read, edit, or manage shared sources within one account. Use it for project collaboration, selected readers, and group-based maintenance.

This page creates a shared directory managed by Alice and readable by Bob, then checks reading, denied writes, and revocation. Account administrators retain implicit management access to shared sources.

## Prerequisites

Complete [Authentication](17-authentication.md) and prepare `acme-admin`, `acme-alice`, and `acme-bob` connections. Alice and Bob are ordinary users so that the checks exercise their grants.

ACL applies to `viking://resources/...`. Personal memories, private sources, and Peer spaces retain their User boundary. See [Knowledge sharing](22-sharing.md) to share selected content from those spaces.

## Enable ACL

Acme's administrator sets the service address and enters team-admin's Admin key with hidden input:

```bash
export OV_URL="http://127.0.0.1:1933"
read -r -s ADMIN_KEY
export ADMIN_KEY
```

Enable ACL checks for the account:

```bash
curl -sS -X PATCH "$OV_URL/api/v1/admin/accounts/acme/configuration" \
  -H "X-API-Key: $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"settings":{"acl":{"enabled":true}}}'
```

Check that `result.settings.acl.enabled` is `true`. This switch defaults to off; shared sources are not ACL-filtered while it is off. Writing grants does not enable the switch automatically.

## Create a restricted directory

The shared root grants `manage` to everyone in the account, and descendants inherit parent permissions. Use `restricted` to cut off inheritance and assign direct grants when limiting access to selected members.

The administrator creates a new example directory:

```bash
ov config switch acme-admin
ov mkdir viking://resources/team-knowledge \
  --acl '{"acl_mode":"restricted","entries":[{"principal":"user:alice","level":"manage"},{"principal":"user:bob","level":"read"}]}'
```

If it already exists, inspect its grants with `ov acl get`. Choose a fresh name for this walkthrough to preserve existing grants.

| Level | Allowed operations |
| --- | --- |
| `read` | Read, list, and retrieve |
| `write` | Includes read, plus writing, creating, deleting, or moving files |
| `manage` | Includes write, plus managing ACL and deleting or moving directories |

## Write a source

Alice publishes a note. Writing triggers semantic and vector processing, requiring available models and incurring model calls:

```bash
ov config switch acme-alice
ov write viking://resources/team-knowledge/access-check.md \
  --mode create --content "Team agreement: summarize project feedback every Friday." --wait
```

The new file inherits the directory grants. Inspect the directory ACL and original content:

```bash
ov acl get viking://resources/team-knowledge
ov read viking://resources/team-knowledge/access-check.md
```

If waiting times out, check the returned task ID and continue after processing completes.

## Verify as Bob

Bob uses his own connection to read and retrieve:

```bash
ov config switch acme-bob
ov read viking://resources/team-knowledge/access-check.md
ov find "When is project feedback summarized?" --uri viking://resources/team-knowledge
```

Reading should succeed and retrieval should include the note. Try creating a file:

```bash
ov write viking://resources/team-knowledge/bob-check.md \
  --mode create --content "Bob's write check."
```

Bob has only `read`, so the write should be denied. If it succeeds, check whether the active identity is an administrator, whether account ACL is enabled, and whether other effective grants permit writing.

## Change grants

Alice has `manage` and can grant Bob editing access:

```bash
ov config switch acme-alice
ov acl grant viking://resources/team-knowledge --principal user:bob --level write
```

Bob can rerun the preceding write command; it should now succeed. To return to reading only, set the level back to `read`. To end access, revoke the direct grant:

```bash
ov acl grant viking://resources/team-knowledge --principal user:bob --level read
ov acl revoke viking://resources/team-knowledge --principal user:bob
```

Alice runs these two commands. Switch to Bob afterward; reading `access-check.md` should be denied. Permission indexing can have a short delay. Check every two seconds for up to 30 seconds initially; if access continues, inspect effective permissions and other grant sources.

`revoke` removes the direct entry on this node. Access from a parent or group remains effective. Revocation also does not remove already downloaded copies. To continue with the next sharing walkthrough, Alice restores Bob's `read` grant.

## Grant to a group

For larger teams, an administrator can create an account-local group and manage its members, then grant access to `group:<group_id>`. Membership changes apply to subsequent requests. This is useful for project subteams; see [Admin API](../api/08-admin.md) for group and membership operations.

## Next steps

Continue to [Knowledge sharing](22-sharing.md) to publish accumulated project knowledge into this controlled directory. See [ACL concepts](../concepts/15-acl.md) for inheritance and directory-move behavior.
