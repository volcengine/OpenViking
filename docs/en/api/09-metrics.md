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

## Related Documentation

- [Metrics](../concepts/12-metrics.md) - Metric families, labels, feedback metrics, and PromQL examples
- [VikingBot Feedback Observability Design](https://github.com/volcengine/OpenViking/blob/main/bot/docs/zh/design/vikingbot-feedback-observability-design.md) - feedback observability design background and rollout plan (Chinese)
- [System and Monitoring](07-system.md) - Health checks, system status, and Observer APIs
- [API Overview](01-overview.md) - Shared conventions for all API endpoints

## Reported VLM cache and reasoning subsets

The official `vlm.call` event carries nullable `prompt_cached_tokens` and
`completion_reasoning_tokens`. A missing/invalid field stays `null`; reported
zero remains zero. OpenAI, LiteLLM, VolcEngine Chat Completions and VolcEngine
media usage use the same normalizer. The [DeepSeek Chat Completions contract](https://api-docs.deepseek.com/api/create-chat-completion/)
defines `prompt_cache_hit_tokens` as the same fact as
`prompt_tokens_details.cached_tokens`; it is accepted as an alias, never added
to that field. Conflicting aliases, non-integers, negatives and values larger
than their input/output parent remain unknown. Cache misses and cache writes
are not cache hits.

The existing VLM collector exports three additive metric families with
`provider`, `model_name`, and a bounded `subset` label (`cached_prompt` or
`reasoning_completion`):

| Metric | Meaning |
| --- | --- |
| `openviking_vlm_usage_subset_tokens_total` | Reported subset tokens |
| `openviking_vlm_usage_subset_parent_tokens_total` | Input/output tokens from only the calls reporting that subset |
| `openviking_vlm_usage_subset_reports_total` | Successful emitted call events with a valid report, including zero |

Existing input/output/total counters retain their meaning. **Do not add subset
counters to total tokens.** Unknown subset fields do not create a report.
Failed events do not add token or subset reports. Zero amounts do not increment
the registry's strictly positive counters; the report counter preserves the
distinction between reported zero and missing evidence.

For a cache ratio among reporting calls (with a positive parent denominator):

```promql
(
  rate(openviking_vlm_usage_subset_tokens_total{subset="cached_prompt"}[5m])
  or
  0 * rate(openviking_vlm_usage_subset_reports_total{subset="cached_prompt"}[5m])
)
/
rate(openviking_vlm_usage_subset_parent_tokens_total{subset="cached_prompt"}[5m])
```

The report population is important: including prompt tokens from calls that
never reported cache usage would understate their hit ratio. Only use zero
fallback where a report exists. Reporting coverage can be compared with
successful emitted VLM call counts using matching provider/model/account
filters; it is not coverage of all physical RPCs. Existing best-effort event
delivery, provider usage availability, process restart and scrape gaps remain
limits. These counters are not a retained receipt ledger or complete bill,
and do not add workflow/performance Console APIs or UI.

When account metrics are enabled, these families follow the existing account
allowlist and cardinality policy (for example `openviking_vlm_*`). No prompt,
response, task ID or new unbounded label is recorded.
