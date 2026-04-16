# Metrics

OpenViking provides a machine-oriented metrics system for exposing runtime health, request quality, model usage, resource processing throughput, and probe health states.

Unlike the human-facing `/api/v1/observer/*` endpoints and the analytics-oriented `/api/v1/stats/*` endpoints, Metrics are designed for:

- high-frequency scraping by Prometheus, Grafana Agent, and similar systems
- low-cardinality, aggregatable metric models
- monitoring, alerting, capacity observation, and regression diagnosis

## Overview

### Why Metrics

Metrics are well suited to answer questions like:

- Has HTTP traffic increased abnormally over the last few minutes?
- Are resource ingestion, retrieval, or model calls getting slower?
- Is there queue backlog?
- Are key dependencies such as storage, model providers, VikingDB, encryption, and async systems currently healthy?
- Is a specific tenant showing abnormal traffic or error rates?

Compared with logs and observer snapshots, metrics are better for:

- continuous scraping
- time-series aggregation
- dashboard visualization
- alert rules

### How Metrics Differ from Observer and Stats

| Capability | Best For | Output Format | Typical Usage |
|------------|----------|---------------|---------------|
| `/metrics` | online monitoring, alerting, trend aggregation | Prometheus exposition text | Grafana dashboards, Prometheus scraping |
| `/api/v1/observer/*` | human inspection of component snapshots | JSON / status tables | debugging, health checks |
| `/api/v1/stats/*` | analytics-oriented statistics | JSON | memory health, staleness, session extraction |

The boundary is:

- `/metrics` only carries **low-cardinality, low-cost** metrics
- `/api/v1/stats/*` continues to carry analytics-oriented statistics without being constrained by the Prometheus scraping model

## Metrics Architecture

The current metrics stack in OpenViking has four layers:

```text
Business logic / HTTP requests / background tasks
          │
          ▼
      DataSource
   (event emission / state reads)
          │
          ▼
      Collector
 (semantic routing + labels)
          │
          ▼
    MetricRegistry
   (in-process metric store)
          │
          ▼
      Exporter
 (Prometheus text rendering)
          │
          ▼
       /metrics
```

### DataSource

DataSources provide inputs to the metrics system in two main forms:

- **Event-based**: business code emits events at key points, such as retrieval completion, successful model calls, or resource ingestion stage completion
- **Read-based**: current state is read before `/metrics` export, such as queue state, lock state, or probe state

### Collector

Collectors turn inputs into metric semantics:

- choose which metric to write
- choose which labels to attach
- define how failure is exposed, such as `valid=1/0`

### MetricRegistry

The MetricRegistry is the in-process metric store that keeps the current metric values and serves them to the exporter.

### Exporter

The first exporter implementation is the Prometheus exporter, which renders registry contents into Prometheus exposition text.

## Usage

### Accessing `/metrics`

In the current implementation, `/metrics` is not wired to `get_request_context` or other auth dependencies, so from the code-path perspective it currently behaves as a public scrape endpoint.

```bash
curl http://localhost:1933/metrics
```

If your deployment protects `/metrics` at the gateway, reverse proxy, or service discovery layer, attach auth according to the deployment environment.

### Prometheus Scrape Example

```yaml
scrape_configs:
  - job_name: openviking
    metrics_path: /metrics
    static_configs:
      - targets: ["localhost:1933"]
```

### Understanding Common Labels

| Label | Meaning | Example |
|-------|---------|---------|
| `account_id` | tenant dimension label | `test-account`, `__unknown__`, `__overflow__` |
| `route` | HTTP route template | `/api/v1/search/find` |
| `method` | HTTP method | `GET`, `POST` |
| `status` | request or stage status | `200`, `ok`, `error` |
| `operation` | structured operation name | `search.find`, `resources.add_resource` |
| `context_type` | retrieval context type | `resource` |
| `provider` | model or external service provider | `volcengine` |
| `model_name` | model name | `doubao-seed-1-8-251228` |
| `stage` | stage label (defined by each metric family) | resource stage: `parse`; token attribution stage: `embed_query` |
| `valid` | whether the current sample is fresh and valid | `1` / `0` |

Notes:

- `account_id` is only enabled on controlled allowlisted metric families to prevent high-cardinality growth
- `valid=0` means the current state/probe sample is a fallback or stale value, not that the label itself is malformed
- `stage` semantics depend on the metric family:
  - `openviking_resource_stage_*`: resource ingestion pipeline stages (for example `parse/persist/process`)
  - `openviking_operation_tokens_total`: token attribution stages (for example `embed_query/rerank/vlm`)

## Key Metric Families

The metric summaries below are based on representative metrics currently exposed in `.vscode/.workdir/metric/METRIC_res.md`.

### Requests and Operations

| Metric Family | Type | Common Labels | Meaning |
|---------------|------|---------------|---------|
| `openviking_http_requests_total` | Counter | `account_id, method, route, status` | total HTTP requests |
| `openviking_http_request_duration_seconds` | Histogram | `account_id, method, route, status` | HTTP latency distribution |
| `openviking_http_inflight_requests` | Gauge | `account_id, route` | current inflight requests (in-process approximation) |
| `openviking_operation_requests_total` | Counter | `account_id, operation, status` | total structured operations |
| `openviking_operation_duration_seconds` | Histogram | `account_id, operation, status` | structured operation duration distribution |

Typical usage:

- inspect whether `/api/v1/search/find` or `/api/v1/resources` is slowing down
- inspect whether a specific `operation` has elevated error rates

### Retrieval and Resource Processing

| Metric Family | Type | Common Labels | Meaning |
|---------------|------|---------------|---------|
| `openviking_retrieval_requests_total` | Counter | `account_id, context_type` | retrieval request count |
| `openviking_retrieval_results_total` | Counter | `account_id, context_type` | total retrieved results |
| `openviking_retrieval_latency_seconds` | Histogram | `account_id, context_type` | retrieval latency distribution |
| `openviking_retrieval_zero_result_total` | Counter | `account_id, context_type` | retrieval zero-result count |
| `openviking_retrieval_rerank_used_total` | Counter | `account_id` | number of retrievals that used rerank |
| `openviking_retrieval_rerank_fallback_total` | Counter | `account_id` | retrieval rerank fallback count |
| `openviking_resource_stage_total` | Counter | `account_id, stage, status` | count of resource ingestion stages |
| `openviking_resource_stage_duration_seconds` | Histogram | `account_id, stage, status` | duration distribution of ingestion stages |
| `openviking_resource_wait_duration_seconds` | Histogram | `account_id, operation` | resource ingestion wait duration distribution (for example queue waiting) |

Typical `stage` values include:

- `request`
- `parse`
- `summarize`
- `persist`
- `finalize`
- `process`

### Vector, Memory, and Semantic Metrics

| Metric Family | Type | Common Labels | Meaning |
|---------------|------|---------------|---------|
| `openviking_vector_searches_total` | Counter | `operation` | vector search count |
| `openviking_vector_scored_total` | Counter | `operation` | total scored candidates |
| `openviking_vector_passed_total` | Counter | `operation` | total passed candidates |
| `openviking_vector_returned_total` | Counter | `operation` | total returned candidates |
| `openviking_vector_scanned_total` | Counter | `operation` | total scanned candidates |
| `openviking_memory_extracted_total` | Counter | `operation` | total extracted memory items |
| `openviking_semantic_nodes_total` | Counter | `status` | total semantic nodes |

### Model Calls and Tokens

| Metric Family | Type | Common Labels | Meaning |
|---------------|------|---------------|---------|
| `openviking_model_calls_total` | Counter | `model_type, provider, model_name` | unified model call count |
| `openviking_model_tokens_total` | Counter | `model_type, provider, model_name, token_type` | unified model token count |
| `openviking_vlm_calls_total` | Counter | `account_id, provider, model_name` | VLM call count |
| `openviking_vlm_tokens_input_total` | Counter | `account_id, provider, model_name` | VLM input tokens |
| `openviking_vlm_tokens_output_total` | Counter | `account_id, provider, model_name` | VLM output tokens |
| `openviking_vlm_tokens_total` | Counter | `account_id, provider, model_name` | VLM total tokens |
| `openviking_vlm_call_duration_seconds` | Histogram | `account_id, provider, model_name` | VLM call duration distribution |
| `openviking_embedding_requests_total` | Counter | `account_id, status` | embedding request count |
| `openviking_embedding_latency_seconds` | Histogram | `account_id, status` | embedding latency distribution |
| `openviking_embedding_errors_total` | Counter | `account_id, error_code` | embedding error count |
| `openviking_embedding_calls_total` | Counter | `account_id, provider, model_name` | embedding provider call count (per-call) |
| `openviking_embedding_call_duration_seconds` | Histogram | `account_id, provider, model_name` | embedding provider call duration distribution (per-call) |
| `openviking_embedding_tokens_input_total` | Counter | `account_id, provider, model_name` | embedding input tokens (per-call aggregate) |
| `openviking_embedding_tokens_output_total` | Counter | `account_id, provider, model_name` | embedding output tokens (per-call aggregate; may not appear if always 0) |
| `openviking_embedding_tokens_total` | Counter | `account_id, provider, model_name` | embedding total tokens (per-call aggregate) |
| `openviking_rerank_calls_total` | Counter | `account_id, provider, model_name` | rerank provider call count (per-call) |
| `openviking_rerank_call_duration_seconds` | Histogram | `account_id, provider, model_name` | rerank provider call duration distribution (per-call) |
| `openviking_rerank_tokens_input_total` | Counter | `account_id, provider, model_name` | rerank input tokens (per-call aggregate) |
| `openviking_rerank_tokens_output_total` | Counter | `account_id, provider, model_name` | rerank output tokens (per-call aggregate; may not appear if always 0) |
| `openviking_rerank_tokens_total` | Counter | `account_id, provider, model_name` | rerank total tokens (per-call aggregate) |
| `openviking_operation_tokens_total` | Counter | `account_id, operation, stage, token_type` | operation token aggregation (token attribution stages) |

Notes:

- `openviking_model_*` gives a unified cross-model view for embedding and VLM usage
- `openviking_vlm_*` and `openviking_embedding_*` are better suited for workload-specific dashboards

### Queues, Locks, and Runtime State

| Metric Family | Type | Common Labels | Meaning |
|---------------|------|---------------|---------|
| `openviking_queue_processed_total` | Counter | `queue` | total processed items per queue |
| `openviking_queue_errors_total` | Counter | `queue` | total error count per queue |
| `openviking_queue_pending` | Gauge | `queue` | pending queue items |
| `openviking_queue_in_progress` | Gauge | `queue` | in-progress queue items |
| `openviking_lock_active` | Gauge | none | current active locks |
| `openviking_lock_waiting` | Gauge | none | locks currently waiting |
| `openviking_lock_stale` | Gauge | none | potentially stale locks |

These help answer:

- Is there queue backlog?
- Is there lock contention or stale locking?

### Tasks and Task Tracker

| Metric Family | Type | Common Labels | Meaning |
|---------------|------|---------------|---------|
| `openviking_task_pending` | Gauge | `task_type` | pending tasks tracked by task tracker |
| `openviking_task_running` | Gauge | `task_type` | running tasks tracked by task tracker |
| `openviking_task_completed` | Gauge | `task_type` | completed tasks tracked by task tracker |
| `openviking_task_failed` | Gauge | `task_type` | failed tasks tracked by task tracker |

### Cache

| Metric Family | Type | Common Labels | Meaning |
|---------------|------|---------------|---------|
| `openviking_cache_hits_total` | Counter | `level` | cache hit count |
| `openviking_cache_misses_total` | Counter | `level` | cache miss count |

### Session

| Metric Family | Type | Common Labels | Meaning |
|---------------|------|---------------|---------|
| `openviking_session_lifecycle_total` | Counter | `account_id, action, status` | session lifecycle event count |
| `openviking_session_contexts_used_total` | Counter | `account_id, action` | session contexts used total |
| `openviking_session_archive_total` | Counter | `account_id, status` | session archive count |

### Probes and Health State

| Metric Family | Type | Common Labels | Meaning |
|---------------|------|---------------|---------|
| `openviking_service_readiness` | Gauge | may include `valid` | main service readiness |
| `openviking_api_key_manager_readiness` | Gauge | may include `valid` | API key manager readiness |
| `openviking_storage_readiness` | Gauge | `probe, valid` | storage probe, for example `agfs` |
| `openviking_model_provider_readiness` | Gauge | `provider, valid` | model provider readiness |
| `openviking_async_system_readiness` | Gauge | `probe, valid` | async system readiness |
| `openviking_retrieval_backend_readiness` | Gauge | `probe, valid` | retrieval backend readiness |
| `openviking_encryption_component_health` | Gauge | `valid` | overall encryption component health |
| `openviking_encryption_root_key_ready` | Gauge | `valid` | whether the root key is ready |
| `openviking_encryption_kms_provider_ready` | Gauge | `provider, valid` | KMS provider readiness |

Meaning of `valid`:

- `valid="1"`: the sample was produced by a successful refresh
- `valid="0"`: the sample is a fallback or stale value and should be treated with caution

### Encryption (Operational Metrics)

| Metric Family | Type | Common Labels | Meaning |
|---------------|------|---------------|---------|
| `openviking_encryption_operations_total` | Counter | `account_id, operation, status` | encrypt/decrypt operation count |
| `openviking_encryption_duration_seconds` | Histogram | `account_id, operation, status` | encrypt/decrypt duration distribution |
| `openviking_encryption_bytes_total` | Counter | `account_id, operation` | encrypt/decrypt processed bytes total |
| `openviking_encryption_payload_size_bytes` | Histogram | `account_id, operation` | encrypt/decrypt payload size distribution |
| `openviking_encryption_auth_failed_total` | Counter | `account_id, status` | auth-failed count |
| `openviking_encryption_key_derivation_total` | Counter | `account_id, status` | key derivation count |
| `openviking_encryption_key_derivation_duration_seconds` | Histogram | `account_id, status` | key derivation duration distribution |
| `openviking_encryption_key_load_duration_seconds` | Histogram | `account_id, status, provider` | key load duration distribution |
| `openviking_encryption_key_cache_hits_total` | Counter | `account_id, provider` | key cache hit count |
| `openviking_encryption_key_cache_misses_total` | Counter | `account_id, provider` | key cache miss count |
| `openviking_encryption_key_version_usage_total` | Counter | `account_id, key_version` | key version usage count |

### Component and Observer Aggregate Metrics

| Metric Family | Type | Common Labels | Meaning |
|---------------|------|---------------|---------|
| `openviking_component_health` | Gauge | `component, valid` | component health state |
| `openviking_component_errors` | Gauge | `component, valid` | component error state |
| `openviking_observer_components_total` | Gauge | `valid` | number of observed components |
| `openviking_observer_components_unhealthy` | Gauge | `valid` | number of unhealthy components |
| `openviking_observer_components_with_errors` | Gauge | `valid` | number of components with errors |

Typical `component` values include:

- `queue`
- `models`
- `lock`
- `retrieval`
- `vikingdb`

### VikingDB and Model Usage Statistics

| Metric Family | Type | Common Labels | Meaning |
|---------------|------|---------------|---------|
| `openviking_vikingdb_collection_health` | Gauge | `collection, valid` | collection health |
| `openviking_vikingdb_collection_vectors` | Gauge | `collection, valid` | current vector count per collection |
| `openviking_model_usage_available` | Gauge | `model_type, valid` | whether model usage statistics are currently available |

Possible `model_type` values include:

- `vlm`
- `embedding`
- `rerank`

## Configuration Example

### Enabling Metrics

In `ov.conf`, the metrics subsystem can be explicitly enabled through `server.observability.metrics`:

```json
{
  "server": {
    "observability": {
      "metrics": {
        "enabled": true,
        "account_dimension": {
          "enabled": true,
          "max_active_accounts": 100,
          "metric_allowlist": [
            "openviking_http_requests_total",
            "openviking_http_request_duration_seconds",
            "openviking_http_inflight_requests",
            "openviking_operation_requests_total",
            "openviking_operation_duration_seconds",
            "openviking_vlm_calls_total",
          "openviking_vlm_call_duration_seconds",
          "openviking_rerank_*"
          ]
        }
      }
    }
  }
}
```

Recommended mental model:

- `server.observability.metrics.enabled`: master switch for the metrics subsystem
- `server.observability.metrics.account_dimension`: controls whether `account_id` labels are enabled and where they are allowed

### Recommended `account_id` Usage

- enabled by default, but only allowlisted metric families will receive tenant ids (empty allowlist still yields `__unknown__`)
- do not turn `user_id`, `session_id`, or `resource_uri` into labels
- only enable tenant dimensions on a small set of critical dashboard and alert metrics
- `metric_allowlist` supports a limited wildcard syntax: only trailing `*` prefix matches (e.g. `openviking_rerank_*`, `openviking_embedding_*`)
- a standalone `*` is not supported, nor full glob/regex patterns

## Related Documentation

- [Architecture Overview](./01-architecture.md) - overall OpenViking architecture
- [Multi-Tenant](./11-multi-tenant.md) - `account/user/agent` isolation model
- [Data Encryption](./10-encryption.md) - storage-layer encryption and isolation
- [Metrics API](../api/09-metrics.md) - `/metrics` endpoint usage
- [Metrics Design](../../design/metric-design.md) - metrics system design details
