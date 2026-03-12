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

4. In **Settings**, paste your OpenViking `X-API-Key` and click **Save** (or press Enter).
`X-API-Key` is configured in the web UI Settings panel and stored in browser session storage.

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
