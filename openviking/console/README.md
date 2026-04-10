# OpenViking Console

This is a standalone console service.
It is not wired into release packaging or CLI commands.

## What it provides

- File system browsing (`ls/read/stat`)
- Find query
- Add resource (`/api/v1/resources`)
- Tenant/account management UI
- System/observer status panels

## Quick start

1. Start OpenViking server (default: `http://127.0.0.1:1933`)
2. Start the console service:

```bash
python -m openviking.console.bootstrap \
  --host 127.0.0.1 \
  --port 8020 \
  --openviking-url http://127.0.0.1:1933
```

3. Open:

```text
http://127.0.0.1:8020/
```

4. In **Settings**, configure headers for your upstream auth mode.
`api_key` is the default server mode, so in that mode you normally paste `X-API-Key` and click **Save** (or press Enter). If the upstream server runs in `trusted` mode, you can omit `X-API-Key` for ordinary requests only when that server is localhost-only and has no `root_api_key`; otherwise you still need `X-API-Key`, and you should also set `X-OpenViking-Account` and `X-OpenViking-User` (and optionally `X-OpenViking-Agent`).
`X-API-Key` is stored locally in the browser and restored into the current tab.

When the upstream server runs in `trusted` mode, ordinary access does not require user registration first. If you try account or user management actions against Admin API endpoints in `trusted` mode, the server now returns an explicit error explaining that `trusted` mode resolves requests as `USER` and that account/user management requires `api_key` mode with `root_api_key`.

## Startup parameters

- `--openviking-url` (default `http://127.0.0.1:1933`)
- `--host` (default `127.0.0.1`)
- `--port` (default `8020`)
- `--write-enabled` (default `false`)
- `--request-timeout-sec` (default `30`)
- `--cors-origins` (default `*`, comma-separated)

Without `--write-enabled`, write operations are blocked by backend guardrails.
If you need **Add Resource** or **multi-tenant management** (create/delete account, add/delete user, role/key changes),
start with `--write-enabled`.

Example:

```bash
python -m openviking.console.bootstrap \
  --host 127.0.0.1 \
  --port 8020 \
  --openviking-url http://127.0.0.1:1933 \
  --write-enabled
```
