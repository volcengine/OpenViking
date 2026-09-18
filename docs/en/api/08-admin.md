# Admin (Multi-tenant)

The Admin API manages accounts, users, and groups in a multi-tenant environment. It covers workspace (account) creation/deletion, user registration/removal, group membership, role changes, and API key regeneration.

This API is available in both `api_key` and `trusted` deployments:
- In `api_key` mode, the effective role is always derived from the presented API key.
- In `trusted` mode, ordinary requests still do not use user-key registration. When a configured `root_api_key` is presented to `/api/v1/admin/*`, the trusted upstream is authorized as ROOT.

For `/api/v1/admin/*`, `trusted` mode permits requests with no explicit identity headers, and also permits target identity headers when they match the account/user in the URL. These requests are treated as ROOT after the deployment's `root_api_key` is verified. For ordinary trusted-mode data APIs, role and identity still come from `X-OpenViking-Account` + `X-OpenViking-User`.

## Roles and Permissions

| Role | Description |
|------|-------------|
| ROOT | System administrator with full access |
| ADMIN | Workspace administrator, manages users within their account |
| USER | Regular user |

| Operation | ROOT | ADMIN | USER |
|-----------|------|-------|------|
| Create/delete workspace | Y | N | N |
| List workspaces | Y | N | N |
| Register/remove users | Y | Y (own account) | N |
| Manage groups and membership | Y | Y (own account) | N |
| List agents (deprecated, returns empty list) | Y | Y (own account) | N |
| Regenerate user key | Y | Y (own account) | N |
| Promote user to ADMIN | Y | Y (own account) | N |

## CLI `--sudo` Option

When using the `ov` CLI to perform admin operations requiring ROOT privileges, you can use the `--sudo` option. This option uses the `root_api_key` from your `~/.openviking/ovcli.conf` instead of the regular `api_key`.

### Configuration Requirements

Configure `root_api_key` in `~/.openviking/ovcli.conf`:

```json
{
  "url": "http://localhost:1933",
  "api_key": "alice-user-key",
  "root_api_key": "your-root-api-key",
  ...
}
```

### Commands Supporting `--sudo`

- `ov --sudo admin` - Account and user management
- `ov --sudo system` - System utility commands
- `ov --sudo reindex` - Rebuild indexes
- `ov --sudo admin migrate` - Legacy agent/session migration and cleanup
- `ov --sudo task status/list` - Query root/system background tasks, such as migration tasks

### Usage Limitations

- `--sudo` only works with the commands above - using it with regular data commands will error
- Must have `root_api_key` configured to use `--sudo`

## Groups

A group belongs to one account and lets one ACL principal grant access to multiple users. Its caller-supplied `group_id` follows the same identifier rules as `user_id` and is the account-unique, stable identifier; there is no separate group name. Only existing users from the same account can be members, and groups cannot be nested.

The server adds memberships to `RequestContext.group_ids` for each request. Adding or removing a member takes effect on the next request without rewriting resource ACL or context records. Removing a user also removes all memberships. A group must be empty before deletion.

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/admin/accounts/{account_id}/groups` | Create an empty group with `{"group_id":"engineering"}` |
| GET | `/api/v1/admin/accounts/{account_id}/groups` | List groups |
| DELETE | `/api/v1/admin/accounts/{account_id}/groups/{group_id}` | Delete an empty group |
| GET | `/api/v1/admin/accounts/{account_id}/groups/{group_id}/members` | List members |
| PUT | `/api/v1/admin/accounts/{account_id}/groups/{group_id}/members/{user_id}` | Add a member idempotently; repeated calls return `added=true` |
| DELETE | `/api/v1/admin/accounts/{account_id}/groups/{group_id}/members/{user_id}` | Remove a member; repeated calls return `removed=false` |

```bash
ov --sudo admin create-group acme engineering
ov --sudo admin add-group-member acme engineering alice
ov acl grant viking://resources/project-a \
  --principal group:engineering --level read
ov --sudo admin remove-group-member acme engineering alice
ov --sudo admin delete-group acme engineering
```

The Python SDK exposes `admin_create_group`, `admin_list_groups`, `admin_list_group_members`, `admin_add_group_member`, `admin_remove_group_member`, and `admin_delete_group`. The Go SDK uses matching PascalCase method names.

## API Reference

### get_agent_evolution_status

Return the effective Agent Evolution switch for the caller's account. ROOT
operates on the configured default account; ADMIN operates on its own account.

**HTTP API**

```
GET /api/v1/admin/agent-evolution
```

```bash
curl http://localhost:1933/api/v1/admin/agent-evolution \
  -H "X-API-Key: <root-key>"
```

**Response Example**

```json
{
  "status": "ok",
  "result": {
    "enabled": false,
    "account_id": "default"
  },
  "time": 0.1
}
```

`enabled` is the account override from
`/local/{account_id}/_system/setting.json`, or
`server.agent_evolution.enabled` when no override exists. Session commits read
this effective value without restarting the server.

The existing update endpoint name is unchanged:

```http
PUT /api/v1/admin/agent-evolution
Content-Type: application/json

{"enabled": true}
```

### account_settings

ROOT can manage any account and ADMIN can manage only its own account. The
generic settings endpoint accepts only explicitly allowlisted fields. It currently
allows `agent_evolution.enabled` and `acl.enabled`.

```http
GET /api/v1/admin/accounts/{account_id}/settings
PATCH /api/v1/admin/accounts/{account_id}/settings
Content-Type: application/json

{
  "agent_evolution": {"enabled": true},
  "acl": {"enabled": true}
}
```

`acl.enabled` defaults to `false`. While disabled, shared resources use the
original public behavior and ACL authorization is skipped. When enabled, newly
created shared resources receive ACL fields and ACL-protected shared resources
are authorized against them. Existing content without an ACL is not migrated or
modified. Disabling the setting also stops enforcing existing ACLs.

```bash
ov --sudo admin set-account-settings acme --acl-enabled true
```

Before an existing setting is replaced, it is backed up to
`/local/{account_id}/_system/setting.backup.json`.

### account_memory_templates

ROOT can manage any Account; ADMIN can manage only its own Account. Ordinary
Users cannot use these endpoints. Authorization is role-based, not based on
whether the User is named `default`.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/admin/accounts/{account_id}/memory-templates` | List the six editable templates, full defaults and effective values |
| GET | `/api/v1/admin/accounts/{account_id}/memory-templates/{memory_type}` | Read one template |
| PUT | `/api/v1/admin/accounts/{account_id}/memory-templates/{memory_type}` | Complete and publish one template |
| DELETE | `/api/v1/admin/accounts/{account_id}/memory-templates/{memory_type}` | Remove that override and restore deployment defaults |

The kernel accepts the existing memory YAML structure as a JSON object and enforces
the following editing allowlist at the API boundary. Only these six types are
exposed. Experience, Cases, Trajectories and other types are not exposed for reading
or editing. These APIs cannot create, delete or rename Memory Types; DELETE removes
only the Account override.

| Type | Editable configuration |
|------|------------------------|
| `profile` | `description`; `fields.content.description` |
| `events` | `description`; `fields.event_name.description`, `fields.summary.description`; `content_template` |
| `preferences` | `description`; `fields.topic.description`, `fields.content.description` |
| `entities` | `description`; `fields.category.description`, `fields.name.description`, `fields.content.description` |
| `soul` | `description`; `fields.core_truths.description`, `fields.boundaries.description`, `fields.vibe.description`, `fields.continuity.description`; `content_template` |
| `identity` | `description`; `fields.creature.description`, `fields.name.description`, `fields.vibe.description`, `fields.avatar.description`, `fields.emoji.description`, `fields.introduction.description`; `content_template` |

Here `fields.<name>.description` selects an existing entry in the `fields` array
by `name`; it does not replace that field. JSON keys are case-sensitive: use
`description`, not `Description`. Profile's `content` field permits only its
description to change.

All other configuration stays locked to deployment defaults: `memory_type`,
`enabled`, `operation_mode`, `stage`, `peer_enabled`, `directory`,
`filename_template`, field names/types/merge operations/initial values,
`embedding_template`, `overview_template`, and unlisted field descriptions.
Profile keeps `profile.md` and content's `merge_op=patch`; Events keeps
`add_only` and the descriptions of `goal` and `ranges`; Identity keeps Name's
immutable merge rule. Profile, Preferences and Entities cannot edit
`content_template`. Changing topic/category/name/event_name instructions can
still indirectly affect future paths/names, without changing their templates.

Example: change only the type description:

```bash
curl -X PUT "$OV_ENDPOINT/api/v1/admin/accounts/acme/memory-templates/profile" \
  -H "X-API-Key: $OV_ADMIN_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"description":"Remember business facts in concise English."}'
```

PUT completes omitted values from **deployment defaults**, not the previous
Account override, and persists a **complete YAML template**. `fields` updates
existing entries by name, replacing only permitted descriptions; all omitted
fields and attributes retain default values. Fields cannot be added, removed or
renamed, and an empty list does not remove any fields. Full GET `effective`
objects can be submitted unchanged: locked values matching defaults are accepted.
Changed locked values, unknown keys/fields and duplicate field names return
`INVALID_ARGUMENT` without modifying the active file. To edit one description while
preserving all other customizations, GET `effective`, modify that object, then
PUT it. An empty object publishes a complete copy of the defaults as custom;
DELETE removes the custom file. DELETE is idempotent.

Results contain `memory_type`, `status` (`system_default` or `custom`),
`updated_at` (UTC publication time or null), and full `defaults` / `effective`
objects using YAML field names such as `fields[].type`. List returns
`result.account_id` and `result.templates`; single-template operations return
`result.account_id` plus the template result.

Storage is per Account and per type:

```text
/local/{account_id}/_system/memory_templates/
  profile.yaml
  preferences.yaml
  events.yaml
  ...
```

Only published overrides create files. Each file contains the complete schema
and an internal `_updated_at` timestamp; no `memory_templates.json` is used.
The previous content is backed up to `{type}.yaml.backup`. Access goes through
AGFS, preserving the deployment's encryption/storage configuration. Do not edit
encrypted backing files directly. `setting.json` and User `user_config.json`
are unchanged. Personal deployments use the default Account; enterprise
deployments use the target Account, with no separate kernel storage layout.

Template reads do not acquire locks. Publication writes a unique staging file in
the same directory, then switches the active path through AGFS: LocalFS uses a
file rename; S3 copies the complete object over the destination before deleting
the staging object, without deleting the destination first. Readers may see the
complete old or new version, or deployment defaults before the first publication
and after DELETE. This is per-file publication, not a transaction across an
entire template listing or registry snapshot.
Writers/DELETE still use cross-process locks; publication locks cover both the
active and staging paths. Contention uses zero-wait attempts with asynchronous
backoff for up to 10 seconds, leaving executor threads available for I/O/release.
A failed staging write leaves the active file unchanged. Post-publication cleanup
errors only produce warnings, never an in-place rollback. If the move reports an
error, the destination is checked: a verified publication is retained; an
unverifiable outcome returns an error without blindly restoring the old version.
Use GET to confirm its state. Cancellation stops lock retries, but already
submitted native I/O is drained and locks released before cancellation propagates;
cancelling an in-progress publication does not guarantee that it is undone.
Process crashes or cleanup failures can leave `.tmp` files, which readers ignore.

The ordinary Session memory extraction pipeline loads Account templates before
schema filtering and initial-file generation. The resulting registry snapshot is
used for extraction, patch merging, and memory-file updates. A later publication
does not change an in-flight extraction's snapshot. Streaming updates compare the
current memory type's schema values (including the rendering mode), not the whole
registry: changes to unrelated types do not split a batch. Different schemas are
merged/rendered separately. If those groups target the same file, before or after
patch merging, the updater raises a conflict before applying any memory operations
in that merge batch. Re-extract with current templates and file contents before
retrying; replaying the old patches is not a fix. This is fail-fast conflict
handling, not automatic rebasing or an atomic transaction across a whole Commit;
other memory-type groups or append-only writes may already have completed.
Work queued before publication
uses the configuration at **extraction start**, not at HTTP Commit acceptance.
All eligible Users/Peers in that Account share the templates; no shared deployment
registry is mutated. Publishing/resetting does not proactively rewrite existing
memories; subsequent commits can update them according to the effective rules.

Editable descriptions and content templates must be nonempty strings;
each serialized file is limited to 1 MiB. Descriptions (both type-level and
`fields[].description`) share one restricted Jinja contract, whether they come
from deployment defaults or an Account override. Editing a description does not
disable rendering, and no description provenance flag is stored or checked.
Only the existing `language` context variable is available; body fields,
`extract_context`, and arbitrary objects are not exposed. The syntax subset is
the same as the restricted bodies below: conditionals, local variables, bounded
literal loops, safe string methods and tests, but no filters or arbitrary calls.
For example, <code v-pre>Use {{ language.upper() }}.</code> renders as `Use EN.` when the existing
schema-rendering context supplies `language=en`. No new language propagation is
introduced; the Python protocol's existing static field-description path remains
unchanged. Missing language retains the previous undefined/empty-output behavior;
use `language or 'English'` for a fallback. Context values are not recursively
evaluated as Jinja. Invalid custom expressions are rejected before publication,
and persisted overrides are revalidated before extraction. Deployment descriptions
also use the restricted renderer, so deployment-specific unsupported syntax must
be migrated rather than receiving a trust exemption. Descriptions are limited to
2048 AST nodes and 1 MiB rendered output. `content_template` keeps its separate
variable/source-size contract and inherited-body compatibility below.
Each editable `description`
(type-level or `fields[].description`) is limited to 50,000 Unicode code points,
including whitespace and template-like text. This is a per-field source-character limit,
not a UTF-8 byte, rendered-output or combined-description limit. Oversized updates
return 400 without modifying the active configuration. Publication does not invoke
an LLM. Storage failures/corrupt files
are reported, not silently treated as defaults. This change adds no public
file-browser directory, SDK/CLI commands, drafts or version-history UI.

#### Managed content-template contract

The restricted contract below applies only when the Account body differs from the
current deployment default. At publication and extraction load, the server compares
the complete `content_template` string against its own deployment registry. An exact
match uses the existing deployment renderer, including its filters and helpers;
there is no client-supplied trust flag. This includes description-only PUT, empty
PUT, and GET `effective` → PUT when the body is unchanged. The override can still
have `status=custom` even though its body is inherited. The bundled Events YAML and
its existing date expression are unchanged.

Equality is exact, including whitespace. A modified body must pass the restricted
contract even if it was based on a deployment template. If deployment defaults
later change, the stored body is compared again on the next extraction load; a
previous match does not grant permanent trust. A nonmatching body outside the
allowlist must be replaced/reset before extraction can use it. Already-started
extractions retain their resolved snapshot.

`content_template` formats extracted/merged fields into Markdown. Administrators
may change headings, order, fixed text and conditional visibility, including
omitting fields or the Events ChatLog/resource-event branch. Omission does not
disable extraction, remove stored field metadata or delete Session messages.
Events' default embedding template consumes the body, so these edits can affect
retrieval input. Paths, field definitions and merge rules remain locked.

| Type | Content variables |
| --- | --- |
| events | event_name, goal, summary, ranges |
| soul | core_truths, boundaries, vibe, continuity |
| identity | name, creature, vibe, emoji, avatar, introduction |

`language` is a description variable, not a content variable. Only Events
may call these read-only `extract_context` helpers with positional arguments:
`get_resource_event_content(ranges, summary)`,
`get_first_message_time_from_ranges(ranges)`,
`get_first_message_time_with_weekday_from_ranges(ranges)`,
`get_event_content(ranges, summary[, ratio_threshold])`,
`get_year(ranges)`, `get_month(ranges)`, `get_day(ranges)`.
The first argument must be `ranges` directly; missing field values are supplied as
empty strings, so no fallback filter is needed. An explicit ratio must be a numeric
literal from 0 to 1 (omitted: 0.2; built-in: 0).

Supported Jinja: `if/elif/else`, comparisons/boolean expressions, local `set`, and
non-nested/non-recursive `for` over an explicit list/tuple of at most 32 items
(including title/value pairs). `loop.index/index0/first/last/length` are available.
String methods: `.upper()`, `.lower()`, `.strip()`, with no positional or keyword
arguments. These use the same method-call syntax as deployment templates; Account
templates allow only these methods on plain strings. The receiver type is checked
before attribute lookup, so same-named methods/properties on other objects (including
string subclasses) are not allowed. Methods can be chained or used on string fields,
locals, literals, and string results of approved Events helpers. Method references
cannot be stored or accessed without calling them.

No filters are supported in custom Account bodies, including `| upper`,
`| lower`, `| trim`, `| default(...)`, or `| length`; this unreleased interface does
not retain a filter compatibility mode. Use `summary or 'pending'` or an explicit
conditional for fallbacks, and `summary.strip().upper()` for string formatting.
Tests: `defined`, `undefined`, `none`, `string` remain supported.
Built-in field/context names cannot be overwritten. Imports,
inheritance, macros, arbitrary calls/attributes, subscripts, arithmetic/string
multiplication/concatenation and reserved `MEMORY_FIELDS` comments are not allowed.

Limits: 64 KiB UTF-8 source, 2048 AST nodes, 1 MiB rendered body excluding system
metadata. Nonmatching Account bodies are checked at publication and extraction load,
then rendered with a restricted Jinja environment and only approved fields/helpers.
Runtime failures on this restricted path stop that file write instead of falling
back to an empty body. Exact inherited bodies keep the deployment renderer's
existing behavior, including its error/fallback semantics; they are not subject to
the restricted renderer's source/AST/output limits. The complete Account YAML file
is still limited to 1 MiB.
These guards do not replace Worker resource quotas, evaluate extraction quality or sanitize
Markdown/HTML for UI display. Descriptions and restricted content templates share
the syntax sandbox, but expose different variables and use different source limits.

Publication validation failures return `INVALID_ARGUMENT` with `error.details`:
`field=description`, `fields.<name>.description`, or `content_template`, a controlled
`reason`, and `line` when available. The
active configuration remains unchanged. Structurally valid older overrides using
unsupported Jinja can still be read, replaced or reset, but extraction refuses to
execute them unchecked. Corrupt YAML remains an explicit error.

### user_settings

ROOT can manage any User and ADMIN can manage Users in its own account. The
User settings endpoint currently allowlists only `memory_policy`. The shared
top-level `memory_types` filter controls which memory schemas may be extracted.
User memories are routed to Self or Peer according to each message's `peer_id`;
Agent memory types remain Self-only.

```http
GET /api/v1/admin/accounts/{account_id}/users/{user_id}/settings
PATCH /api/v1/admin/accounts/{account_id}/users/{user_id}/settings
Content-Type: application/json

{
  "memory_policy": {
    "memory_types": ["profile", "preferences", "events", "entities", "experiences"]
  }
}
```

The response contains the User-level `memory_policy`, expanded with its default
memory types and Agent-memory dependencies. `experiences` expands to
`cases`, `trajectories`, and `experiences`. The policy is independent of the
account-level Agent Evolution switch, which is managed through the dedicated
account endpoint. Updates are backed up to the User's
`settings/user_config.backup.json` before replacement. A Session without an
explicit policy reads the latest User policy when it is committed; if the User
has no override, it falls back to `server.user_config_defaults.memory_policy`
and then to the kernel default. To clear a persisted User override and resume
inheriting these defaults, PATCH `{"memory_policy": null}`. An empty object
`{"memory_policy": {}}` is an explicit policy and does not clear the override.

---

### create_account

#### 1. API Implementation Overview

Create a new workspace with its first admin user.

**Processing Flow:**
1. Verify requester has ROOT privileges
2. Use API Key Manager to create account and initial admin user
3. Initialize account-level directory structure
4. Initialize admin user's personal directory
5. Write optional initial admin user config
6. Return account info and user key (not in trusted mode)

**Code Entry Points:**
- `openviking/server/routers/admin.py:create_account` - HTTP route
- `openviking/server/api_keys/new.py:APIKeyManager.create_account` - Core implementation
- `openviking_cli/client/sync_http.py:SyncHTTPClient.admin_create_account` - Python SDK

#### 2. Interface and Parameters

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| account_id | str | Yes | - | Workspace ID |
| admin_user_id | str | Yes | - | First admin user ID |
| seed | str | No | `null` | Optional deterministic API key seed. When set, the key secret is `sha256(user_id + "\0" + seed)` |
| user_config | object | No | `null` | Initial config for the first admin user. Supports `add_targets.resource_uri`, `add_targets.skill_uri`, and `memory_policy` |

**Notes:**
- In `trusted` mode, `user_key` is omitted from the response
- Omit `seed` for the default random API key. Treat seed values as secret material; short seeds can make the key guessable.
- Account-level namespace isolation settings are no longer supported. User memory uses user-scoped namespaces, and one-to-many external participants are represented with `peer_id`.
- `user_config.add_targets.resource_uri` must be a writable resource directory URI: `viking://resources` or `viking://resources/...`, `viking://~/resources` or `viking://~/resources/...`, `viking://user/{user_id}/resources` or `viking://user/{user_id}/resources/...`, or `viking://user/{user_id}/peers/{peer_id}/resources` or `viking://user/{user_id}/peers/{peer_id}/resources/...`.
- `user_config.add_targets.skill_uri` must be `viking://~/skills` or `viking://agent/skills`. Explicit `viking://user/{user_id}/skills` is not accepted in v1.
- Legacy spellings `viking://user/resources[/...]` and `viking://user/skills` are still accepted here and normalized to the `viking://~/...` form (the server logs an info message). Everywhere else, the uid-less spelling is rejected at the request boundary — write new configs with `viking://~/...`.

#### 3. Usage Examples

**HTTP API**

```
POST /api/v1/admin/accounts
```

```bash
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{
    "account_id": "acme",
    "admin_user_id": "alice",
    "seed": "alice-seed"
  }'
```

**Trusted mode (registered gateway user)**

```bash
# First, register the gateway admin user in api_key mode
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{
    "account_id": "platform",
    "admin_user_id": "gateway-admin"
  }'

# Then use it in trusted mode; admin authorization comes from root_api_key
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -H "X-OpenViking-Account: platform" \
  -H "X-OpenViking-User: gateway-admin" \
  -d '{
    "account_id": "acme",
    "admin_user_id": "alice"
  }'
```

**Trusted mode (root fallback without identity headers)**

```bash
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{
    "account_id": "acme",
    "admin_user_id": "alice"
  }'
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-key>")
client.initialize()

result = client.admin_create_account(
    account_id="acme",
    admin_user_id="alice",
    seed="alice-seed",
)
print(f"Account created: {result['account_id']}")
print(f"Admin user: {result['admin_user_id']}")
print(f"User key: {result.get('user_key', '(not exposed in trusted mode)')}")

result = client.admin_create_account(
    account_id="acme-private",
    admin_user_id="alice",
    user_config={
        "add_targets": {
            "resource_uri": "viking://~/resources",
            "skill_uri": "viking://~/skills",
        }
    },
)
```

**TypeScript SDK**

```typescript
console.log(await client.adminCreateAccount("account-id", "admin-user-id"));
```

**Go SDK**

```go
result, err := client.AdminCreateAccount(ctx, "acme", "alice")
if err != nil {
    return err
}
fmt.Println(result["account_id"])

seed := "alice-seed"
result, err = client.AdminCreateAccountWithOptions(ctx, "acme-private", "alice", &openviking.AdminCreateAccountOptions{
    Seed: &seed,
    UserConfig: map[string]any{
        "add_targets": map[string]any{
            "resource_uri": "viking://~/resources",
            "skill_uri":    "viking://~/skills",
        },
    },
})
```

**CLI**

```bash
# Requires ROOT privileges, use --sudo
ov --sudo admin create-account acme --admin alice
ov --sudo admin create-account acme --admin alice --seed alice-seed

ov --sudo admin create-account acme-private --admin alice \
  --user-config-json '{"add_targets":{"resource_uri":"viking://~/resources","skill_uri":"viking://~/skills"}}'
```

**Response Example**

```json
{
  "status": "ok",
  "result": {
    "account_id": "acme",
    "admin_user_id": "alice",
    "user_key": "7f3a9c1e..."
  },
  "time": 0.1
}
```

In `trusted` mode, the same response omits `user_key`.

---

### list_accounts

#### 1. API Implementation Overview

List all workspaces (ROOT only).

**Processing Flow:**
1. Verify requester has ROOT privileges
2. Call API Key Manager to get all accounts (in creation order)
3. Apply optional `name` filter
4. Apply optional `limit`/`page` pagination
5. Return list with account ID, creation time, and user count

**Code Entry Points:**
- `openviking/server/routers/admin.py:list_accounts` - HTTP route
- `openviking/server/api_keys/new.py:APIKeyManager.get_accounts` - Core implementation
- `openviking_cli/client/sync_http.py:SyncHTTPClient.admin_list_accounts` - Python SDK

#### 2. Interface and Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| name | str | No | null | Filter by account ID (wildcard `*` and `?` matching) |
| limit | int | No | null | Page size (≥1). Omit to return all matches |
| page | int | No | 1 | 1-based page number; only applies when `limit` is set |

Results are returned in creation order.

#### 3. Usage Examples

**HTTP API**

```
GET /api/v1/admin/accounts
```

```bash
# List all accounts
curl -X GET http://localhost:1933/api/v1/admin/accounts \
  -H "X-API-Key: <root-key>"

# With filter (wildcard name matching)
curl -X GET "http://localhost:1933/api/v1/admin/accounts?name=*acme*" \
  -H "X-API-Key: <root-key>"

# Paginated (second page of 50)
curl -X GET "http://localhost:1933/api/v1/admin/accounts?limit=50&page=2" \
  -H "X-API-Key: <root-key>"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-key>")
client.initialize()

accounts = client.admin_list_accounts(name="*acme*", limit=50, page=1)
for account in accounts:
    print(f"Account: {account['account_id']}, created: {account['created_at']}, users: {account['user_count']}")
```

**TypeScript SDK**

```typescript
console.log(await client.adminListAccounts({ name: "*acme*", limit: 50, page: 1 }));
```

**Go SDK**

```go
accounts, err := client.AdminListAccounts(ctx)
if err != nil {
    return err
}
fmt.Println(accounts)
```

**CLI**

```bash
# Requires ROOT privileges, use --sudo
ov --sudo admin list-accounts

# Filter by wildcard name
ov --sudo admin list-accounts --name '*acme*'

# Paginated
ov --sudo admin list-accounts --limit 50 --page 2
```

**Response Example**

```json
{
  "status": "ok",
  "result": [
    {"account_id": "default", "created_at": "2026-02-12T10:00:00Z", "user_count": 1},
    {"account_id": "acme", "created_at": "2026-02-13T08:00:00Z", "user_count": 2}
  ],
  "time": 0.1
}
```

---

### delete_account

#### 1. API Implementation Overview

Asynchronously delete a workspace and all associated users and data (ROOT only). The endpoint returns HTTP `202` and a `task_id` without waiting for data cleanup.

**Processing Flow:**
1. Verify ROOT privileges and persist the account deletion fence, immediately rejecting its keys and ordinary requests; persist a system-owned `account_delete` Task and queued work, then return `status=deleting` and `task_id`
2. Stop account watches and business tasks, then remove vectors, OAuth grants, usage/audit data, and the entire account AGFS directory, including account-owned task records
3. Remove the account registry entry and complete the Task only after cleanup succeeds

Account and user cleanup share one queue with a single consumer. Account tasks clean the entire account directly. Later user cleanup tasks complete without further cleanup if their target was deleted. Old tasks also skip accounts or users recreated with the same IDs. Account-owned task records are deleted with the account and are not recreated by late deliveries; system-owned cleanup Tasks remain queryable.

**Code Entry Points:**
- `openviking/server/routers/admin.py:delete_account` - HTTP route
- `openviking/service/deletion.py:DeletionService.delete` - Core implementation
- `openviking_cli/client/sync_http.py:SyncHTTPClient.admin_delete_account` - Python SDK

#### 2. Interface and Parameters

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| account_id | str | Yes | - | Workspace ID to delete |

**Notes:**
- Delete operation is irreversible and cascades to all account data
- Cleanup failure marks the Task as `failed` and records the error; the account remains `deleting`
- Repeated requests during cleanup return the same Task; requesting deletion after failure creates a retry Task for remaining data
- Unfinished work is recovered on restart; the account cannot be recreated or re-enabled during cleanup
- Vector IDs are enumerated by account before deletion is submitted in batches of at most 100 records, without the former 100,000-record total ceiling
- Vector deletion succeeds when the delete requests succeed; it does not require an immediate zero count or empty read-back. Remote indexes may briefly return stale data even after the Task completes
- Account listings expose `status=active|deleting` and the cleanup `task_id` when deleting
- Query `GET /api/v1/tasks/{task_id}` as ROOT for status and errors; cleanup uses only `pending`, `running`, `completed`, and `failed`, without separate cleanup stages. Only `completed` confirms cleanup finished

#### 3. Usage Examples

**HTTP API**

```
DELETE /api/v1/admin/accounts/{account_id}
```

```bash
curl -X DELETE http://localhost:1933/api/v1/admin/accounts/acme \
  -H "X-API-Key: <root-key>"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-key>")
client.initialize()

result = client.admin_delete_account(account_id="acme")
print(f"Cleanup task: {result['task_id']}")
```

**TypeScript SDK**

```typescript
await client.adminDeleteAccount("account-id");
```

**Go SDK**

```go
result, err := client.AdminDeleteAccount(ctx, "acme")
if err != nil {
    return err
}
fmt.Println(result["task_id"])
```

**CLI**

```bash
# Requires ROOT privileges, use --sudo
ov --sudo admin delete-account acme
ov --sudo task status <task_id>
```

**Response Example**

```json
{
  "status": "ok",
  "result": {
    "account_id": "acme",
    "status": "deleting",
    "task_id": "550e8400-e29b-41d4-a716-446655440000"
  },
  "time": 0.1
}
```

---

### register_user

#### 1. API Implementation Overview

Register a new user in a workspace.

**Processing Flow:**
1. Verify requester has ROOT privileges or is an ADMIN of the account
2. Call API Key Manager to register new user
3. Initialize new user's personal directory
4. Write optional initial user config
5. Return user info and user key (not in trusted mode)

**Code Entry Points:**
- `openviking/server/routers/admin.py:register_user` - HTTP route
- `openviking/server/api_keys/new.py:APIKeyManager.register_user` - Core implementation
- `openviking_cli/client/sync_http.py:SyncHTTPClient.admin_register_user` - Python SDK

#### 2. Interface and Parameters

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| account_id | str | Yes | - | Workspace ID |
| user_id | str | Yes | - | User ID |
| role | str | No | "user" | Role to assign. `ROOT` and same-account `ADMIN` may register `"user"` or `"admin"`. ROOT identity comes only from `server.root_api_key`. |
| seed | str | No | `null` | Optional deterministic API key seed. When set, the key secret is `sha256(user_id + "\0" + seed)` |
| user_config | object | No | `null` | Initial config for the new user. Supports `add_targets.resource_uri`, `add_targets.skill_uri`, and `memory_policy` |

**Notes:**
- In `trusted` mode, `user_key` is omitted from the response
- Omit `seed` for the default random API key. Treat seed values as secret material; short seeds can make the key guessable.
- ADMIN can only register users in their own account
- The `"root"` role cannot be minted through user registration
- `user_config.add_targets.resource_uri` must be a writable resource directory URI: `viking://resources` or `viking://resources/...`, `viking://~/resources` or `viking://~/resources/...`, `viking://user/{user_id}/resources` or `viking://user/{user_id}/resources/...`, or `viking://user/{user_id}/peers/{peer_id}/resources` or `viking://user/{user_id}/peers/{peer_id}/resources/...`.
- `user_config.add_targets.skill_uri` must be `viking://~/skills` or `viking://agent/skills`. Explicit `viking://user/{user_id}/skills` is not accepted in v1.
- Legacy spellings `viking://user/resources[/...]` and `viking://user/skills` are still accepted here and normalized to the `viking://~/...` form (the server logs an info message). Everywhere else, the uid-less spelling is rejected at the request boundary — write new configs with `viking://~/...`.

#### 3. Usage Examples

**HTTP API**

```
POST /api/v1/admin/accounts/{account_id}/users
```

```bash
curl -X POST http://localhost:1933/api/v1/admin/accounts/acme/users \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-or-admin-key>" \
  -d '{
    "user_id": "bob",
    "role": "user",
    "seed": "bob-seed"
  }'
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-or-admin-key>")
client.initialize()

result = client.admin_register_user(
    account_id="acme",
    user_id="bob",
    role="user",
    seed="bob-seed",
)
print(f"User registered: {result['user_id']}")
print(f"User key: {result.get('user_key', '(not exposed in trusted mode)')}")

result = client.admin_register_user(
    account_id="acme",
    user_id="bob-private",
    role="user",
    user_config={"add_targets": {"resource_uri": "viking://~/resources/project-a"}},
)
```

**TypeScript SDK**

```typescript
console.log(await client.adminRegisterUser("account-id", "user-id", "user"));
```

**Go SDK**

```go
result, err := client.AdminRegisterUser(ctx, "acme", "bob", "user")
if err != nil {
    return err
}
fmt.Println(result["user_id"])

seed := "bob-seed"
result, err = client.AdminRegisterUserWithOptions(ctx, "acme", "bob-private", "user", &openviking.AdminRegisterUserOptions{
    Seed: &seed,
    UserConfig: map[string]any{
        "add_targets": map[string]any{"resource_uri": "viking://~/resources/project-a"},
    },
})
```

**CLI**

```bash
# Either ROOT or account ADMIN can execute
# If using regular user's api_key who is an ADMIN of acme:
ov admin register-user acme bob --role user
ov admin register-user acme bob --role user --seed bob-seed
# If using root_api_key (--sudo):
ov --sudo admin register-user acme bob --role user

ov admin register-user acme bob-private --role user \
  --user-config-json '{"add_targets":{"resource_uri":"viking://~/resources/project-a"}}'
```

**Response Example**

```json
{
  "status": "ok",
  "result": {
    "account_id": "acme",
    "user_id": "bob",
    "user_key": "d91f5b2a..."
  },
  "time": 0.1
}
```

---

### list_users

#### 1. API Implementation Overview

List active users in a workspace. Users with deletion in progress are omitted.

**Processing Flow:**
1. Verify requester has ROOT privileges or is an ADMIN of the account
2. Call API Key Manager to get active users list (in creation order)
3. Apply optional filters (name, role)
4. Apply optional `limit`/`page` pagination
5. Return users list (trusted mode omits user_key)

**Code Entry Points:**
- `openviking/server/routers/admin.py:list_users` - HTTP route
- `openviking/server/api_keys/new.py:APIKeyManager.get_users` - Core implementation
- `openviking_cli/client/sync_http.py:SyncHTTPClient.admin_list_users` - Python SDK

#### 2. Interface and Parameters

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| account_id | str | Yes | - | Workspace ID |
| name | str | No | null | Filter by user ID (wildcard `*` and `?` matching) |
| role | str | No | null | Filter by role |
| include_credentials | bool | No | true | HTTP-only. Set false to return `user_id`, `role`, and `api_key_available` without credentials or key prefixes. The default preserves the existing mode-dependent response. |
| limit | int | No | null | Page size (≥1). Omit to return all matches |
| page | int | No | 1 | 1-based page number; only applies when `limit` is set |

**Notes:**
- Results are returned in creation order
- ADMIN can only list users in their own account
- In `trusted` mode, `user_key` is omitted from the response
- Users whose deletion has started are no longer returned

**Summary responses (HTTP):** Set `include_summary=true` to return an object in `result` with `users` (the current page), `total` (matching users), `account_total`, `manager_count` (admin/root), and `key_count` (users with a visible key or prefix). Account statistics ignore search/role filters and exclude deleting users; `key_count` is zero when key exposure is disabled. The default remains a user array for existing callers.

`query` performs a trimmed, case-insensitive literal substring match on user IDs. It combines with the existing `name` wildcard and `role` filters. For example:

```text
GET /api/v1/admin/accounts/acme/users?limit=20&page=1&query=alice&include_summary=true
```

#### 3. Usage Examples

**HTTP API**

```
GET /api/v1/admin/accounts/{account_id}/users
```

```bash
# List all users
curl -X GET http://localhost:1933/api/v1/admin/accounts/acme/users \
  -H "X-API-Key: <root-or-admin-key>"

# With filters (wildcard name matching)
curl -X GET "http://localhost:1933/api/v1/admin/accounts/acme/users?name=*ali*&role=admin" \
  -H "X-API-Key: <root-or-admin-key>"

# Paginated (second page of 50)
curl -X GET "http://localhost:1933/api/v1/admin/accounts/acme/users?limit=50&page=2" \
  -H "X-API-Key: <root-or-admin-key>"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-or-admin-key>")
client.initialize()

users = client.admin_list_users(account_id="acme", name="*ali*", limit=50, page=1)
for user in users:
    print(f"User: {user['user_id']}, role: {user['role']}")
```

**TypeScript SDK**

```typescript
console.log(await client.adminListUsers("account-id", { name: "*ali*", limit: 50, page: 1 }));
```

**Go SDK**

```go
users, err := client.AdminListUsers(ctx, "acme")
if err != nil {
    return err
}
fmt.Println(users)
```

**CLI**

```bash
# Either ROOT or account ADMIN can execute
# If using regular user's api_key who is an ADMIN of acme:
ov admin list-users acme
# If using root_api_key (--sudo):
ov --sudo admin list-users acme
# Filter by wildcard name
ov admin list-users acme --name '*ali*'
# Paginated
ov admin list-users acme --limit 50 --page 2
```

**Response Example**

```json
{
  "status": "ok",
  "result": [
    {"user_id": "alice", "role": "admin"},
    {"user_id": "bob", "role": "user"}
  ],
  "time": 0.1
}
```


---

### remove_user

#### 1. API Implementation Overview

Remove a user from a workspace. The user's API key is revoked immediately, and owned data cleanup runs asynchronously.

**Processing Flow:**
1. Verify requester has ROOT privileges or is an ADMIN of the account
2. Write a deletion fence and revoke the user's API key
3. Enqueue a durable cleanup task for the user's owned data
4. Return the deletion task ID

**Code Entry Points:**
- `openviking/server/routers/admin.py:remove_user` - HTTP route
- `openviking/service/deletion.py:DeletionService.delete` - Core implementation
- `openviking_cli/client/sync_http.py:SyncHTTPClient.admin_remove_user` - Python SDK

#### 2. Interface and Parameters

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| account_id | str | Yes | - | Workspace ID |
| user_id | str | Yes | - | User ID to remove |

**Notes:**
- ADMIN can only remove users in their own account
- Cannot delete the last admin user of an account
- After deletion starts, the user key is invalid and list_users omits the user
- Vector deletion succeeds when the delete requests succeed, without waiting for remote index synchronization; counts and queries may briefly lag after the Task completes

#### 3. Usage Examples

**HTTP API**

```
DELETE /api/v1/admin/accounts/{account_id}/users/{user_id}
```

```bash
curl -X DELETE http://localhost:1933/api/v1/admin/accounts/acme/users/bob \
  -H "X-API-Key: <root-or-admin-key>"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-or-admin-key>")
client.initialize()

result = client.admin_remove_user("acme", "bob")
print(f"User deletion task: {result['task_id']}")
```

**TypeScript SDK**

```typescript
await client.adminRemoveUser("account-id", "user-id");
```

**Go SDK**

```go
result, err := client.AdminRemoveUser(ctx, "acme", "bob")
if err != nil {
    return err
}
fmt.Println(result["task_id"])
```

**CLI**

```bash
# Either ROOT or account ADMIN can execute
# If using regular user's api_key who is an ADMIN of acme:
ov admin remove-user acme bob
# If using root_api_key (--sudo):
ov --sudo admin remove-user acme bob
```

**Response Example**

```json
{
  "status": "ok",
  "result": {
    "account_id": "acme",
    "user_id": "bob",
    "status": "deleting",
    "task_id": "..."
  },
  "time": 0.1
}
```

---

### set_role

#### 1. API Implementation Overview

Promote an account user to ADMIN. ROOT may operate on any account; ADMIN is limited to its own account.

**Processing Flow:**
1. Verify the requester has ROOT or ADMIN privileges and keep ADMIN within its own account
2. Call API Key Manager to update user role
3. Return updated user info

**Code Entry Points:**
- `openviking/server/routers/admin.py:set_user_role` - HTTP route
- `openviking/server/api_keys/new.py:APIKeyManager.set_role` - Core implementation
- `openviking_cli/client/sync_http.py:SyncHTTPClient.admin_set_role` - Python SDK

#### 2. Interface and Parameters

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| account_id | str | Yes | - | Workspace ID |
| user_id | str | Yes | - | User ID |
| role | str | Yes | - | Must be "admin" |

**Notes:**
- ROOT and ADMIN can promote users to ADMIN; ADMIN is limited to its own account
- This endpoint cannot set "user" or "root"; ROOT comes only from `server.root_api_key`

#### 3. Usage Examples

**HTTP API**

```
PUT /api/v1/admin/accounts/{account_id}/users/{user_id}/role
```

```bash
curl -X PUT http://localhost:1933/api/v1/admin/accounts/acme/users/bob/role \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{"role": "admin"}'
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-key>")
client.initialize()

result = client.admin_set_role(account_id="acme", user_id="bob", role="admin")
print(f"User: {result['user_id']}, new role: {result['role']}")
```

**TypeScript SDK**

```typescript
await client.adminSetRole("account-id", "user-id", "admin");
```

**Go SDK**

```go
result, err := client.AdminSetRole(ctx, "acme", "bob", "admin")
if err != nil {
    return err
}
fmt.Println(result["role"])
```

**CLI**

```bash
# Requires ROOT privileges, use --sudo
ov --sudo admin set-role acme bob admin
```

**Response Example**

```json
{
  "status": "ok",
  "result": {
    "account_id": "acme",
    "user_id": "bob",
    "role": "admin"
  },
  "time": 0.1
}
```

---

### regenerate_key

#### 1. API Implementation Overview

Regenerate a user's API key. The old key is immediately invalidated.

**Processing Flow:**
1. Verify requester has ROOT privileges or is an ADMIN of the account
2. Call API Key Manager to regenerate user key
3. Old key is immediately invalidated
4. Return new user key

**Code Entry Points:**
- `openviking/server/routers/admin.py:regenerate_key` - HTTP route
- `openviking/server/api_keys/new.py:APIKeyManager.regenerate_key` - Core implementation
- `openviking_cli/client/sync_http.py:SyncHTTPClient.admin_regenerate_key` - Python SDK

#### 2. Interface and Parameters

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| account_id | str | Yes | - | Workspace ID |
| user_id | str | Yes | - | User ID |
| seed | str | No | `null` | Optional deterministic API key seed in the JSON request body. When set, the key secret is `sha256(user_id + "\0" + seed)` |

**Notes:**
- ADMIN can only regenerate keys for users in their own account
- Old key is immediately invalidated, clients using it need to be updated
- Omit `seed` for the default random regenerated key.

#### 3. Usage Examples

**HTTP API**

```
POST /api/v1/admin/accounts/{account_id}/users/{user_id}/key
```

```bash
curl -X POST http://localhost:1933/api/v1/admin/accounts/acme/users/bob/key \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-or-admin-key>" \
  -d '{"seed": "bob-new-seed"}'
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-or-admin-key>")
client.initialize()

result = client.admin_regenerate_key(
    account_id="acme",
    user_id="bob",
    seed="bob-new-seed",
)
print(f"New user key: {result['user_key']}")
```

**TypeScript SDK**

```typescript
console.log(await client.adminRegenerateKey("account-id", "user-id"));
```

**Go SDK**

```go
result, err := client.AdminRegenerateKey(ctx, "acme", "bob")
if err != nil {
    return err
}
fmt.Println(result["user_key"])

seed := "bob-new-seed"
result, err = client.AdminRegenerateKeyWithOptions(ctx, "acme", "bob", &openviking.AdminRegenerateKeyOptions{
    Seed: &seed,
})
```

**CLI**

```bash
# Either ROOT or account ADMIN can execute
# If using regular user's api_key who is an ADMIN of acme:
ov admin regenerate-key acme bob
ov admin regenerate-key acme bob --seed bob-new-seed
# If using root_api_key (--sudo):
ov --sudo admin regenerate-key acme bob
```

**Response Example**

```json
{
  "status": "ok",
  "result": {
    "user_key": "e82d4e0f..."
  },
  "time": 0.1
}
```

---

### migrate_legacy_data

#### 1. API Implementation Overview

Migrate legacy `viking://session/...` data into `viking://user/<user_id>/sessions/...`, or clean up old session directories after verifying migration. This endpoint is ROOT-only and runs as a background task. The account-shared `agent` directory is excluded from migration and cleanup.

**Processing Flow:**
1. Verify requester has ROOT privileges
2. For `action=migrate`, run preflight checks for account registry, session owner metadata, and other prerequisites
3. Create a root-level background task
4. During migration, copy session files; during cleanup, delete old session vector records before deleting old session AGFS directories

Migration preserves files that already exist at the destination. Cleanup leaves shared `agent` directories and migrated user data intact.

**Code Entry Points:**
- `openviking/server/routers/admin.py:migrate_legacy_data` - HTTP route
- `openviking/service/legacy_migration.py:LegacyDataMigration` - Migration implementation

#### 2. Interface and Parameters

**HTTP API**

```
POST /api/v1/admin/migrate
```

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| action | str | No | migrate | `migrate` runs migration; `cleanup` removes old namespaces |

**Migration result fields**

| Field | Description |
|-------|-------------|
| migrated.files / migrated.directories | Number of files and directories copied |
| migrated.operations | Session migration operation count (`sessions`) |
| skipped / created_users | Skipped files and users created automatically |

**Cleanup result fields**

| Field | Description |
|-------|-------------|
| cleanup.directories | Number of legacy directories deleted |
| cleanup.vector_records | Number of old vector records deleted |
| cleanup.targets | Legacy scopes that were cleaned |
| skipped / warnings | Skipped items and warnings |

#### 3. Usage Examples

**HTTP API**

```bash
# Run migration
curl -X POST http://localhost:1933/api/v1/admin/migrate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{"action": "migrate"}'

# Clean old namespaces
curl -X POST http://localhost:1933/api/v1/admin/migrate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{"action": "cleanup"}'
```

**Python SDK**

```python
print(client.admin_migrate(cleanup=False))
```

**TypeScript SDK**

```typescript
console.log(await client.adminMigrate(false));
```

**Go SDK**

```go
result, err := client.AdminMigrate(ctx, &openviking.AdminMigrateOptions{
    Cleanup: false,
})
if err != nil {
    return err
}
fmt.Println(result["task_id"])
```

**CLI**

```bash
ov --sudo admin migrate --output json
ov --sudo admin migrate --cleanup --output json
```

**Response Example**

```json
{
  "task_id": "legacy_migration_..."
}
```

---

<a id="user-add-location-settings"></a>

## Full Example

### Typical Admin Workflow

```bash
# Step 1: ROOT creates workspace with alice as first admin (requires --sudo)
ov --sudo admin create-account acme --admin alice
# Returns alice's user_key

# Step 2: alice (admin) registers regular user bob
# Configure api_key in config file to alice's user_key, no --sudo needed
ov admin register-user acme bob --role user
# Returns bob's user_key

# Step 3: List all users in the account
ov admin list-users acme

# Step 4: ROOT promotes bob to admin (requires --sudo)
ov --sudo admin set-role acme bob admin

# Step 5: bob lost their key, regenerate (old key immediately invalidated)
# alice as admin can do this, no --sudo needed
ov admin regenerate-key acme bob

# Step 6: Remove user
ov admin remove-user acme bob

# Step 7: Delete entire workspace (requires --sudo)
ov --sudo admin delete-account acme
```

### HTTP API Equivalent

```bash
# Step 1: Create workspace
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{"account_id": "acme", "admin_user_id": "alice"}'

# Step 2: Register user (using alice's admin key)
curl -X POST http://localhost:1933/api/v1/admin/accounts/acme/users \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <alice-key>" \
  -d '{"user_id": "bob", "role": "user"}'

# Step 3: List users
curl -X GET http://localhost:1933/api/v1/admin/accounts/acme/users \
  -H "X-API-Key: <alice-key>"

# Step 4: Promote the user to admin
curl -X PUT http://localhost:1933/api/v1/admin/accounts/acme/users/bob/role \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <alice-key>" \
  -d '{"role": "admin"}'

# Step 5: Regenerate key
curl -X POST http://localhost:1933/api/v1/admin/accounts/acme/users/bob/key \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <alice-key>"

# Step 6: Remove user
curl -X DELETE http://localhost:1933/api/v1/admin/accounts/acme/users/bob \
  -H "X-API-Key: <alice-key>"

# Step 7: Delete workspace
curl -X DELETE http://localhost:1933/api/v1/admin/accounts/acme \
  -H "X-API-Key: <root-key>"
```

---

## Related Documentation

- [Multi-Tenant](../concepts/11-multi-tenant.md) - Tenant model, roles, and sharing boundaries
- [API Overview](01-overview.md) - Authentication and response format
- [Sessions](05-sessions.md) - Session management
- [System](07-system.md) - System and monitoring API
