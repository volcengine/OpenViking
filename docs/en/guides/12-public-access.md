# Public Access & Reverse Proxy

OpenViking runs two internal services:

| Port | Service | Handles |
|------|---------|---------|
| 1933 | API server | REST API, MCP, OAuth, `.well-known/*` |
| 8020 | Console | Web UI at `/console/...` |

The bundled **Caddy reverse proxy** merges them into a single port — **1934** — so
clients only need one URL. This works out of the box with `docker compose up`.

## Port overview

```
                   ┌────────────────────────┐
Internet / LAN ──► │  Caddy  :1934  (HTTP)  │
                   │                        │
                   │  /console/*  → :8020   │
                   │  /*          → :1933   │
                   └────────────────────────┘
```

Port 1934 is plain HTTP — fine for local development, internal networks, and
as an upstream target behind your own TLS-terminating proxy or CDN.

For **public HTTPS** (required for OAuth with MCP clients like Claude.ai,
ChatGPT, Cursor), Caddy can also serve port 443 with automatic Let's Encrypt
certificates. See [Adding HTTPS](#adding-https-for-public-access) below.

## Quick start (local / internal)

```bash
docker compose up -d
```

Both services are now reachable on one port:

```bash
curl http://localhost:1934/health                  # API server health
curl http://localhost:1934/api/v1/system/status \
     -H "X-Api-Key: YOUR_KEY"                     # REST API
open http://localhost:1934/console                  # Web console
```

You can still access 1933 and 8020 directly — those port mappings are left in
`docker-compose.yml` for debugging. Once you're confident everything works
through 1934, feel free to comment them out.

## Adding HTTPS for public access

You need: a public domain, ports 80 + 443 reachable, DNS pointing here.

### 1. Create `.env`

```dotenv
OPENVIKING_PUBLIC_BASE_URL=https://ov.your-domain.com
OV_ACME_EMAIL=admin@your-domain.com   # optional; recommended for Let's Encrypt
```

`OPENVIKING_PUBLIC_BASE_URL` is read by both the OpenViking container (it
publishes this URL in OAuth metadata and `WWW-Authenticate` headers) and by
Caddy (as the site address for the HTTPS block).

### 2. Add a domain block to `Caddyfile`

Append this below the existing `:1934` block:

```caddyfile
{$OPENVIKING_PUBLIC_BASE_URL} {
    @console path /console /console/*
    handle @console {
        reverse_proxy openviking:8020
    }
    handle {
        reverse_proxy openviking:1933
    }
    # Pin ACME registration email (optional):
    # tls {$OV_ACME_EMAIL}
}
```

The `:1934` block stays — it continues to serve HTTP for local/internal
access. The new block serves HTTPS on 443 for the public domain.

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
```

## Using your own reverse proxy

If you already have nginx, Traefik, or another proxy handling TLS, point it
at port 1934 instead of juggling 1933 + 8020 separately. Port 1934 already
does the path-based routing internally.

### nginx (TLS termination at nginx, plain HTTP to 1934)

```nginx
server {
    listen 443 ssl http2;
    server_name ov.your-domain.com;

    ssl_certificate     /etc/letsencrypt/live/ov.your-domain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/ov.your-domain.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:1934;
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

With this setup, remove the Caddy HTTPS block from the Caddyfile — keep only
the `:1934` block.

### Caddy (external, without docker-compose Caddy)

If you run Caddy on the host rather than inside compose:

```caddyfile
ov.your-domain.com {
    reverse_proxy 127.0.0.1:1934
}
```

### Cloudflare / CDN

Point the CDN origin at `http://your-server-ip:1934`. Set
`OPENVIKING_PUBLIC_BASE_URL=https://ov.your-domain.com` so the server knows
its public-facing origin. Ensure the CDN forwards `Host`,
`X-Forwarded-Proto`, and `X-Forwarded-Host` headers.

## Tell the server its public URL

OAuth metadata, `WWW-Authenticate` headers, and resource URLs all need to
contain the public-facing origin. Resolution order (highest priority first):

1. `OPENVIKING_PUBLIC_BASE_URL` environment variable
2. `oauth.issuer` in `ov.conf`
3. `X-Forwarded-Proto` + `X-Forwarded-Host` request headers
4. The request `Host` header

Behind any reverse proxy, set option 1 explicitly:

```bash
export OPENVIKING_PUBLIC_BASE_URL="https://ov.your-domain.com"
```

Or in `ov.conf`:

```jsonc
{
  "oauth": {
    "enabled": true,
    "issuer": "https://ov.your-domain.com"
  }
}
```

## HTTPS and OAuth

OAuth 2.1 (and the MCP SDK) **requires HTTPS** for any non-localhost issuer.
If the server's published origin is `http://` on a non-loopback address, MCP
clients will refuse to connect with an "Issuer URL must be HTTPS" error.

This is a protocol-level requirement, not an OpenViking limitation. For local
testing, `http://127.0.0.1:1934` works without HTTPS. For anything else, set
up TLS as described above.

Non-OAuth API access (API key auth) works fine over HTTP if you accept the
risk — the protocol doesn't enforce TLS for bearer tokens.

## Without Docker

If you run `openviking-server` and the console directly (systemd, bare
metal, etc.), install Caddy on the host and use the same Caddyfile pattern
with `127.0.0.1` upstreams:

```caddyfile
:1934 {
    @console path /console /console/*
    handle @console {
        reverse_proxy 127.0.0.1:8020
    }
    handle {
        reverse_proxy 127.0.0.1:1933
    }
}

# Add domain block for HTTPS if needed:
# ov.your-domain.com { ... }
```

Or with nginx:

```nginx
server {
    listen 1934;

    location /console {
        proxy_pass http://127.0.0.1:8020;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host  $host;
    }

    location / {
        proxy_pass http://127.0.0.1:1933;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host  $host;
    }
}
```

## Related

- [Deployment Guide](03-deployment.md) — Docker, systemd, Kubernetes
- [OAuth Guide](11-oauth.md) — OAuth 2.1 configuration and client setup
- [Authentication](04-authentication.md) — API key management
