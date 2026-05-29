# Web Studio Auto-Proxy Server

`server/proxy.mjs` is an optional, zero-dependency Node.js server that lets you
deploy Web Studio as a public site without exposing the OpenViking root API
key to the browser.

It does three things:

1. Serves the built `dist/` SPA bundle on the same origin.
2. Proxies OpenViking API paths to a configured upstream OV server, injecting
   `X-API-Key` (and optional account / user) server-side. Incoming
   `X-API-Key`, `Authorization`, `X-OpenViking-Account`, and `X-OpenViking-User`
   headers are stripped before forwarding, so the browser can't override the
   server-managed identity.
3. Publishes `/_studio/runtime-config.json` so the SPA knows it is in
   "auto-proxy mode" and:
   - hides the connection dialog form,
   - stops sending `X-API-Key` from the browser,
   - never persists credentials in `localStorage` / `sessionStorage`.

## Quick start

```bash
cd web-studio
npm ci
npm run build

OV_STUDIO_UPSTREAM=https://ov-api.example.com \
OV_STUDIO_API_KEY=$ROOT_API_KEY \
npm run proxy
```

Then open <http://localhost:3000>.

## Configuration

| Env var                  | Default                                            | Purpose                                                     |
| ------------------------ | -------------------------------------------------- | ----------------------------------------------------------- |
| `OV_STUDIO_UPSTREAM`     | (required)                                         | Upstream OpenViking Server origin, e.g. `https://ov.api`.   |
| `OV_STUDIO_API_KEY`      | (required)                                         | Root or scoped API key injected as `X-API-Key`.             |
| `OV_STUDIO_ACCOUNT_ID`   | _unset_                                            | If set, forwarded as `X-OpenViking-Account`.                |
| `OV_STUDIO_USER_ID`      | _unset_                                            | If set, forwarded as `X-OpenViking-User`.                   |
| `OV_STUDIO_HOST`         | `0.0.0.0`                                          | Bind host.                                                  |
| `OV_STUDIO_PORT`         | `3000`                                             | Bind port.                                                  |
| `OV_STUDIO_DIST_DIR`     | `<web-studio>/dist`                                | Path to the built SPA.                                      |
| `OV_STUDIO_PROXY_PATHS`  | `/api,/bot,/health,/ready,/openapi.json`           | Path prefixes proxied to upstream.                          |
| `OV_STUDIO_CORS_ORIGINS` | _empty_                                            | Comma-separated allowlist; `*` allows any. Same-origin only by default. |
| `OV_STUDIO_BASE_PATH`    | `/`                                                | SPA mount base, matches Vite `--base`.                      |

## Threat model

- The browser sees no API key, account, or user header.
- Anyone able to reach the proxy origin can act with the configured identity.
  This is intentional for "open studio" deployments. Lock the origin down with
  network policy / SSO if you need finer access control.
- The proxy strips `X-API-Key`, `Authorization`, `X-OpenViking-Account`, and
  `X-OpenViking-User` from incoming requests so a malicious client can't pass
  alternative credentials downstream.

## Why not nginx?

The nginx layout in the main README is still the right answer when you already
have nginx in front. This Node script is for deployments that want the
"static frontend + thin proxy" pattern as a single self-contained process —
for example Render / Railway / Fly app instances or local demos.
