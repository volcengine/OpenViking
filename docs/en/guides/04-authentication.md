# Authentication

OpenViking Server supports two authentication modes with role-based access control: `api_key` and `trusted`.

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
| Trusted mode | `"trusted"` | `X-OpenViking-Account` / `X-OpenViking-User` / optional `X-OpenViking-Agent` headers | Behind a trusted gateway or internal network boundary |

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

- `X-OpenViking-Account` and `X-OpenViking-User` are required on tenant-scoped requests.
- `X-OpenViking-Agent` is optional and defaults to `default`.
- If `root_api_key` is also configured, every request must still provide a matching API key.
- Only expose this mode behind a trusted network boundary or an identity-injecting gateway.

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

## Roles and Permissions

| Role | Scope | Capabilities |
|------|-------|-------------|
| ROOT | Global | All operations + Admin API (create/delete accounts, manage users) |
| ADMIN | Own account | Regular operations + manage users in own account |
| USER | Own account | Regular operations (ls, read, find, sessions, etc.) |

## Development Mode

When `auth_mode = "api_key"` and no `root_api_key` is configured, authentication is disabled. All requests are accepted as ROOT with the default account. **This is only allowed when the server binds to localhost** (`127.0.0.1`, `localhost`, or `::1`). If `host` is set to a non-loopback address (e.g. `0.0.0.0`) without a `root_api_key`, the server will refuse to start.

```json
{
  "server": {
    "host": "127.0.0.1",
    "port": 1933
  }
}
```

> **Security note:** The default `host` is `127.0.0.1`. If you need to expose the server on the network, you **must** configure `root_api_key`.

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

- [Configuration](01-configuration.md) - Config file reference
- [Deployment](03-deployment.md) - Server setup
- [API Overview](../api/01-overview.md) - API reference
