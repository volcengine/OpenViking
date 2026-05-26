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
    reverse_proxy openviking:1933
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

### 4. Launch

```bash
docker compose up -d
```

The first HTTPS request triggers ACME certificate issuance. Subsequent
requests use the cached cert. Caddy handles renewal automatically.

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

```nginx
server {
    listen 443 ssl http2;
    server_name ov.your-domain.com;

    ssl_certificate     /etc/letsencrypt/live/ov.your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ov.your-domain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:1933;
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

Point the CDN origin at `http://your-server-ip:1933`. Set
`OPENVIKING_PUBLIC_BASE_URL=https://ov.your-domain.com` so the server knows
its public address. Make sure the CDN forwards `Host`, `X-Forwarded-Proto`,
and `X-Forwarded-Host`.

## Telling the server its public URL

OAuth metadata, `WWW-Authenticate` headers, and resource URLs need to embed
the public origin. Resolution order (**highest to lowest**):

1. `OPENVIKING_PUBLIC_BASE_URL` environment variable
2. `oauth.issuer` in `ov.conf`
3. `X-Forwarded-Proto` + `X-Forwarded-Host` request headers
4. The request's `Host` header

Behind any reverse proxy, set option 1 explicitly:

```bash
export OPENVIKING_PUBLIC_BASE_URL="https://ov.your-domain.com"
```

or in `ov.conf`:

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
`reverse_proxy openviking:1933` — **kept only for compatibility with
deployments that already bookmarked 1934**. New deployments can connect to
1933 directly; there is no routing value here. Remove the caddy service and
the 1934 port mapping in `docker-compose.yml` if you don't need it.

## Related

- [Deployment Guide](03-deployment.md) — Docker, systemd, Kubernetes
- [OAuth Guide](11-oauth.md) — OAuth 2.1 setup and client onboarding
- [Authentication](04-authentication.md) — API key management
