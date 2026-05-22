# OAuth 2.1 Guide

OpenViking server ships a native OAuth 2.1 implementation. Any client that
needs OAuth — including MCP clients (Claude.ai, Claude Desktop, ChatGPT,
Cursor) and any future first-party browser app — can authorize against the
server directly, without a third-party proxy. The protocol surface (DCR,
authorize, token, metadata) is provided by the official `mcp.server.auth`
SDK and is otherwise standards-compliant OAuth 2.1, so non-MCP OAuth clients
work just as well.

## Recommended setup

> **Prerequisite**: public HTTPS. OAuth 2.1 (and the MCP SDK) **requires
> HTTPS** for any non-localhost issuer. See the
> [Public Access Guide](12-public-access.md) for how to set up HTTPS with
> Caddy or nginx.

1. **Set up HTTPS** — follow [Public Access Guide](12-public-access.md) to
   get `https://ov.your-domain.com` working (Caddy + `.env` +
   `docker compose up`).

2. **Enable OAuth in `~/.openviking/ov.conf`:**

   ```json
   { "oauth": { "enabled": true } }
   ```

3. **Restart** (`docker compose restart openviking`).

4. **Connect a client.** In Claude.ai → Connectors → Add, enter
   `https://ov.your-domain.com/mcp`. The browser flips to
   `https://ov.your-domain.com/studio/oauth/consent`: if you aren't already
   signed in to Studio, paste your API key into the "Connection & Identity"
   dialog; then click **Authorize** on the consent card. Claude.ai gets the
   token and the connector is live.

That's the whole production path. The rest of this guide explains why each
piece exists, how to test locally without HTTPS, and how to verify with curl
when something doesn't work.

---

## Why native OAuth

Some MCP clients only accept OAuth 2.1, not API keys. Until now the only path
was to deploy the community [MCP-Key2OAuth](https://github.com/t0saki/MCP-Key2OAuth)
Cloudflare Worker proxy that translates OAuth into an API-Key bearer. Native
support removes:

- The extra deployment unit (CF Worker + KV namespaces)
- The third-party trust boundary (the proxy operator can decrypt the upstream API key)
- The copy-paste UX where users paste the API key into a browser textbox

API-Key auth still works as before — OAuth layers on top.

---

## How it works

OpenViking's authorize UI runs inside **OpenViking Studio** by default
(same-origin with the main server and sharing Studio's session). When an MCP
client opens the browser to authorize:

```
1.  MCP client    POST /mcp                 → 401 + WWW-Authenticate header
2.  MCP client    GET  /.well-known/oauth-protected-resource (RFC 9728)
3.  MCP client    GET  /.well-known/oauth-authorization-server (RFC 8414)
4.  MCP client    POST /register (Dynamic Client Registration, RFC 7591)
5.  MCP client    GET  /authorize?... (browser redirect)
6.  Server        →    /studio/oauth/consent?pending=...
                       (consent SPA loads and fetches
                        /api/v1/auth/oauth/pending/<id> to render
                        client_name / redirect_host / scopes)
7.  User          confirms on the consent card in their already signed-in
                  Studio tab (or picks "Use a different API key" in the
                  IdentityPicker to do a one-off authorize)
8.  Studio        POST /api/v1/auth/oauth-verify
                  (Authorization: Bearer <api-key>,
                   body: {pending_id, decision})
                  Server marks pending as verified and binds the caller's
                  identity (account / user / role).
9.  Studio        polls /oauth/authorize/page/status, sees "approved",
                  redirects browser to the MCP client's redirect_uri with code
10. MCP client    POST /token (PKCE S256) → access_token (ovat_...) +
                  refresh_token (ovrt_...)
11. MCP client    POST /mcp (Authorization: Bearer ovat_...) → tool list
```

Consent happens inside Studio, so there's **no cross-tab code copying** on
the happy path. Studio already holds your API key in `sessionStorage` (the
one you entered when signing in); the consent SPA uses it as
`Authorization: Bearer` against `/api/v1/auth/oauth-verify`.

If you can't open Studio on the current device (CLI MCP clients, cross-device
authorize), the consent page's "Use another device →" link falls back to the
server-rendered HTML page at `/oauth/authorize/page`: it displays a 6-char
`display_code`, which you type at `/studio/oauth/verify` on another device
that's already signed in to Studio.

The "push" OTP endpoint `POST /api/v1/auth/otp` is still available for
CLI / scripted scenarios — and the **OAuth client OTP** block at the bottom
of the Studio "Connection & Identity" dialog gives you a one-click way to
mint a short-lived OTP to hand to an MCP client.

---

## Quick start (HTTP, local only)

The fastest way to verify OAuth is wired correctly is on `127.0.0.1`. The MCP
SDK accepts `http://127.0.0.1` and `http://localhost` as issuer URLs without
HTTPS — but Claude.ai and Claude Desktop themselves require **public HTTPS**
endpoints, so this mode is only useful for local testing with tools like
[MCP Inspector](https://github.com/modelcontextprotocol/inspector).

1. **Enable OAuth in `~/.openviking/ov.conf`:**

   ```json
   {
     "oauth": {
       "enabled": true
     }
   }
   ```

2. **Start the services:**

   ```bash
   docker compose up -d
   ```

   Or without Docker:

   ```bash
   openviking-server
   ```

3. **Open Studio and sign in** at <http://127.0.0.1:1933/studio>. From the
   top-right open "Connection & Identity", paste your API key, click Save.

4. **Connect a local MCP client** (e.g. MCP Inspector) to
   `http://127.0.0.1:1933/mcp`. The client will hit the OAuth flow above and
   the browser will land on `/studio/oauth/consent?pending=...`; click
   Authorize and the client will receive a token. For push-style scenarios,
   use the **OAuth client OTP** block in the Connection & Identity dialog.

For Claude.ai / Claude Desktop on the public internet, see the
[Public Access Guide](12-public-access.md).

---

## Production deployment (HTTPS)

OAuth 2.1 **requires HTTPS** for any non-localhost issuer. The
[Public Access Guide](12-public-access.md) covers the full setup — Caddy,
nginx, docker compose, CDN — in detail. The short version:

1. Follow [Public Access Guide § Adding HTTPS](12-public-access.md#adding-https-for-public-access)
   to get `https://your-domain.com` serving port 1934 over TLS.
2. Enable OAuth: `{ "oauth": { "enabled": true } }` in `ov.conf`.
3. Restart: `docker compose restart openviking`.
4. Set `OPENVIKING_PUBLIC_BASE_URL=https://your-domain.com` in `.env` (the
   server uses this as the issuer in OAuth metadata and `WWW-Authenticate`).

Once HTTPS + OAuth are both up, connect clients as described below.

---

## Connecting OAuth-only MCP clients

### Claude.ai (web)

1. Settings → Connectors → **Add connector**.
2. Enter `https://my.ov/mcp` as the server URL.
3. Claude opens an authorize page that redirects to
   `https://my.ov/studio/oauth/consent?pending=...`.
4. If you're not yet signed in to Studio, paste your API key in the
   "Connection & Identity" dialog (or pick "Use a different API key" in the
   IdentityPicker for a one-off authorize).
5. Confirm client_name / redirect_host on the consent card → **Authorize**.
6. The popup redirects back to Claude with a fresh access token.

> If you triggered authorization from a CLI device with no local browser,
> either copy the authorize URL to a desktop browser, or click "Use another
> device →" on the consent page to switch to the 6-character display_code
> path (enter it on another device's `/studio/oauth/verify`).

### Claude Desktop / Claude Code

The same flow works from Claude Desktop. For Claude Code, the simpler path is
still API key:

```bash
claude mcp add --transport http openviking https://my.ov/mcp \
  --header "Authorization: Bearer <api-key>"
```

If you want Claude Code to drive OAuth, the connector flow is identical to
Claude.ai's once configured.

### ChatGPT (Codex, Plus, Enterprise)

Connector setup is via Settings → Beta features → Custom Connectors. Enter
the MCP URL; ChatGPT discovers the OAuth endpoints from the
`/.well-known/...` documents and walks the same authorize → token flow.

### Cursor

Cursor's MCP integration honors OAuth automatically when the server URL
returns a 401 with `WWW-Authenticate: Bearer resource_metadata=...`. Add the
URL via Cursor's MCP settings.

---

## Verifying with `curl`

You can drive the entire flow without a real MCP client:

```bash
# 1. Register a client
curl -X POST -H "Content-Type: application/json" \
     -d '{"redirect_uris":["http://127.0.0.1:9999/cb"],"client_name":"test","token_endpoint_auth_method":"none"}' \
     https://my.ov/register
# → {"client_id":"...", ...}

# 2. PKCE pair
VERIFIER=$(openssl rand -base64 64 | tr -d '=+/' | head -c 64)
CHALLENGE=$(printf "%s" "$VERIFIER" | openssl dgst -sha256 -binary | basenc --base64url | tr -d '=')

# 3. Open the authorize URL in a browser. The page shows a 6-char code.
echo "https://my.ov/authorize?response_type=code&client_id=$CID&redirect_uri=http://127.0.0.1:9999/cb&code_challenge=$CHALLENGE&code_challenge_method=S256&state=xyz"

# 4. Approve from the Studio consent page (or via API).
#    - Studio path uses pending_id (?pending=... from the authorize URL).
#    - Cross-device path uses the 6-char display_code.
curl -X POST -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/json" \
     -d '{"pending_id":"<pending-id-from-authorize-url>","decision":"approve"}' \
     https://my.ov/api/v1/auth/oauth-verify

# 5. The browser auto-redirects to /cb?code=ovac_...&state=xyz. Copy the code.

# 6. Exchange the auth code for tokens.
curl -X POST \
     -d "grant_type=authorization_code&code=ovac_...&client_id=$CID&code_verifier=$VERIFIER&redirect_uri=http://127.0.0.1:9999/cb" \
     https://my.ov/token
# → {"access_token":"ovat_...","refresh_token":"ovrt_...","expires_in":3600}

# 7. Call MCP with the access token.
curl -X POST -H "Authorization: Bearer ovat_..." \
     -d '{"jsonrpc":"2.0","method":"tools/list","id":1}' \
     https://my.ov/mcp
```

---

## Configuration reference

`ov.conf` excerpt:

```jsonc
{
  "oauth": {
    "enabled": false,                       // off by default
    "issuer": null,                         // e.g. "https://my.ov" (optional; env var wins)
    "access_token_ttl_seconds": 3600,       // 1 hour
    "refresh_token_ttl_seconds": 2592000,   // 30 days
    "auth_code_ttl_seconds": 300,           // 5 minutes
    "otp_ttl_seconds": 300,                 // 5 minutes
    "db_filename": "oauth.db"               // relative to storage.workspace
  }
}
```

Environment variables:

| Variable | Purpose |
|---|---|
| `OPENVIKING_PUBLIC_BASE_URL` | Highest-priority public origin override (used as issuer, in PRM, in `WWW-Authenticate`) |
| `OPENVIKING_CONFIG_FILE` | Path to `ov.conf` (or pass `--config`) |

---

## Token model

| Token | Format | Prefix | Lifetime | Storage |
|---|---|---|---|---|
| Access token | `secrets.token_urlsafe(40)` | `ovat_` | 1 hour | SQLite (SHA-256 indexed) |
| Refresh token | `secrets.token_urlsafe(40)` | `ovrt_` | 30 days | SQLite (SHA-256 indexed) |
| Authorization code | `secrets.token_urlsafe(40)` | `ovac_` | 5 minutes | SQLite (SHA-256 indexed) |
| Display code (page) | 6-char alphanumeric (no O/0/I/1) | — | 10 minutes | SQLite (`oauth_pending_authorizations`) |

All tokens are opaque; OpenViking does **not** issue JWTs. There is no
cryptographic key to manage on the server side. Token claims are looked up
from SQLite on every request, so revoking a token is a single `UPDATE`.

### Token = identity

Each issued token is bound to a single `(account_id, user_id, role)` triple
recorded at authorization time. An OAuth token grants the same permissions
as the API key that produced it — *not* more, *not* less.

### OAuth lifetime ≤ authorizing key lifetime

Every issued token additionally records the SHA-256 fingerprint of the API
key whose holder authorized it. On every OAuth bearer request the server
recomputes the user's current key fingerprint and demands a strict match.
The practical effects:

- **Rotating** a user's API key (`regenerate_key`) immediately invalidates
  every OAuth access and refresh token previously issued under that user.
  No manual revocation step is needed — the next bearer request gets a 401
  asking the client to re-authorize.
- **Removing** a user (`remove_user`) has the same effect: the fingerprint
  lookup returns `None` and all the user's OAuth tokens stop working.
- **ROOT** keys and **trusted-mode** identities cannot issue OAuth state
  (no per-user key to bind to). `/api/v1/auth/otp` and
  `/api/v1/auth/oauth-verify` reject these callers with 400.

The fingerprint is `sha256(stored_key_value)`, where the stored value is
either the plaintext key (when API key hashing is disabled) or its
Argon2id hash (when enabled). Both are written once at create / regenerate
and never mutate, so the fingerprint is stable until the next rotation.

---

## Troubleshooting

### Claude.ai shows "We couldn't connect" without ever opening a popup

The first thing Claude.ai does is GET `/.well-known/oauth-protected-resource`
on the URL you entered. If that 404s, the OAuth flow doesn't start. Check:

```bash
curl -i https://my.ov/.well-known/oauth-protected-resource
```

You should get a JSON body with `authorization_servers`. If you get 404,
either OAuth is disabled (`oauth.enabled = false`) or the reverse proxy isn't
forwarding `/.well-known/...` to 1933.

### "Issuer URL must be HTTPS"

The MCP SDK rejects `http://` issuers other than `127.0.0.1` / `localhost`.
Either:

- Set `OPENVIKING_PUBLIC_BASE_URL=https://my.ov`, or
- Set `oauth.issuer` to an `https://` URL in `ov.conf`, or
- For local testing only, ensure your client connects via `http://127.0.0.1:1933` directly

### The cross-device fallback shows a code, but `/studio/oauth/verify` says "Invalid code"

Codes are 6 characters, **uppercase**, and case-sensitive on the wire. The
`/studio/oauth/verify` input normalizes to uppercase. If you're typing
manually, check for letters that look like digits (the alphabet excludes
`O`, `0`, `I`, `1` to reduce confusion).

### Token rotates but the next refresh is rejected

Refresh tokens are one-shot. If you refresh and *both* the old and new
refresh tokens get used (e.g. by a buggy client), the second one will be
rejected and that token chain is revoked entirely (RFC 9700 §4.14). The
client must restart the authorize flow.

### `WWW-Authenticate` header missing on `/mcp` 401

The header is only emitted when an `oauth_provider` is registered on
`app.state` — i.e. when `oauth.enabled = true`. Confirm with:

```bash
curl -i https://my.ov/mcp -d '{}' -H 'Content-Type: application/json' | grep -i www-authenticate
```

---

## References

- [Public Access & Reverse Proxy Guide](12-public-access.md) — HTTPS, Caddy, nginx, docker compose
- [MCP Specification — Authorization](https://modelcontextprotocol.io/specification/2025-03-26/server/authorization)
- [RFC 8414 — OAuth 2.0 Authorization Server Metadata](https://datatracker.ietf.org/doc/html/rfc8414)
- [RFC 9728 — OAuth 2.0 Protected Resource Metadata](https://datatracker.ietf.org/doc/html/rfc9728)
- [RFC 7591 — Dynamic Client Registration](https://datatracker.ietf.org/doc/html/rfc7591)
- [RFC 7636 — PKCE](https://datatracker.ietf.org/doc/html/rfc7636)
- [OpenViking MCP Integration Guide](06-mcp-integration.md)
