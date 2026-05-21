# OpenViking Web Studio

English / [中文](README_CN.md)

Web Studio is the React/Vite frontend workspace for OpenViking. It is a static single page application for resource management, retrieval, bot-backed sessions, and operational diagnostics.

Web Studio does not embed OpenViking storage, indexing, retrieval, task queues, or VikingBot runtime. It must connect to a running OpenViking Server.

## Runtime Contract

Default local server URL:

```text
http://127.0.0.1:1933
```

The sessions UI requires VikingBot endpoints proxied by OpenViking Server:

```text
GET  /bot/v1/health
POST /bot/v1/chat
POST /bot/v1/chat/stream
POST /bot/v1/feedback
```

For local development and deployment, start OpenViking Server with bot support:

```bash
openviking-server --with-bot
```

Without `--with-bot`, core APIs such as resources, search, tasks, and system status may still work, but `/bot/v1/*` returns `503`; the sessions page cannot provide real chat behavior.

## Quick Start

### 1. Start the Server

From the repository root:

```bash
uv pip install -e ".[bot,dev]"
openviking-server init
openviking-server doctor
openviking-server --with-bot
```

Or from the published package:

```bash
pip install "openviking[bot]"
openviking-server init
openviking-server doctor
openviking-server --with-bot
```

Check the required server surface:

```bash
curl http://127.0.0.1:1933/health
curl http://127.0.0.1:1933/ready
curl http://127.0.0.1:1933/bot/v1/health
```

### 2. Start Web Studio

```bash
cd web-studio
npm install
npm run dev
```

Open:

```text
http://127.0.0.1:3000
```

To override the initial server URL:

```bash
VITE_OV_BASE_URL=http://127.0.0.1:1933 npm run dev
```

The connection dialog can still override the server URL, API key, account ID, and user ID at runtime.

## Connection and Auth

Application code should use the adapter under `src/lib/ov-client` instead of importing from `src/gen/ov-client` directly. The adapter centralizes base URL handling, auth headers, telemetry defaults, and error normalization.

Browser storage:

| Value                         | Storage          | Key                     |
| ----------------------------- | ---------------- | ----------------------- |
| API key                       | `sessionStorage` | `ov_console_api_key`    |
| Base URL, account ID, user ID | `localStorage`   | `ov_console_connection` |

Request headers injected by the adapter:

- `X-API-Key`
- `X-OpenViking-Account`
- `X-OpenViking-User`
- `X-OpenViking-Agent: web-studio`

For production or multi-tenant deployments, configure a real `server.root_api_key` or user key in OpenViking Server and enter the matching connection settings in Web Studio.

## Commands

| Command                     | Purpose                                             |
| --------------------------- | --------------------------------------------------- |
| `npm run dev`               | Start the Vite dev server on port 3000.             |
| `npm run build`             | Build the static production bundle into `dist/`.    |
| `npm run preview`           | Preview the built `dist/` bundle locally.           |
| `npm run lint`              | Run ESLint for the current business-code scope.     |
| `npm run format`            | Check formatting with Prettier.                     |
| `npm run check`             | Run Prettier write mode and ESLint autofix.         |
| `npm run test`              | Run Vitest.                                         |
| `npm run gen-server-client` | Regenerate `src/gen/ov-client` from server OpenAPI. |

## Generated OpenAPI Client

Generated code lives under:

```text
src/gen/ov-client
```

Do not edit generated files by hand. Regenerate them from the target OpenViking Server version:

```bash
openviking-server --with-bot
cd web-studio
npm run gen-server-client
```

The generation script currently reads:

```text
http://127.0.0.1:1933/openapi.json
```

It formats the OpenAPI document, normalizes operation IDs, and runs `@hey-api/openapi-ts`.

## Project Layout

```text
src/routes/              TanStack Router routes
src/routes/<page>/       Top-level page modules
src/routes/<page>/-*     Page-private components, hooks, schemas, and helpers
src/components/ui/       Shared base UI primitives
src/components/          Shared app components
src/hooks/               Shared React hooks
src/lib/ov-client/       Runtime OpenViking client adapter
src/gen/ov-client/       Generated OpenAPI client
src/i18n/locales/        en and zh-CN translation resources
src/styles.css           Global CSS and design tokens
types/ov-server/         Supplemental typed server-result subsets
```

Keep route-specific implementation colocated under the corresponding route directory. User-visible copy belongs in both `src/i18n/locales/en.ts` and `src/i18n/locales/zh-CN.ts`.

## Deployment

Web Studio deploys as static files from `dist/`. OpenViking Server remains a separate runtime dependency.

### 1. Start the Required Server

Production-like example:

```bash
openviking-server --host 0.0.0.0 --port 1933 --with-bot
```

Production deployments should configure `server.root_api_key` in `ov.conf`. If Web Studio and OpenViking Server are served from different origins, include the Web Studio origin in `server.cors_origins`.

Minimum health checks:

```bash
curl https://ov-api.example.com/health
curl https://ov-api.example.com/ready
curl https://ov-api.example.com/bot/v1/health
```

`/bot/v1/health` is part of the Web Studio deployment contract. A healthy core server without a healthy bot proxy is not enough for the sessions UI.

### 2. Build Static Files

For a separate frontend host:

```bash
cd web-studio
npm ci
VITE_OV_BASE_URL=https://ov-api.example.com npm run build
```

`VITE_OV_BASE_URL` is the initial OpenViking API origin used in the browser. Users can still change it in the connection dialog.

### 3. Serve from a Dedicated Host

Example URL:

```text
https://web-studio.example.com/
```

Minimal nginx example:

```nginx
server {
    listen 80;
    server_name web-studio.example.com;

    root /srv/web-studio/dist;
    index index.html;

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

### 4. Serve from the Same Host Root

Example URL:

```text
https://ov.example.com/
```

Proxy OpenViking API paths to the server and serve Web Studio at `/`:

```nginx
server {
    listen 80;
    server_name ov.example.com;

    root /srv/web-studio/dist;
    index index.html;

    location /api/ {
        proxy_pass http://127.0.0.1:1933;
    }

    location /bot/ {
        proxy_pass http://127.0.0.1:1933;
    }

    location /health {
        proxy_pass http://127.0.0.1:1933;
    }

    location /ready {
        proxy_pass http://127.0.0.1:1933;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

Build with:

```bash
VITE_OV_BASE_URL=https://ov.example.com npm run build
```

### 5. Serve from a Same-Host Subpath

Example URL:

```text
https://ov.example.com/web-studio/
```

In this layout, Web Studio is mounted under `/web-studio/`, while OpenViking API paths stay at the host root:

```text
https://ov.example.com/api/*
https://ov.example.com/bot/*
https://ov.example.com/health
https://ov.example.com/ready
```

Build with both values:

```bash
cd web-studio
npm ci
VITE_OV_BASE_URL=https://ov.example.com npm run build -- --base=/web-studio/
```

Meaning:

- `VITE_OV_BASE_URL=https://ov.example.com`: API origin used by browser requests.
- `--base=/web-studio/`: Vite public asset base and TanStack Router mount path.

Publish `dist/` to:

```text
/srv/web-studio
```

nginx example:

```nginx
server {
    listen 80;
    server_name ov.example.com;

    root /srv;

    location = /web-studio {
        return 301 /web-studio/;
    }

    location /web-studio/ {
        try_files $uri $uri/ /web-studio/index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:1933;
    }

    location /bot/ {
        proxy_pass http://127.0.0.1:1933;
    }

    location /health {
        proxy_pass http://127.0.0.1:1933;
    }

    location /ready {
        proxy_pass http://127.0.0.1:1933;
    }
}
```

Do not set `VITE_OV_BASE_URL` to `https://ov.example.com/web-studio`. `/web-studio/` is only the frontend mount path; OpenViking API requests should still go to `https://ov.example.com/api/*` and `https://ov.example.com/bot/*`.

### 6. Docker Server Dependency

The official OpenViking image can be used as the API server dependency:

```bash
docker run -d \
  --name openviking \
  -p 1933:1933 \
  -p 8020:8020 \
  -v ~/.openviking:/app/.openviking \
  --restart unless-stopped \
  ghcr.io/volcengine/openviking:latest
```

The image starts VikingBot by default. Do not pass `--without-bot` and do not set `OPENVIKING_WITH_BOT=0` for a Web Studio deployment that needs sessions/chat.

Web Studio static files are still built and hosted separately unless your deployment image or platform explicitly bundles `web-studio/dist`.

## Troubleshooting

### `/bot/v1/*` Returns 503

The server was not started with `--with-bot`, or the VikingBot gateway failed to start. Install bot dependencies and restart:

```bash
uv pip install -e ".[bot,dev]"
openviking-server --with-bot
```

Check server logs for `Bot API proxy enabled`.

### Client Generation Cannot Fetch OpenAPI

`npm run gen-server-client` reads `http://127.0.0.1:1933/openapi.json`. Start a local server first and use the same server version you plan to target at runtime.

### Browser Shows CORS Errors

If Web Studio and OpenViking Server use different origins, add the Web Studio origin to `server.cors_origins` in `ov.conf` and restart the server. For same-origin deployment, proxy `/api/`, `/bot/`, `/health`, and `/ready` to OpenViking Server.

### Connection Dialog Keeps Reopening

The API key is missing or invalid, the key belongs to a different server, or the selected account/user does not match the key scope. Verify the same server URL and key with a direct API request, then update the Web Studio connection settings.

## Related Docs

- [OpenViking server deployment](../docs/en/guides/03-deployment.md): server-side deployment details.
- [VikingBot validation with OpenViking Server](../bot/docs/vikingbot-phase1-validation-with-openviking-server.md): bot proxy validation flow.
