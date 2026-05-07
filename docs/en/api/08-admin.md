# Admin (Multi-tenant)

The Admin API manages accounts and users in a multi-tenant environment. It covers workspace (account) creation/deletion, user registration/removal, role changes, and API key regeneration.

This API is available in both `api_key` and `trusted` deployments:
- In `api_key` mode, the effective role is always derived from the presented API key.
- In `trusted` mode, ordinary requests still do not use user-key registration, but a trusted gateway may call Admin API using a registered user with appropriate role (role is looked up from user registry).

In `trusted` mode, role is determined by looking up `X-OpenViking-Account` + `X-OpenViking-User` from the user registry. If the user doesn't exist, role defaults to `USER`.
For `/api/v1/admin/*`, trusted mode also permits requests with no explicit identity headers; those requests are treated as ROOT and are intended for trusted upstreams authenticated by the deployment's `root_api_key`.

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
| List agent namespaces | Y | Y (own account) | N |
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

### Usage Limitations

- `--sudo` only works with admin commands - using it with regular data commands will error
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
5. Return account info and user key (not in trusted mode)

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
| isolate_user_scope_by_agent | bool | No | false | Further isolate user scope by agent |
| isolate_agent_scope_by_user | bool | No | false | Further isolate agent scope by user |

**Notes:**
- In `trusted` mode, `user_key` is omitted from the response
- `isolate_user_scope_by_agent` and `isolate_agent_scope_by_user` are only available via HTTP API, not in Python SDK or CLI

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
    "isolate_user_scope_by_agent": true,
    "isolate_agent_scope_by_user": false
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
    "admin_user_id": "alice",
    "isolate_user_scope_by_agent": true,
    "isolate_agent_scope_by_user": false
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

result = client.admin_create_account("acme", "alice")
print(f"Account created: {result['account_id']}")
print(f"Admin user: {result['admin_user_id']}")
print(f"User key: {result.get('user_key', '(not exposed in trusted mode)')}")
```

**CLI**

```bash
# Requires ROOT privileges, use --sudo
ov --sudo admin create-account acme --admin alice
```

**Response Example**

```json
{
  "status": "ok",
  "result": {
    "account_id": "acme",
    "admin_user_id": "alice",
    "user_key": "7f3a9c1e...",
    "isolate_user_scope_by_agent": true,
    "isolate_agent_scope_by_user": false
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
2. Cascade delete all AGFS data for the account (user/, agent/, session/, resources/)
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
4. Return user info and user key (not in trusted mode)

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

**Notes:**
- In `trusted` mode, `user_key` is omitted from the response
- ADMIN can only register users in their own account
- The `"root"` role cannot be minted through user registration

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
    "role": "user"
  }'
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-or-admin-key>")
client.initialize()

result = client.admin_register_user("acme", "bob", role="user")
print(f"User registered: {result['user_id']}")
print(f"User key: {result.get('user_key', '(not exposed in trusted mode)')}")
```

**CLI**

```bash
# Either ROOT or account ADMIN can execute
# If using regular user's api_key who is an ADMIN of acme:
ov admin register-user acme bob --role user
# If using root_api_key (--sudo):
ov --sudo admin register-user acme bob --role user
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

### list_agents

#### 1. API Implementation Overview

List agent namespaces that exist under a workspace. This is an admin discovery API; it does not change normal `viking://agent/...` filesystem semantics.

**Processing Flow:**
1. Verify requester has ROOT privileges or is an ADMIN of the account
2. Verify the account exists
3. Scan the account's `viking://agent` namespace root
4. Return sorted agent namespace entries

**Code Entry Points:**
- `openviking/server/routers/admin.py:list_agents` - HTTP route
- `crates/ov_cli/src/client.rs:HttpClient.admin_list_agents` - CLI HTTP client
- `crates/ov_cli/src/commands/admin.rs:list_agents` - CLI command

#### 2. Interface and Parameters

**Parameters**

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| account_id | str | Yes | - | Workspace ID |

**Notes:**
- ROOT can list agents in any account
- ADMIN can only list agents in their own account
- USER cannot call this API
- The result lists agent namespaces that exist in storage. A new account includes the initialized `default` agent namespace.

#### 3. Usage Examples

**HTTP API**

```
GET /api/v1/admin/accounts/{account_id}/agents
```

```bash
curl -X GET http://localhost:1933/api/v1/admin/accounts/acme/agents \
  -H "X-API-Key: <root-or-admin-key>"
```

**CLI**

```bash
# Either ROOT or account ADMIN can execute
# If using regular user's api_key who is an ADMIN of acme:
ov admin list-agents acme
# If using root_api_key (--sudo):
ov --sudo admin list-agents acme
```

**Response Example**

```json
{
  "status": "ok",
  "result": [
    {"agent_id": "default", "uri": "viking://agent/default"},
    {"agent_id": "openclaw", "uri": "viking://agent/openclaw"}
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

**Notes:**
- ADMIN can only regenerate keys for users in their own account
- Old key is immediately invalidated, clients using it need to be updated

#### 3. Usage Examples

**HTTP API**

```
POST /api/v1/admin/accounts/{account_id}/users/{user_id}/key
```

```bash
curl -X POST http://localhost:1933/api/v1/admin/accounts/acme/users/bob/key \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <root-or-admin-key>"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(api_key="<root-or-admin-key>")
client.initialize()

result = client.admin_regenerate_key("acme", "bob")
print(f"New user key: {result['user_key']}")
```

**CLI**

```bash
# Either ROOT or account ADMIN can execute
# If using regular user's api_key who is an ADMIN of acme:
ov admin regenerate-key acme bob
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
