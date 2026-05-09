# Metrics

OpenViking exposes a `/metrics` endpoint for Prometheus, Grafana Agent, and other monitoring systems that scrape Prometheus exposition text.

Unlike `/api/v1/observer/*` and `/api/v1/stats/*`, `/metrics` is intended for:

- machine scraping rather than human-oriented inspection
- Prometheus exposition text rather than the standard JSON API wrapper
- runtime health and service quality signals rather than business analytics

## API Reference

### metrics()

Export Prometheus metrics for the current process.

This endpoint is typically scraped by Prometheus on a schedule, but it is also useful for local debugging and manual inspection.

**Authentication**

- In the current implementation, `/metrics` is not wired to `get_request_context` or other auth dependencies.
- In other words, based on the current server code, `/metrics` is effectively a public scrape endpoint.
- If access control is tightened later through a gateway, reverse proxy, or deployment policy, follow the actual deployment configuration.

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
- `/metrics` returns Prometheus text, not the standard OpenViking `{status, result, time}` JSON response format.
- Analytics-oriented statistics such as memory health, staleness, or session extraction should remain under `/api/v1/stats/*`, not `/metrics`.
- For human-readable component snapshots, prefer `/api/v1/observer/*`.

## Related Documentation

- [System and Monitoring](07-system.md) - Health checks, system status, and Observer APIs
- [API Overview](01-overview.md) - Shared conventions for all API endpoints
