# Public Access & Reverse Proxy

OpenViking serves REST API, MCP, OAuth, `.well-known/*`, and Web Studio
(`/studio`) on port 1933 by default. This guide shows how to put it behind a
public HTTPS domain.

> **Why HTTPS**: OAuth 2.1 / the MCP SDK **require HTTPS** for any
> non-localhost issuer — Claude.ai, Claude Desktop, ChatGPT, Cursor and other
> OAuth MCP clients refuse to connect over plain HTTP and report "Issuer URL
> must be HTTPS". API-key-only clients (including Claude Code with
> `--header`) work over HTTP, but TLS is still strongly recommended for
> production.

Prerequisites: a public domain, ports 80 + 443 reachable, DNS pointing at
your host.

## Authenticate before exposing the proxy

Configure authentication before publishing any route, including when the upstream listens on `127.0.0.1`. A reverse proxy makes that localhost service reachable from the internet. The default `dev` mode accepts requests as ROOT; TLS and `OPENVIKING_PUBLIC_BASE_URL` do not add authentication.

For API key authentication, merge this section into `ov.conf`, replace the placeholder with a secret, and restart the server:

```json
{
  "server": {
    "auth_mode": "api_key",
    "root_api_key": "<your-secret-root-key>"
  }
}
```

Use the root key only for administration, such as creating the account and its first admin through the [Admin API](04-authentication.md#managing-accounts-and-users). Use the resulting user/admin key for tenant data APIs. Do not put the root key in a public browser client or configure the proxy to attach it to all requests. Keep the backend port private so clients use the intended HTTPS entrypoint.

Before allowing users to connect, check the public URL:

```bash
# Expected: 401 in the API key setup above, never a successful directory listing.
curl -sS -o /dev/null -w '%{http_code}\n' \
  'https://ov.your-domain.com/api/v1/fs/ls?uri=viking://resources'

# Set OPENVIKING_API_KEY to a tenant-bound user/admin key first. Expected: 200.
curl -sS -o /dev/null -w '%{http_code}\n' \
  -H "X-API-Key: $OPENVIKING_API_KEY" \
  'https://ov.your-domain.com/api/v1/fs/ls?uri=viking://resources'

# Health is intentionally unauthenticated; 200 here does not verify access control.
curl -sS https://ov.your-domain.com/health
```

If you use OIDC or a trusted identity gateway instead, follow that mode's [authentication requirements](04-authentication.md) and verify rejection of unauthenticated data requests. Do not expose a `trusted` backend directly to callers who can supply their own identity headers.

<a id="adding-https-for-public-access"></a>

## Option A: bundled Caddy with auto Let's Encrypt (recommended)

`docker compose up` already brings up a Caddy reverse-proxy container. Add a
domain block to it and you get HTTPS on 443 with auto-renewal.

### 1. Create `.env`

```dotenv
OPENVIKING_PUBLIC_BASE_URL=https://ov.your-domain.com
OV_ACME_EMAIL=admin@your-domain.com   # optional; recommended for Let's Encrypt
```

`OPENVIKING_PUBLIC_BASE_URL` is read by both the OpenViking container (used
as the issuer in OAuth metadata and `WWW-Authenticate` headers) and Caddy
(as the HTTPS site address).

### 2. Add a domain block to `Caddyfile`

```caddyfile
{$OPENVIKING_PUBLIC_BASE_URL} {
    reverse_proxy openviking:{$OPENVIKING_SERVER_PORT:1933}
    # Pin ACME registration email (optional):
    # tls {$OV_ACME_EMAIL}
}
```

### 3. Uncomment HTTPS lines in `docker-compose.yml`

Three places:

```yaml
# In caddy.ports — uncomment:
- "80:80"
- "443:443"

# In caddy.volumes — uncomment:
- caddy_data:/data
- caddy_config:/config

# At the bottom — uncomment:
volumes:
  caddy_data:
  caddy_config:
```

For a proxy-only public entrypoint, remove the OpenViking host port mapping, or bind it to `127.0.0.1`. Also remove the legacy `1934:1934` mapping or bind it to localhost. Caddy can still reach `openviking:1933` through the Compose network.

### 4. Launch

```bash
docker compose up -d
```

Caddy obtains and renews certificates for the configured domain. Check its logs if issuance fails; on-demand issuance is a separate configuration. See [Caddy automatic HTTPS](https://caddyserver.com/docs/automatic-https).

### 5. Verify

```bash
curl https://ov.your-domain.com/health
# {"status": "ok"}

# OAuth metadata (if oauth.enabled = true):
curl https://ov.your-domain.com/.well-known/oauth-authorization-server

# Open Studio in the browser:
open https://ov.your-domain.com/studio
```

## Option B: bring your own reverse proxy

If you already run nginx / Traefik / Envoy / Cloudflare for TLS termination,
point the upstream straight at OV's 1933.

### nginx

This example assumes nginx and OpenViking share a host. The upstream timeout is an example; size it for your longest request. Disabling response buffering lets streamed MCP responses reach the client promptly; see [nginx proxy buffering](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_buffering).

```nginx
server {
    listen 443 ssl;
    http2 on;  # nginx < 1.25.1: remove this line and use `listen 443 ssl http2;`
    server_name ov.your-domain.com;

    ssl_certificate     /etc/letsencrypt/live/ov.your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ov.your-domain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:1933;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_read_timeout 300s;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host  $host;
    }
}

server {
    listen 80;
    server_name ov.your-domain.com;
    return 301 https://$host$request_uri;
}
```

### Caddy (host install, no compose)

```caddyfile
ov.your-domain.com {
    reverse_proxy 127.0.0.1:1933
}
```

### Cloudflare / CDN

Configure an HTTPS origin with certificate validation, or a private origin tunnel. Keep direct origin access restricted to the proxy. Set `OPENVIKING_PUBLIC_BASE_URL=https://ov.your-domain.com`, forward the public host/protocol headers, and check that the proxy preserves streaming and allows your request duration and upload sizes.

## Telling the server its public URL

OAuth metadata and `WWW-Authenticate` headers need the public origin. The OAuth origin helper resolves it in this order (**highest to lowest**):

1. `OPENVIKING_PUBLIC_BASE_URL` environment variable
2. `oauth.issuer` in `ov.conf`
3. `X-Forwarded-Proto` + `X-Forwarded-Host` request headers
4. The request's `Host` header

For MCP upload URLs, `server.public_base_url` is the configuration fallback instead of `oauth.issuer`. Setting the environment variable keeps both aligned; if you also set `oauth.issuer`, use the same origin.

Behind a reverse proxy, set option 1 in the server process environment:

```bash
export OPENVIKING_PUBLIC_BASE_URL="https://ov.your-domain.com"
```

For Compose, set it in `.env` and run `docker compose up -d`; an `export` in an unrelated shell does not update an existing container. If configuring the OAuth issuer in `ov.conf` instead:

```jsonc
{
  "oauth": {
    "enabled": true,
    "issuer": "https://ov.your-domain.com"
  }
}
```

## Compatibility note: the `:1934` single-upstream proxy

`docker compose up` also ships a Caddy reverse proxy on port 1934, simply
`reverse_proxy openviking:{$OPENVIKING_SERVER_PORT:1933}` — **kept only for compatibility with
deployments that already bookmarked 1934**. New deployments can connect to
1933 on the private network; public clients should use the HTTPS entrypoint above. Remove the caddy service and
the 1934 port mapping in `docker-compose.yml` if you don't need it.

## Related

- [Deployment Guide](03-deployment.md) — Docker, systemd, Kubernetes
- [OAuth Guide](11-oauth.md) — OAuth 2.1 setup and client onboarding
- [Authentication](04-authentication.md) — API key management
