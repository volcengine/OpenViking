# Metrics

OpenViking exposes a `/metrics` endpoint for Prometheus, Grafana Agent, and other monitoring systems that scrape Prometheus exposition text.

It returns Prometheus text for scheduled collection: runtime health and service-quality signals, not business analytics. For a component status snapshot in JSON, use `/api/v1/observer/*`.

## API Reference

### metrics()

Export Prometheus metrics for the current process.

This endpoint is typically scraped by Prometheus on a schedule, but it is also useful for local debugging and manual inspection.

**Authentication**

The server does not authenticate `/metrics` requests. If your gateway or reverse proxy restricts access, configure the scraper with that layer’s credentials.

**HTTP API**

```
GET /metrics
```

```bash
curl -X GET http://localhost:1933/metrics
```

If your deployment requires gateway- or proxy-level authentication, attach the required headers there, for example:

```bash
curl -X GET http://localhost:1933/metrics \
  -H "Authorization: Bearer your-key"
```

**Response Format**

On success, the endpoint returns `text/plain; version=0.0.4; charset=utf-8` with Prometheus exposition text, for example:

```text
# HELP openviking_http_requests_total Total number of HTTP requests
# TYPE openviking_http_requests_total counter
openviking_http_requests_total{method="GET",route="/api/v1/system/status",status="200"} 12

# HELP openviking_http_inflight_requests Number of inflight HTTP requests
# TYPE openviking_http_inflight_requests gauge
openviking_http_inflight_requests{route="/api/v1/system/status"} 0
```

When the metrics system is disabled, the endpoint returns:

- HTTP status code: `404`
- Response body:

```text
Prometheus metrics are disabled.
```

**Example: Prometheus Scrape Config**

```yaml
scrape_configs:
  - job_name: openviking
    metrics_path: /metrics
    static_configs:
      - targets: ["localhost:1933"]
```

If your deployment protects `/metrics` at the gateway layer, configure the scrape job with the required auth settings through your proxy, service discovery, or Prometheus auth options.

**Notes**

- `/metrics` is meant for frequent scraping, so the exported metrics should remain low-cardinality and low-cost.
- `/metrics` returns Prometheus text, not the standard OpenViking `{status, result}` JSON response format.
- For human-readable component snapshots, prefer `/api/v1/observer/*`.
- `/metrics` also includes VikingBot feedback observability metrics derived from scrape-time aggregation of persisted session data; see the Metrics concept documentation for the feedback metric families and examples.

## Embedding usage observations

Concrete embedding entrypoints publish one content-free `embedding.call` observation per existing operation through the shared event bus. It includes `attempt_id`, `outcome`, `observation_scope`, nullable token counts, `usage_known_fields`, `usage_complete`, `usage_quality`, and `usage_provenance`. Retries settle separately; cancellation while waiting for document admission does not represent a provider attempt.

`embedding_operation` describes the adapter operation, including local inference; it is not a physical HTTP request count. Ark uses `provider_attempt` for each SDK invocation, so multimodal hybrid's two requests remain separate. An SDK invocation may fail before sending data, and these events are not a complete billing ledger. Existing query admission, retries, and model outputs are unchanged.

Missing usage stays `null`; explicit zero stays zero. Embedding totals are not converted into generated output tokens when input usage is missing. Character estimates remain available as `usage_quality="estimated"` with `usage_provenance="character_estimate"`; provider-reported input takes precedence where supported. Estimates are excluded from `openviking_embedding_tokens_*` and ingestion token counters, which now contain reported known values. `openviking_embedding_usage_reports_total{quality="actual|estimated|unknown"}` exposes the observation quality. Total counters require both input and output to be known. Historical pull-model counters retain known and estimated subtotals for compatibility and must not be treated as complete bills or added to event counters.

No prompts, response bodies, keys, raw errors, or source document content are added to observations. This change does not introduce cross-request batching or allocate one operation's usage across different subjects.

## Related Documentation

- [Metrics](../concepts/12-metrics.md) - Metric families, labels, feedback metrics, and PromQL examples
- [VikingBot Feedback Observability Design](https://github.com/volcengine/OpenViking/blob/main/bot/docs/zh/design/vikingbot-feedback-observability-design.md) - feedback observability design background and rollout plan (Chinese)
- [System and Monitoring](07-system.md) - Health checks, system status, and Observer APIs
- [API Overview](01-overview.md) - Shared conventions for all API endpoints
