# Authentication

OpenViking Server supports three built-in authentication modes with role-based access control: `api_key`, `trusted`, and `dev`. The mode is auto-detected if not explicitly configured. In addition, custom authentication plugins can be registered to support arbitrary identity sources (e.g., LDAP, OIDC, mTLS).

## Overview

OpenViking uses a two-layer API key system:

| Key Type | Created By | Role | Purpose |
|----------|-----------|------|---------|
| Root Key | Server config (`root_api_key`) | ROOT | Account management + selected system/monitoring operations |
| User Key | Admin API | ADMIN or USER | Per-account data access; ADMIN can also manage users in its account |

All API keys are plain random tokens with no embedded identity. The server resolves identity by first comparing against the root key, then looking up the user key index.

## Authentication Modes

| Mode | `server.auth_mode` | Identity Source | Typical Use |
|------|--------------------|-----------------|-------------|
| API key mode | `"api_key"` | API key. Data ownership is resolved from the user/admin key. | Standard multi-tenant deployment |
| Trusted mode | `"trusted"` | `X-OpenViking-Account` / `X-OpenViking-User`, plus `root_api_key` on non-localhost deployments. Role is looked up from APIKeyManager if the user exists. | Behind a trusted gateway or internal network boundary |
| Dev mode | `"dev"` | No authentication, always ROOT | Local development only |

If `auth_mode` is not explicitly configured:
- If `root_api_key` is set (non-empty): auto-selects `api_key` mode
- If `root_api_key` is not set: auto-selects `dev` mode

> **Note:** Setting `root_api_key` to an empty string `""` is invalid. Either set a non-empty value or remove the setting entirely.

## Setting Up (Server Side)

Configure the authentication mode in the `server` section of `ov.conf`:

```json
{
  "server": {
    "auth_mode": "api_key",
    "root_api_key": "your-secret-root-key"
  }
}
```

Start the server:

```bash
openviking-server
```

### Custom Authentication Plugins

The server uses a plugin-based auth architecture. Each `auth_mode` maps to an `AuthPlugin` implementation. Built-in plugins (`dev`, `api_key`, `trusted`) are auto-registered; third-party plugins can be added by subclassing `AuthPlugin` and registering it before startup.

**Plugin interface (`openviking.server.auth.plugin.AuthPlugin`)**

| Method | Purpose |
|--------|---------|
| `resolve_identity(request, api_key, x_openviking_account, x_openviking_user)` | Resolve credentials to a `ResolvedIdentity`. |
| `validate_config(config)` | Validate `ServerConfig` at startup; should `sys.exit(1)` on fatal misconfiguration. |
| `initialize(app, service, config)` | Initialize runtime state (e.g., `APIKeyManager`) on `app.state`. |
| `get_request_context_checks(path, identity)` | Optional post-auth path/identity checks. |
| `requires_api_key_manager()` | Whether Admin API routes need an `APIKeyManager`. |
| `can_skip_api_key_for_bot_proxy()` | Whether the bot proxy may skip API key validation (e.g., `dev` mode). |

**Register a custom plugin**

```python
from openviking.server.auth.plugin import AuthPlugin
from openviking.server.auth.registry import register_auth_plugin
from openviking.server.identity import ResolvedIdentity, Role

@register_auth_plugin
class LDAPAuthPlugin(AuthPlugin):
    auth_mode = "ldap"

    async def resolve_identity(self, request, *, api_key=None, x_openviking_account=None, x_openviking_user=None):
        # ... LDAP bind and identity resolution ...
        return ResolvedIdentity(role=Role.USER, account_id="...", user_id="...")

    def validate_config(self, config):
        pass

    async def initialize(self, app, service, config):
        pass
```

Then set `server.auth_mode = "ldap"` in `ov.conf`.

**Custom roles**

The built-in `Role` class supports dynamic registration of custom roles with privilege ranks:

```python
from openviking.server.identity import Role

Role.register("operator", rank=1)  # Between USER (0) and ADMIN (1)
```

Custom roles work with `require_role()` and `require_auth_role()` decorators out of the box.

## Managing Accounts and Users

Normal requests in both `api_key` and `trusted` modes do not need Admin API as a prerequisite for ordinary reads, writes, search, or session access. Admin API is still the place to create accounts, register users, change roles, and issue user keys.

Use the root key to create accounts (workspaces) and users via the Admin API:

```bash
# Create account with first admin
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "X-API-Key: your-secret-root-key" \
  -H "Content-Type: application/json" \
  -d '{"account_id": "acme", "admin_user_id": "alice"}'
# Returns: {"result": {"account_id": "acme", "admin_user_id": "alice", "user_key": "..."}}

# Register a regular user (as ROOT or ADMIN)
curl -X POST http://localhost:1933/api/v1/admin/accounts/acme/users \
  -H "X-API-Key: your-secret-root-key" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "bob", "role": "user"}'
# Returns: {"result": {"account_id": "acme", "user_id": "bob", "user_key": "..."}}
```

Trusted deployments can also call Admin API through a trusted gateway. There are two supported patterns:

- Present the trusted deployment's `root_api_key`. For `/api/v1/admin/*`, the server treats the request as ROOT after validating that key.
- Optionally also present `X-OpenViking-Account` + `X-OpenViking-User` when the admin route targets a specific account/user. Those headers must match the target URL and are kept as the request identity, but authorization still comes from the trusted `root_api_key`.

Example using a trusted upstream identity:

```bash
# First, register the gateway admin (do this once in api_key mode)
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "X-API-Key: your-secret-root-key" \
  -H "Content-Type: application/json" \
  -d '{"account_id": "platform", "admin_user_id": "gateway-admin"}'

# Then promote it to root if it needs cross-account admin access
curl -X PUT http://localhost:1933/api/v1/admin/accounts/platform/users/gateway-admin/role \
  -H "X-API-Key: your-secret-root-key" \
  -H "Content-Type: application/json" \
  -d '{"role": "root"}'

# Then, in trusted mode, use that identity to call Admin API
curl -X POST http://localhost:1933/api/v1/admin/accounts \
  -H "X-API-Key: your-secret-root-key" \
  -H "X-OpenViking-Account: platform" \
  -H "X-OpenViking-User: gateway-admin" \
  -H "Content-Type: application/json" \
  -d '{
    "account_id": "acme",
    "admin_user_id": "alice"
  }'
```

## Using API Keys (Client Side)

OpenViking accepts API keys via two headers:

**X-API-Key header**

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-API-Key: <user-key>"
```

**Authorization: Bearer header**

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "Authorization: Bearer <user-key>"
```

**Python SDK (HTTP)**

```python
import openviking as ov

client = ov.SyncHTTPClient(
    url="http://localhost:1933",
    api_key="<user-key>",
)
```

**CLI (via ovcli.conf)**

```json
{
  "url": "http://localhost:1933",
  "api_key": "<user-key>"
}
```

When you use a user key or admin key, the server derives `account` and `user`
from the key. Do not send `X-OpenViking-Account` / `X-OpenViking-User` in
`api_key` mode; those identity headers are accepted only in `trusted` mode.

**CLI override flags**

```bash
openviking ls viking://
```

### Using --sudo with Root API Key

The CLI supports configuring both `api_key` (for regular user operations) and `root_api_key` (for admin operations) in `ovcli.conf`:

```json
{
  "url": "http://localhost:1933",
  "api_key": "<user-key>",
  "root_api_key": "<root-key>"
}
```

When you need to perform admin commands (`admin`, `system`, `reindex`), use the `--sudo` flag to elevate privileges:

```bash
# List all accounts (requires root privileges)
ov --sudo admin list-accounts

# System commands
ov --sudo system status
```

The `--sudo` flag:
- Only works with management/system commands: `admin`, `system`
- Will error if used with non-admin commands
- Will error if `root_api_key` is not configured in `ovcli.conf`
- Uses `root_api_key` instead of `api_key` for the request

### Tenant Data Access

Tenant-scoped data APIs (for example `ls`, `find`, resources, and sessions)
must use a key that is bound to an account/user in `api_key` mode. That can be
a `USER` key or an `ADMIN` key; an `ADMIN` key accesses data as its own user and
cannot switch identity with `X-OpenViking-Account` / `X-OpenViking-User`.

A `ROOT` key is not bound to a tenant user, so it cannot access tenant-scoped
data APIs in `api_key` mode. If a deployment needs an upstream gateway to assert
`account` / `user`, use `trusted` mode instead of passing identity headers with a
root key.

**ovcli.conf**

```json
{
  "url": "http://localhost:1933",
  "auth_mode": "trusted",
  "api_key": "your-trusted-server-key",
  "account": "acme",
  "user": "alice"
}
```

## Trusted Mode

Trusted mode skips user-key lookup and instead trusts explicit identity headers on each request:

```json
{
  "server": {
    "auth_mode": "trusted",
    "host": "127.0.0.1"
  }
}
```

Rules in trusted mode:

- Normal data access does not require user registration or user-key provisioning first.
- `X-OpenViking-Account` and `X-OpenViking-User` are required on tenant-scoped requests.
- Use `peer_id` in session-message bodies for stable speaker attribution. Use `X-OpenViking-Actor-Peer` to filter the current user's peer collection for filesystem and retrieval operations.
- `/api/v1/admin/*` is special: when a configured `root_api_key` is presented, trusted mode treats the request as ROOT. Explicit account/user headers are allowed only when they are complete and match the target URL.
- For ordinary trusted data APIs, role is determined by looking up the account/user in APIKeyManager. If the user exists, their configured role is used; otherwise it defaults to `USER`.
- Trusted identity comes from the headers, not from a user key. If `root_api_key` is configured, it acts as proof that the caller is an approved trusted upstream.
- If `root_api_key` is also configured, every request must still provide a matching API key.
- Only expose this mode behind a trusted network boundary or an identity-injecting gateway.

Implications:

- Trusted mode is not development mode.
- Trusted mode does not use the Admin API as a prerequisite for ordinary reads, writes, search, or session access.
- Admin API remains available in trusted mode to upstreams authenticated with the configured `root_api_key`.
- Trusted Admin API responses omit `user_key` from account creation and user registration results.
- `root` can create/delete accounts and change roles; `admin` can manage users inside its own account; `user` cannot call Admin API.
- To use Admin API in trusted mode on non-localhost deployments, configure `root_api_key` and pass it with each admin request.

**curl**

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-OpenViking-Account: acme" \
  -H "X-OpenViking-User: alice"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(
    url="http://localhost:1933",
    account="acme",
    user="alice",
)
```

## Dev Mode

When `auth_mode = "dev"` (or auto-detected when no `root_api_key` is configured), authentication is disabled. All requests are accepted as ROOT with the default account. **This is only allowed when the server binds to localhost** (`127.0.0.1`, `localhost`, or `::1`). If `host` is set to a non-loopback address (e.g. `0.0.0.0`) in `dev` mode, the server will refuse to start.

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 1933
  }
}
```

Or explicitly:

```json
{
  "server": {
    "auth_mode": "dev",
    "host": "127.0.0.1",
    "port": 1933
  }
}
```

> **Security note:** The default `host` is `127.0.0.1`. If you need to expose the server on the network, you **must** configure `root_api_key`.

## Roles and Permissions

| Role | Scope | Capabilities |
|------|-------|-------------|
| ROOT | Global | All operations + Admin API (create/delete accounts, manage users) |
| ADMIN | Own account | Regular operations + manage users in own account |
| USER | Own account | Regular operations (ls, read, find, sessions, etc.) |

In `trusted` mode, ordinary tenant requests default to `USER` unless the account/user is registered with a higher role. Admin routes also allow a trusted ROOT fallback when no explicit identity is provided.

## Unauthenticated Endpoints

The `/health` endpoint never requires authentication. This allows load balancers and monitoring tools to check server health.

```bash
curl http://localhost:1933/health
```

## Admin API Reference

| Method | Endpoint | Role | Description |
|--------|----------|------|-------------|
| POST | `/api/v1/admin/accounts` | ROOT | Create account with first admin |
| GET | `/api/v1/admin/accounts` | ROOT | List all accounts |
| DELETE | `/api/v1/admin/accounts/{id}` | ROOT | Delete account |
| POST | `/api/v1/admin/accounts/{id}/users` | ROOT, ADMIN | Register user |
| GET | `/api/v1/admin/accounts/{id}/users` | ROOT, ADMIN | List users |
| DELETE | `/api/v1/admin/accounts/{id}/users/{uid}` | ROOT, ADMIN | Remove user |
| PUT | `/api/v1/admin/accounts/{id}/users/{uid}/role` | ROOT | Change user role |
| POST | `/api/v1/admin/accounts/{id}/users/{uid}/key` | ROOT, ADMIN | Regenerate user key |

## Related Documentation

- [Multi-Tenant](../concepts/11-multi-tenant.md) - Capabilities, sharing boundaries, and integration patterns
- [Configuration](01-configuration.md) - Config file reference
- [Deployment](03-deployment.md) - Server setup
- [API Overview](../api/01-overview.md) - API reference
