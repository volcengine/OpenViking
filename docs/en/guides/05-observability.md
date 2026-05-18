# Observability & Diagnostics

This guide collects the current OpenViking observability entry points in one place, including:

- service health and component status
- request-level `telemetry`
- terminal-side `ov tui`
- web-side `OpenViking Console`
- `/metrics` time-series metrics

If you just want to know where to look first, start with the table below.

## Choose the right entry point

| Entry point | Best for | Typical use case |
| --- | --- | --- |
| `/health`, `observer/*` | service health, queue backlog, VikingDB and VLM status | deployment validation, on-call checks |
| `ov tui` | `viking://` trees, directory summaries, file content, vector records, image preview for supported image files | development debugging, verifying that data actually landed |
| `OpenViking Console` | web UI for browsing, search, resource import, tenants, and system state | interactive investigation without typing every command |
| `telemetry` | per-request duration, token usage, vector retrieval, ingestion stages | debugging one specific slow or unexpected call |
| `/metrics` | request trends, error rates, latency distribution, queue and probe state | Prometheus scraping, Grafana dashboards, alert rules |

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
2. Confirm the right-side panel shows `abstract`, `overview`, or file content (supported image files — `png`, `jpg`, `jpeg`, `gif`, `bmp`, `webp`, `tiff`, `tif` — are rendered inline as a preview).
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

## Use `/metrics` for time-series observability

`/metrics` is OpenViking's time-series metrics endpoint for the Prometheus scraping model. It is well suited for questions like:

- Has HTTP traffic increased abnormally over the last few minutes?
- Is the error rate for a route or operation continuing to rise?
- Is latency distribution getting worse?
- Is queue backlog starting to build up?
- Have key dependencies, probes, or model providers become unhealthy?

Compared with `observer/*`, `/metrics` is better for **trends, aggregation, and alerting**. `observer/*` is better for inspecting the current point-in-time state by hand.

Compared with `telemetry`, `/metrics` focuses on **aggregated time series**, while `telemetry` focuses on **what happened inside one specific request**.

### Enable metrics quickly

`/metrics` may be disabled by default. When the metrics subsystem is not enabled, the endpoint returns `404` with the message `Prometheus metrics are disabled.`.

You do not need the full configuration to get started. Enabling the master switch under the `server` section is enough.

**Minimal config (recommended)**

Add the following to `~/.openviking/ov.conf` (or the path passed via `--config`):

```json
{
  "server": {
    "observability": {
      "metrics": {
        "enabled": true
      }
    }
  }
}
```

Restart OpenViking Server after editing the config.

### Observability config hierarchy

OpenViking groups signal-level observability configuration under `server.observability`:

- `server.observability.metrics`: metrics subsystem and exporters
- `server.observability.traces`: trace export configuration
- `server.observability.logs`: log export configuration
- `server.observability.dump_body`: attaches HTTP request/response bodies (filtered by content-type, truncated by bytes) as attributes on the active trace span so they can be inspected in trace UIs. Off by default — bodies may contain secrets and high-cardinality content

Example:

```json
{
  "server": {
    "observability": {
      "metrics": {
        "enabled": true,
        "exporters": {
          "prometheus": {
            "enabled": true
          },
          "otel": {
            "enabled": true,
            "protocol": "grpc",
            "tls": {
              "insecure": true
            },
            "endpoint": "otel-collector:4317",
            "service_name": "openviking-server",
            "export_interval_ms": 10000,
            "headers": {}
          }
        }
      },
      "traces": {
        "enabled": true,
        "protocol": "grpc",
        "tls": {
          "insecure": true
        },
        "endpoint": "otel-collector:4317",
        "service_name": "openviking-server",
        "headers": {}
      },
      "logs": {
        "enabled": true,
        "protocol": "grpc",
        "tls": {
          "insecure": true
        },
        "endpoint": "otel-collector:4317",
        "service_name": "openviking-server",
        "headers": {}
      },
      "dump_body": {
        "enabled": false,
        "max_bytes": 4096
      }
    }
  }
}
```

Notes:

- `headers` forwards custom OTLP request headers or gRPC metadata to the exporter.
- This is useful when an OTLP backend requires extra auth headers for direct ingestion.
- The `headers` shape is the same across `traces`, `logs`, and `metrics.exporters.otel`.
- When `protocol="grpc"`, `headers` are sent as gRPC metadata and keys should be lowercase, for example `x-byteapm-appkey`; this restriction does not apply to `protocol="http"`.

For full fields, supported ranges, and more examples, see:

- [Metrics](../concepts/12-metrics.md)

### Access `/metrics` directly

In the current implementation, `/metrics` is not wired to `get_request_context` or other auth dependencies, so from the code-path perspective it currently behaves as a public scrape endpoint:

```bash
curl http://localhost:1933/metrics
```

If your deployment protects `/metrics` at the gateway, reverse proxy, or service discovery layer, attach auth according to the deployment environment.

### Prometheus scrape example

The most common setup is to let Prometheus scrape it on a schedule:

```yaml
scrape_configs:
  - job_name: openviking
    metrics_path: /metrics
    static_configs:
      - targets: ["localhost:1933"]
```

### Import and view the dashboard in Grafana

Once Prometheus is successfully scraping `/metrics`, the next common step is to import the OpenViking demo dashboard into Grafana.

**Step 1: Confirm that Prometheus is already scraping `/metrics`**

Before importing the dashboard, make sure the Prometheus data source can already query OpenViking metrics. The quickest checks are:

- run `openviking_http_requests_total` in the Prometheus UI
- or run `openviking_service_readiness`
- if either query returns time series, Grafana should be able to render panels afterwards

If there is no data yet, go back to the Prometheus scrape configuration above and verify `targets`, `metrics_path`, and network connectivity first.

**Step 2: Import the official demo dashboard into Grafana**

The OpenViking repository already includes ready-to-import dashboard JSON:

- [openviking_demo_dashboard.json](../../../examples/grafana/openviking_demo_dashboard.json)
- [openviking_token_demo_dashboard.json](../../../examples/grafana/openviking_token_demo_dashboard.json) (Note: this dashboard depends on the `tim012432-calendarheatmap-panel` Grafana plugin. Install it before importing to ensure panels render correctly.)

You can import it with the following steps:

1. Sign in to Grafana.
2. Open `Dashboards` from the left-side menu.
3. Click `New` or `Import` in the top-right corner.
4. Upload the JSON file, or paste the contents of the linked file.
5. Select Prometheus as the data source on the import screen.
6. Click `Import` to finish.

If the dashboard imports but panels are empty, the first two things to verify are:

- whether Grafana is bound to the correct Prometheus data source
- whether Prometheus is actually scraping `openviking_*` metrics

**Step 3: What to look at after the dashboard opens**

After import, the most useful panels usually come from these metric families:

- `openviking_http_*`: HTTP request volume, latency, and inflight requests
- `openviking_operation_*`: structured operation success rates and latency
- `openviking_queue_*`: queue throughput, backlog, and in-progress work
- `openviking_*_readiness`: dependency and probe health state

A beginner-friendly viewing order is:

1. Start with readiness and health panels to confirm that the system and dependencies are generally healthy.
2. Move to HTTP and operation panels to see whether traffic, error rates, or latency have changed.
3. Then inspect queue panels to determine whether async work is backing up.
4. Finally, narrow the scope with labels such as `account_id`, `route`, and `status`.

**Step 4: What the final result looks like**

After a successful import, you should see a dashboard centered on OpenViking requests, queues, probes, model calls, and overall system state. For a visual reference, see:

- [grafana-demo-dashboard.png](../../images/grafana-demo-dashboard.png)

This screenshot helps you quickly verify whether the imported layout looks correct. If the dashboard structure matches but some panels are empty, it usually means the corresponding metrics have not produced samples yet, or the filters do not match the current traffic.

### Understanding common labels

The most common labels you will use while investigating dashboards are:

- `account_id`: tenant dimension label. It is only enabled on controlled allowlisted metric families. Unidentified requests fall into `__unknown__`, and values beyond the active-tenant budget fall into `__overflow__`
- `route`: HTTP route template, for example `/api/v1/search/find`
- `status`: request or stage status, such as `200`, `ok`, or `error`
- `valid`: whether the sample came from a successful refresh; `valid="0"` usually indicates a fallback or stale sample

### When to use `/metrics` vs. other entry points

- To check whether the service is healthy and which component is currently unhealthy, start with `/health` and `observer/*`
- To verify whether resources really landed and vectors were actually written, use `ov tui`
- To inspect why one specific request was slow, where tokens went, or which stage blocked resource processing, use `telemetry`
- To inspect whether request volume, error rates, or latency are degrading over time, use `/metrics`

## Related Documentation

- [Deployment](03-deployment.md) - server setup
- [Authentication](04-authentication.md) - API key setup
- [Operation Telemetry Reference](07-operation-telemetry.md) - request-level structured tracing
- [System API](../api/07-system.md) - system and observer API reference
- [Metrics](../concepts/12-metrics.md) - time-series metrics and configuration
