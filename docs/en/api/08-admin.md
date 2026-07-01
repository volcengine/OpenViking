# Admin (Multi-tenant)

The Admin API manages accounts and users in a multi-tenant environment. It covers workspace (account) creation/deletion, user registration/removal, role changes, and API key regeneration.

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
| List agents (deprecated, returns empty list) | Y | Y (own account) | N |
| Regenerate user key | Y | Y (own account) | N |
| Change user role | Y | N | N |

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

## API Reference

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
| user_config | object | No | `null` | Initial config for the first admin user. Currently supports `add_targets.resource_uri` and `add_targets.skill_uri` |

**Notes:**
- In `trusted` mode, `user_key` is omitted from the response
- Omit `seed` for the default random API key. Treat seed values as secret material; short seeds can make the key guessable.
- Account-level namespace isolation settings are no longer supported. User memory uses user-scoped namespaces, and one-to-many external participants are represented with `peer_id`.
- `user_config.add_targets.resource_uri` must be a writable resource directory URI: `viking://resources` or `viking://resources/...`, `viking://user/resources` or `viking://user/resources/...`, `viking://user/{user_id}/resources` or `viking://user/{user_id}/resources/...`, or `viking://user/{user_id}/peers/{peer_id}/resources` or `viking://user/{user_id}/peers/{peer_id}/resources/...`.
- `user_config.add_targets.skill_uri` must be `viking://user/skills` or `viking://agent/skills`. Explicit `viking://user/{user_id}/skills` is not accepted in v1.

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

# Then promote it to root for cross-account admin operations
curl -X PUT http://localhost:1933/api/v1/admin/accounts/platform/users/gateway-admin/role \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{"role": "root"}'

# Then use in trusted mode
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

result = client.admin_create_account("acme", "alice", seed="alice-seed")
print(f"Account created: {result['account_id']}")
print(f"Admin user: {result['admin_user_id']}")
print(f"User key: {result.get('user_key', '(not exposed in trusted mode)')}")

result = client.admin_create_account(
    "acme-private",
    "alice",
    user_config={
        "add_targets": {
            "resource_uri": "viking://user/resources",
            "skill_uri": "viking://user/skills",
        }
    },
)
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
            "resource_uri": "viking://user/resources",
            "skill_uri":    "viking://user/skills",
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
  --user-config-json '{"add_targets":{"resource_uri":"viking://user/resources","skill_uri":"viking://user/skills"}}'
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
2. Call API Key Manager to get all accounts
3. Return list with account ID, creation time, and user count

**Code Entry Points:**
- `openviking/server/routers/admin.py:list_accounts` - HTTP route
- `openviking/server/api_keys/new.py:APIKeyManager.get_accounts` - Core implementation
- `openviking_cli/client/sync_http.py:SyncHTTPClient.admin_list_accounts` - Python SDK

#### 2. Interface and Parameters

No parameters.

#### 3. Usage Examples

**HTTP API**

```
GET /api/v1/admin/accounts
```

```bash
curl -X GET http://localhost:1933/api/v1/admin/accounts \
  -H "X-API-Key: <root-key>"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-key>")
client.initialize()

accounts = client.admin_list_accounts()
for account in accounts:
    print(f"Account: {account['account_id']}, created: {account['created_at']}, users: {account['user_count']}")
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

Delete a workspace and all associated users and data (ROOT only).

**Processing Flow:**
1. Verify requester has ROOT privileges
2. Cascade delete all AGFS data for the account (`user/` and `resources/`; sessions live under `user/`)
3. Cascade delete all vector DB records for the account
4. Finally delete account metadata and all user keys

**Code Entry Points:**
- `openviking/server/routers/admin.py:delete_account` - HTTP route
- `openviking/server/api_keys/new.py:APIKeyManager.delete_account` - Core implementation
- `openviking_cli/client/sync_http.py:SyncHTTPClient.admin_delete_account` - Python SDK

#### 2. Interface and Parameters

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| account_id | str | Yes | - | Workspace ID to delete |

**Notes:**
- Delete operation is irreversible and cascades to all account data
- If some data fails to delete, warnings are logged and deletion continues

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

result = client.admin_delete_account("acme")
print(f"Account deleted: {result['deleted']}")
```

**Go SDK**

```go
result, err := client.AdminDeleteAccount(ctx, "acme")
if err != nil {
    return err
}
fmt.Println(result["deleted"])
```

**CLI**

```bash
# Requires ROOT privileges, use --sudo
ov --sudo admin delete-account acme
```

**Response Example**

```json
{
  "status": "ok",
  "result": {
    "deleted": true
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
| role | str | No | "user" | Role to assign. `ROOT` and same-account `ADMIN` may register `"user"` or `"admin"`. `"root"` must be assigned through the dedicated role-change endpoint. |
| seed | str | No | `null` | Optional deterministic API key seed. When set, the key secret is `sha256(user_id + "\0" + seed)` |
| user_config | object | No | `null` | Initial config for the new user. Currently supports `add_targets.resource_uri` and `add_targets.skill_uri` |

**Notes:**
- In `trusted` mode, `user_key` is omitted from the response
- Omit `seed` for the default random API key. Treat seed values as secret material; short seeds can make the key guessable.
- ADMIN can only register users in their own account
- The `"root"` role cannot be minted through user registration
- `user_config.add_targets.resource_uri` must be a writable resource directory URI: `viking://resources` or `viking://resources/...`, `viking://user/resources` or `viking://user/resources/...`, `viking://user/{user_id}/resources` or `viking://user/{user_id}/resources/...`, or `viking://user/{user_id}/peers/{peer_id}/resources` or `viking://user/{user_id}/peers/{peer_id}/resources/...`.
- `user_config.add_targets.skill_uri` must be `viking://user/skills` or `viking://agent/skills`. Explicit `viking://user/{user_id}/skills` is not accepted in v1.

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

result = client.admin_register_user("acme", "bob", role="user", seed="bob-seed")
print(f"User registered: {result['user_id']}")
print(f"User key: {result.get('user_key', '(not exposed in trusted mode)')}")

result = client.admin_register_user(
    "acme",
    "bob-private",
    role="user",
    user_config={"add_targets": {"resource_uri": "viking://user/resources/project-a"}},
)
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
        "add_targets": map[string]any{"resource_uri": "viking://user/resources/project-a"},
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
  --user-config-json '{"add_targets":{"resource_uri":"viking://user/resources/project-a"}}'
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

List all users in a workspace.

**Processing Flow:**
1. Verify requester has ROOT privileges or is an ADMIN of the account
2. Call API Key Manager to get users list
3. Apply optional filters (name, role) and pagination limit
4. Return users list (trusted mode omits user_key)

**Code Entry Points:**
- `openviking/server/routers/admin.py:list_users` - HTTP route
- `openviking/server/api_keys/new.py:APIKeyManager.get_users` - Core implementation
- `openviking_cli/client/sync_http.py:SyncHTTPClient.admin_list_users` - Python SDK

#### 2. Interface and Parameters

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| account_id | str | Yes | - | Workspace ID |
| limit | int | No | 100 | Maximum number of users to return |
| name | str | No | null | Filter by user ID (prefix match) |
| role | str | No | null | Filter by role |

**Notes:**
- ADMIN can only list users in their own account
- In `trusted` mode, `user_key` is omitted from the response

#### 3. Usage Examples

**HTTP API**

```
GET /api/v1/admin/accounts/{account_id}/users
```

```bash
# List all users
curl -X GET http://localhost:1933/api/v1/admin/accounts/acme/users \
  -H "X-API-Key: <root-or-admin-key>"

# With filters
curl -X GET "http://localhost:1933/api/v1/admin/accounts/acme/users?role=admin&limit=50" \
  -H "X-API-Key: <root-or-admin-key>"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-or-admin-key>")
client.initialize()

users = client.admin_list_users("acme")
for user in users:
    print(f"User: {user['user_id']}, role: {user['role']}")
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

Remove a user from a workspace. The user's API key is deleted immediately.

**Processing Flow:**
1. Verify requester has ROOT privileges or is an ADMIN of the account
2. Call API Key Manager to delete user and their API key
3. Return deletion confirmation

**Code Entry Points:**
- `openviking/server/routers/admin.py:remove_user` - HTTP route
- `openviking/server/api_keys/new.py:APIKeyManager.remove_user` - Core implementation
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
print(f"User deleted: {result['deleted']}")
```

**Go SDK**

```go
result, err := client.AdminRemoveUser(ctx, "acme", "bob")
if err != nil {
    return err
}
fmt.Println(result["deleted"])
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
    "deleted": true
  },
  "time": 0.1
}
```

---

### set_role

#### 1. API Implementation Overview

Change a user's role (ROOT only).

**Processing Flow:**
1. Verify requester has ROOT privileges
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
| role | str | Yes | - | New role: "admin", "user", or "root" |

**Notes:**
- Only ROOT can change user roles
- Role can be set to "admin", "user", or "root"

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

result = client.admin_set_role("acme", "bob", "admin")
print(f"User: {result['user_id']}, new role: {result['role']}")
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

result = client.admin_regenerate_key("acme", "bob", seed="bob-new-seed")
print(f"New user key: {result['user_key']}")
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

Migrate 0.3.x legacy `viking://agent/...` / `viking://session/...` data into the 0.4.0 user / peer namespace, or clean up old namespaces after migration has been verified. This endpoint is ROOT-only and runs as a background task.

**Processing Flow:**
1. Verify requester has ROOT privileges
2. For `action=migrate`, run preflight checks for account registry, session owner metadata, and other prerequisites
3. Create a root-level background task
4. During migration, copy files and existing vector records; during cleanup, delete old vector records before deleting old AGFS directories

Migration does not automatically call `reindex`. If retrieval after migration is not as expected, users should manually reindex the new paths.

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
| migrated.vector_records | Number of existing vector records copied |
| migrated.skipped_vector_records | Number of old records skipped because they had no vector payload |
| migrated.operations | Operation counts grouped by migration category |
| skipped / warnings / created_users | Skipped items, warnings, and users created automatically |

**Cleanup result fields**

| Field | Description |
|-------|-------------|
| cleanup.directories | Number of legacy directories deleted |
| cleanup.vector_records | Number of old vector records deleted |
| cleanup.targets | Legacy scopes that were cleaned |
| skipped / warnings | Skipped items and warnings |

#### 3. Usage Examples

**Run migration**

```bash
curl -X POST http://localhost:1933/api/v1/admin/migrate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{"action": "migrate"}'
```

**Clean old namespaces**

```bash
curl -X POST http://localhost:1933/api/v1/admin/migrate \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
  -d '{"action": "cleanup"}'
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

### user add-location settings

Per-user add defaults are stored as user config under
`viking://user/{user_id}/settings/user_config.json`. They affect add calls that
omit explicit targets:

1. `add_resource`: explicit `to` / `parent` wins, then user
   `add_targets.resource_uri`, then
   `server.user_config_defaults.add_targets.resource_uri`, then legacy behavior.
2. `add_skill`: explicit `target_uri` wins, then user
   `add_targets.skill_uri`, then
   `server.user_config_defaults.add_targets.skill_uri`, then legacy behavior.

#### HTTP API

```
GET /api/v1/user-settings/add-locations
PATCH /api/v1/user-settings/add-locations
DELETE /api/v1/user-settings/add-locations
```

`PATCH` accepts the add-target fields directly. Passing `null` clears one field;
deleting the settings clears the whole per-user override.

```bash
curl -X PATCH http://localhost:1933/api/v1/user-settings/add-locations \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <user-key>" \
  -d '{"resource_uri": "viking://user/resources/project-a"}'
```

`resource_uri` must be a writable resource directory URI:
`viking://resources` or `viking://resources/...`,
`viking://user/resources` or `viking://user/resources/...`,
`viking://user/{user_id}/resources` or
`viking://user/{user_id}/resources/...`, or
`viking://user/{user_id}/peers/{peer_id}/resources` or
`viking://user/{user_id}/peers/{peer_id}/resources/...`. `skill_uri` must be
`viking://user/skills` or `viking://agent/skills`; explicit
`viking://user/{user_id}/skills` is not accepted in v1. Invalid configured
values are rejected; OpenViking does not silently fall back to another target.

---

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

# Step 4: Change role (requires ROOT key)
curl -X PUT http://localhost:1933/api/v1/admin/accounts/acme/users/bob/role \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-key>" \
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
