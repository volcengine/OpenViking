# Observability & Diagnostics

This guide collects the current OpenViking observability entry points in one place, including:

- service health and component status
- request-level `telemetry`
- terminal-side `ov tui`
- web-side `OpenViking Console`

If you just want to know where to look first, start with the table below.

## Choose the right entry point

| Entry point | Best for | Typical use case |
| --- | --- | --- |
| `/health`, `observer/*` | service health, queue backlog, VikingDB and VLM status | deployment validation, on-call checks |
| `ov tui` | `viking://` trees, directory summaries, file content, vector records | development debugging, verifying that data actually landed |
| `OpenViking Console` | web UI for browsing, search, resource import, tenants, and system state | interactive investigation without typing every command |
| `telemetry` | per-request duration, token usage, vector retrieval, ingestion stages | debugging one specific slow or unexpected call |

## Service health and component status

### Health check

`/health` provides a simple liveness check and does not require authentication.

```bash
curl http://localhost:1933/health
```

```json
{"status": "ok"}
```

### Overall system status

**Python SDK (Embedded / HTTP)**

```python
status = client.get_status()
print(f"Healthy: {status['is_healthy']}")
print(f"Errors: {status['errors']}")
```

**HTTP API**

```bash
curl http://localhost:1933/api/v1/observer/system \
  -H "X-API-Key: your-key"
```

```json
{
  "status": "ok",
  "result": {
    "is_healthy": true,
    "errors": [],
    "components": {
      "queue": {"name": "queue", "is_healthy": true, "has_errors": false},
      "vikingdb": {"name": "vikingdb", "is_healthy": true, "has_errors": false},
      "vlm": {"name": "vlm", "is_healthy": true, "has_errors": false}
    }
  }
}
```

### Component status

| Endpoint | Component | Description |
| --- | --- | --- |
| `GET /api/v1/observer/queue` | Queue | Processing queue status |
| `GET /api/v1/observer/vikingdb` | VikingDB | Vector database status |
| `GET /api/v1/observer/vlm` | VLM | Vision Language Model status |

For example:

```bash
curl http://localhost:1933/api/v1/observer/queue \
  -H "X-API-Key: your-key"
```

### Quick health check

**Python SDK (Embedded / HTTP)**

```python
if client.is_healthy():
    print("System OK")
```

**HTTP API**

```bash
curl http://localhost:1933/api/v1/debug/health \
  -H "X-API-Key: your-key"
```

```json
{"status": "ok", "result": {"healthy": true}}
```

### Response time

Every API response includes an `X-Process-Time` header with the server-side processing time in seconds:

```bash
curl -v http://localhost:1933/api/v1/fs/ls?uri=viking:// \
  -H "X-API-Key: your-key" 2>&1 | grep X-Process-Time
# < X-Process-Time: 0.0023
```

This layer answers "is the service up, blocked, or unhealthy?" If you want to inspect what happened inside one request, move on to telemetry.

## Use `ov tui` for data-plane inspection

The `ov` CLI includes a dedicated TUI file explorer:

```bash
ov tui /
```

You can also start from a specific scope:

```bash
ov tui viking://resources
```

Prerequisites:

- OpenViking Server is running
- `ovcli.conf` is configured
- the current `X-API-Key` can read the target tenant data

This TUI is useful for two kinds of inspection:

- checking what actually exists under `viking://resources`, `viking://user`, `viking://agent`, and `viking://session`
- checking whether vector records for a URI were actually written, and how many there are

Common keys:

- `q`: quit
- `Tab`: switch focus between the tree and content panels
- `j` / `k`: move up and down
- `.`: expand or collapse a directory
- `g` / `G`: jump to the top or bottom
- `v`: toggle vector-record view
- `n`: load the next page in vector-record view
- `c`: count total vector records for the current URI

A typical debugging flow is:

1. Run `ov tui viking://resources` and locate the target document or directory.
2. Confirm the right-side panel shows `abstract`, `overview`, or file content.
3. Press `v` to inspect vector records for that URI.
4. Press `c` to get the total count, and `n` to keep paging if needed.

TUI is primarily for data-plane inspection. It helps answer "did the resource really land?" and "were vectors really written?" but it does not directly show token totals or per-stage request timing.

## Use OpenViking Console for web-based investigation

The repo also contains a standalone web console. It is not wired into the main CLI and must be started separately:

```bash
python -m openviking.console.bootstrap \
  --host 127.0.0.1 \
  --port 8020 \
  --openviking-url http://127.0.0.1:1933
```

Then open:

```text
http://127.0.0.1:8020/
```

On first use, go to `Settings` and set your `X-API-Key`.

The most useful panels for observability are:

- `FileSystem`: browse URIs, directories, and files
- `Find`: run retrieval requests and inspect results
- `Add Resource`: import resources and inspect responses
- `Add Memory`: submit content through a session commit and inspect the memory flow
- `Tenants` / `Monitor`: inspect tenant, user, and system state

If you need write operations such as `Add Resource`, `Add Memory`, or tenant/user administration, start the console with `--write-enabled`:

```bash
python -m openviking.console.bootstrap \
  --host 127.0.0.1 \
  --port 8020 \
  --openviking-url http://127.0.0.1:1933 \
  --write-enabled
```

From an observability standpoint, one useful detail is that the console result panel shows raw API responses. For operations such as `find`, `add-resource`, and `session commit`, the proxy layer requests `telemetry` by default, so you can usually inspect `telemetry.summary` directly in the UI.

Console is best for interactive click-through debugging. If you need to feed observability data into your own logs or automation, prefer the HTTP API or SDK and request telemetry explicitly.

## Request-level telemetry

The public request-tracing feature in OpenViking is called `operation telemetry`. It attaches a structured summary to a response so you can inspect things like:

- total duration
- LLM and embedding token usage
- vector search counts, scan volume, and returned results
- resource-ingestion stages
- memory extraction stats for `session.commit`

The most common way to request it is to pass:

```json
{"telemetry": true}
```

For example:

```bash
curl -X POST http://localhost:1933/api/v1/search/find \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "query": "memory dedup",
    "limit": 5,
    "telemetry": true
  }'
```

For the full field reference, supported operations, and more examples, see:

- [Operation Telemetry Reference](07-operation-telemetry.md)

## Related Documentation

- [Deployment](03-deployment.md) - server setup
- [Authentication](04-authentication.md) - API key setup
- [Operation Telemetry Reference](07-operation-telemetry.md) - request-level structured tracing
- [System API](../api/07-system.md) - system and observer API reference
