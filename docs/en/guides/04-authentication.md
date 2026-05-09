# Authentication

OpenViking Server supports three authentication modes with role-based access control: `api_key`, `trusted`, and `dev`. The mode is auto-detected if not explicitly configured.

## Overview

OpenViking uses a two-layer API key system:

| Key Type | Created By | Role | Purpose |
|----------|-----------|------|---------|
| Root Key | Server config (`root_api_key`) | ROOT | Full access + admin operations |
| User Key | Admin API | ADMIN or USER | Per-account access |

All API keys are plain random tokens with no embedded identity. The server resolves identity by first comparing against the root key, then looking up the user key index.

## Authentication Modes

| Mode | `server.auth_mode` | Identity Source | Typical Use |
|------|--------------------|-----------------|-------------|
| API key mode | `"api_key"` | API key, with optional tenant headers for root requests | Standard multi-tenant deployment |
| Trusted mode | `"trusted"` | `X-OpenViking-Account` / `X-OpenViking-User` / optional `X-OpenViking-Agent`, plus `root_api_key` on non-localhost deployments. Role is looked up from APIKeyManager if the user exists. | Behind a trusted gateway or internal network boundary |
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

- Present only the trusted deployment's `root_api_key`. For `/api/v1/admin/*`, the server treats the request as ROOT even without `X-OpenViking-Account` / `X-OpenViking-User`.
- Present `X-OpenViking-Account` + `X-OpenViking-User` for a registered gateway user. In that case the server looks up the effective role from the user registry.

Example using a registered gateway user:

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
    "admin_user_id": "alice",
    "isolate_user_scope_by_agent": true,
    "isolate_agent_scope_by_user": false
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
    agent_id="my-agent"
)
```

**CLI (via ovcli.conf)**

```json
{
  "url": "http://localhost:1933",
  "api_key": "<user-key>",
  "account": "acme",
  "user": "alice",
  "agent_id": "my-agent"
}
```

When you use a regular user key, `account` and `user` are optional because the server can derive them from the key. They are recommended when you use `trusted` mode or a root key against tenant-scoped APIs.

**CLI override flags**

```bash
openviking --account acme --user alice --agent-id my-agent ls viking://
```

### Using --sudo with Root API Key

The CLI supports configuring both `api_key` (for regular user operations) and `root_api_key` (for admin operations) in `ovcli.conf`:

```json
{
  "url": "http://localhost:1933",
  "api_key": "<user-key>",
  "root_api_key": "<root-key>",
  "account": "acme",
  "user": "alice",
  "agent_id": "my-agent"
}
```

When you need to perform admin commands (`admin`, `system`, `reindex`), use the `--sudo` flag to elevate privileges:

```bash
# List all accounts (requires root privileges)
ov --sudo admin list-accounts

# Reindex content
ov --sudo reindex viking://

# System commands
ov --sudo system status
```

The `--sudo` flag:
- Only works with admin commands: `admin`, `system`, `reindex`
- Will error if used with non-admin commands
- Will error if `root_api_key` is not configured in `ovcli.conf`
- Uses `root_api_key` instead of `api_key` for the request

### Accessing Tenant Data with Root Key

When using the root key to access tenant-scoped data APIs (e.g. `ls`, `find`, `sessions`), you must specify the target account and user. The server will reject the request otherwise. Admin API and system status endpoints are not affected.

**curl**

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-API-Key: your-secret-root-key" \
  -H "X-OpenViking-Account: acme" \
  -H "X-OpenViking-User: alice"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(
    url="http://localhost:1933",
    api_key="your-secret-root-key",
    account="acme",
    user="alice",
)
```

**ovcli.conf**

```json
{
  "url": "http://localhost:1933",
  "api_key": "your-secret-root-key",
  "account": "acme",
  "user": "alice",
  "agent_id": "my-agent"
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
- `X-OpenViking-Agent` is optional and defaults to `default`.
- `/api/v1/admin/*` is special: when no explicit identity is provided, trusted mode treats the request as ROOT. This is intended for trusted upstreams that authenticate only with the deployment's root API key.
- Role is determined by looking up the account/user in APIKeyManager. If the user exists, their configured role is used; otherwise it defaults to `USER`.
- Trusted identity comes from the headers, not from a user key. If `root_api_key` is configured, it still acts as proof that the caller is an approved trusted upstream.
- If `root_api_key` is also configured, every request must still provide a matching API key.
- Only expose this mode behind a trusted network boundary or an identity-injecting gateway.

Implications:

- Trusted mode is not development mode.
- Trusted mode does not use the Admin API as a prerequisite for ordinary reads, writes, search, or session access.
- Admin API remains available in trusted mode for users that have been registered with appropriate roles (root/admin).
- Trusted Admin API responses omit `user_key` from account creation and user registration results.
- `root` can create/delete accounts and change roles; `admin` can manage users inside its own account; `user` cannot call Admin API.
- To use Admin API in trusted mode, first register the gateway's service account with the appropriate role using the Admin API in api_key mode.

**curl**

```bash
curl http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-OpenViking-Account: acme" \
  -H "X-OpenViking-User: alice" \
  -H "X-OpenViking-Agent: my-agent"
```

**Python SDK**

```python
import openviking as ov

client = ov.SyncHTTPClient(
    url="http://localhost:1933",
    account="acme",
    user="alice",
    agent_id="my-agent",
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
