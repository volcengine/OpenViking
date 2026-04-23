# 指标与 Metrics

OpenViking 提供一套面向机器抓取的指标体系，用于暴露系统运行态、请求质量、模型调用情况、资源处理吞吐、探针健康状态等信息。

与人类排障用的 `/api/v1/observer/*` 和业务分析用的 `/api/v1/stats/*` 不同，Metrics 的目标是：

- 供 Prometheus、Grafana Agent 等系统**高频抓取**
- 使用低基数、可聚合的指标模型
- 服务于监控、告警、容量观察与回归排查

## 概述

### 为什么需要 Metrics

Metrics 适合回答这类问题：

- 最近一段时间 HTTP 请求是否异常升高？
- 资源导入、检索、模型调用是否变慢？
- 队列是否堆积？
- 关键依赖（存储、模型、VikingDB、加密、异步系统）当前是否可用？
- 某些租户是否出现异常流量或异常错误率？

相比日志和 observer 状态，metrics 更适合做：

- 持续抓取
- 时间序列聚合
- Dashboard 展示
- 告警规则

### 与 Observer / Stats 的区别

| 能力 | 适合什么 | 输出形式 | 典型使用场景 |
|------|----------|----------|--------------|
| `/metrics` | 在线监控、告警、聚合趋势 | Prometheus exposition 文本 | Grafana 看板、Prometheus 抓取 |
| `/api/v1/observer/*` | 人工查看组件瞬时状态 | JSON / 状态表 | 排障、健康检查 |
| `/api/v1/stats/*` | 分析型统计 | JSON | memory health、staleness、session extraction 等 |

设计边界是：

- `/metrics` 只承载**低基数、低成本**指标
- `/api/v1/stats/*` 继续承载分析型统计，不为了 Prometheus 抓取模型牺牲表达能力

## 指标体系架构

OpenViking 当前的 metrics 体系由四层组成：

```text
业务逻辑 / HTTP 请求 / 后台任务
          │
          ▼
      DataSource
  （事件发射 / 状态读取）
          │
          ▼
      Collector
 （语义分流、标签决定）
          │
          ▼
    MetricRegistry
   （进程内指标注册中心）
          │
          ▼
      Exporter
 （Prometheus 文本导出）
          │
          ▼
       /metrics
```

### DataSource

DataSource 负责提供指标输入，主要有两种方式：

- **事件型**：业务代码在关键路径发射事件，例如检索完成、模型调用成功、资源导入阶段完成
- **读取型**：在 `/metrics` 抓取前读取当前状态，例如队列状态、锁状态、探针状态

### Collector

Collector 负责把输入转成指标语义：

- 决定写哪个指标
- 决定携带哪些标签
- 决定失败时如何暴露（例如 `valid=1/0`）

### MetricRegistry

MetricRegistry 是进程内的指标注册中心，用于保存当前指标值，并在导出时统一读取。

### Exporter

当前首个落地导出器是 Prometheus Exporter，用于把 registry 中的指标渲染成 Prometheus exposition 文本。

## 使用方式

### 访问 `/metrics`

当前实现中，`/metrics` 未接入 `get_request_context` 等鉴权依赖，因此从代码行为上看，它当前等价于公开抓取端点。

```bash
curl http://localhost:1933/metrics
```

如果你的部署环境通过网关、反向代理或服务发现层对 `/metrics` 做了保护，则应按部署方式附加鉴权。

### Prometheus 抓取示例

```yaml
scrape_configs:
  - job_name: openviking
    metrics_path: /metrics
    static_configs:
      - targets: ["localhost:1933"]
```

### 如何理解常见标签

| 标签 | 含义 | 示例 |
|------|------|------|
| `account_id` | 租户维度标签 | `test-account`、`__unknown__`、`__overflow__` |
| `route` | HTTP 路由模板 | `/api/v1/search/find` |
| `method` | HTTP 方法 | `GET`、`POST` |
| `status` | 请求或阶段状态 | `200`、`ok`、`error` |
| `operation` | 操作名称 | `search.find`、`resources.add_resource` |
| `context_type` | 检索上下文类型 | `resource` |
| `provider` | 模型或外部服务提供方 | `volcengine` |
| `model_name` | 模型名称 | `doubao-seed-1-8-251228` |
| `stage` | 阶段标签（按指标族定义） | 资源阶段：`parse`；Token 归因阶段：`embed_query` |
| `valid` | 当前样本是否为有效新鲜值 | `1` / `0` |

其中：

- `account_id` 只在受控白名单指标上启用，避免高基数失控
- `valid=0` 表示该状态/探针的当前样本是失败回退值或 stale fallback，不代表标签本身错误
- `stage` 的语义依赖指标族：
  - `openviking_resource_stage_*`：资源导入流水线阶段（如 `parse/persist/process`）
  - `openviking_operation_tokens_total`：Token Attribution 的归因阶段（如 `embed_query/rerank/vlm`）

## 关键指标说明

下面的指标说明基于当前实际暴露的代表性指标输出（整理自 `.vscode/.workdir/metric/METRIC_res.md`）。

### 请求与操作

| 指标族 | 类型 | 常见标签 | 含义 |
|--------|------|----------|------|
| `openviking_http_requests_total` | Counter | `account_id, method, route, status` | HTTP 请求总量 |
| `openviking_http_request_duration_seconds` | Histogram | `account_id, method, route, status` | HTTP 请求耗时分布 |
| `openviking_http_inflight_requests` | Gauge | `account_id, route` | 当前 inflight 请求数（进程内近似值） |
| `openviking_operation_requests_total` | Counter | `account_id, operation, status` | 结构化操作总量 |
| `openviking_operation_duration_seconds` | Histogram | `account_id, operation, status` | 结构化操作耗时分布 |

适用场景：

- 看 `/api/v1/search/find`、`/api/v1/resources` 是否异常变慢
- 看某个 `operation` 是否错误率升高

### 检索与资源处理

| 指标族 | 类型 | 常见标签 | 含义 |
|--------|------|----------|------|
| `openviking_retrieval_requests_total` | Counter | `account_id, context_type` | 检索请求次数 |
| `openviking_retrieval_results_total` | Counter | `account_id, context_type` | 检索返回结果数量累计 |
| `openviking_retrieval_latency_seconds` | Histogram | `account_id, context_type` | 检索耗时分布 |
| `openviking_retrieval_zero_result_total` | Counter | `account_id, context_type` | 检索零结果次数 |
| `openviking_retrieval_rerank_used_total` | Counter | `account_id` | 检索中使用 rerank 的次数 |
| `openviking_retrieval_rerank_fallback_total` | Counter | `account_id` | 检索 rerank 回退次数 |
| `openviking_resource_stage_total` | Counter | `account_id, stage, status` | 资源导入各阶段执行次数 |
| `openviking_resource_stage_duration_seconds` | Histogram | `account_id, stage, status` | 资源导入阶段耗时分布 |
| `openviking_resource_wait_duration_seconds` | Histogram | `account_id, operation` | 资源导入等待耗时分布（例如队列等待） |

典型 `stage` 包括：

- `request`
- `parse`
- `summarize`
- `persist`
- `finalize`
- `process`

### 向量检索、记忆与语义节点

| 指标族 | 类型 | 常见标签 | 含义 |
|--------|------|----------|------|
| `openviking_vector_searches_total` | Counter | `operation` | 向量检索次数 |
| `openviking_vector_scored_total` | Counter | `operation` | 向量候选打分数量累计 |
| `openviking_vector_passed_total` | Counter | `operation` | 向量候选通过数量累计 |
| `openviking_vector_returned_total` | Counter | `operation` | 向量候选返回数量累计 |
| `openviking_vector_scanned_total` | Counter | `operation` | 向量候选扫描数量累计 |
| `openviking_memory_extracted_total` | Counter | `operation` | memory extracted 数量累计 |
| `openviking_semantic_nodes_total` | Counter | `status` | semantic nodes 数量累计 |

### 模型调用与 Token

| 指标族 | 类型 | 常见标签 | 含义 |
|--------|------|----------|------|
| `openviking_model_calls_total` | Counter | `model_type, provider, model_name` | 模型调用总量（统一视角） |
| `openviking_model_tokens_total` | Counter | `model_type, provider, model_name, token_type` | 模型 token 累计量 |
| `openviking_vlm_calls_total` | Counter | `account_id, provider, model_name` | VLM 调用次数 |
| `openviking_vlm_tokens_input_total` | Counter | `account_id, provider, model_name` | VLM 输入 token |
| `openviking_vlm_tokens_output_total` | Counter | `account_id, provider, model_name` | VLM 输出 token |
| `openviking_vlm_tokens_total` | Counter | `account_id, provider, model_name` | VLM 总 token |
| `openviking_vlm_call_duration_seconds` | Histogram | `account_id, provider, model_name` | VLM 调用耗时分布 |
| `openviking_embedding_requests_total` | Counter | `account_id, status` | embedding 请求数 |
| `openviking_embedding_latency_seconds` | Histogram | `account_id, status` | embedding 耗时分布 |
| `openviking_embedding_errors_total` | Counter | `account_id, error_code` | embedding 错误次数 |
| `openviking_embedding_calls_total` | Counter | `account_id, provider, model_name` | embedding provider 调用次数（per-call） |
| `openviking_embedding_call_duration_seconds` | Histogram | `account_id, provider, model_name` | embedding provider 调用耗时分布（per-call） |
| `openviking_embedding_tokens_input_total` | Counter | `account_id, provider, model_name` | embedding 输入 token（per-call 聚合） |
| `openviking_embedding_tokens_output_total` | Counter | `account_id, provider, model_name` | embedding 输出 token（per-call 聚合；若长期为 0 可能不出现） |
| `openviking_embedding_tokens_total` | Counter | `account_id, provider, model_name` | embedding 总 token（per-call 聚合） |
| `openviking_rerank_calls_total` | Counter | `account_id, provider, model_name` | rerank provider 调用次数（per-call） |
| `openviking_rerank_call_duration_seconds` | Histogram | `account_id, provider, model_name` | rerank provider 调用耗时分布（per-call） |
| `openviking_rerank_tokens_input_total` | Counter | `account_id, provider, model_name` | rerank 输入 token（per-call 聚合） |
| `openviking_rerank_tokens_output_total` | Counter | `account_id, provider, model_name` | rerank 输出 token（per-call 聚合；若长期为 0 可能不出现） |
| `openviking_rerank_tokens_total` | Counter | `account_id, provider, model_name` | rerank 总 token（per-call 聚合） |
| `openviking_operation_tokens_total` | Counter | `account_id, operation, stage, token_type` | Operation Token 汇总（含 token attribution 归因阶段） |

说明：

- `openviking_model_*` 是统一模型视角，便于同时看 embedding / vlm
- `openviking_vlm_*` 和 `openviking_embedding_*` 更适合业务侧针对性看板
  - `*_requests_*` 更偏“业务请求视角”
  - `*_calls_* / *_call_duration_* / *_tokens_*` 更偏“模型调用视角”（按 `provider/model_name` 聚合）
 - `openviking_operation_tokens_total` 不存 `token_type="all/total"` 这类预聚合标签，总账建议在 TSDB 查询侧用 `sum(...)` 聚合得到

### 队列、锁与系统运行态

| 指标族 | 类型 | 常见标签 | 含义 |
|--------|------|----------|------|
| `openviking_queue_processed_total` | Counter | `queue` | 队列累计处理量 |
| `openviking_queue_errors_total` | Counter | `queue` | 队列累计错误量 |
| `openviking_queue_pending` | Gauge | `queue` | 队列待处理数 |
| `openviking_queue_in_progress` | Gauge | `queue` | 队列执行中数量 |
| `openviking_lock_active` | Gauge | 无 | 当前活跃锁数量 |
| `openviking_lock_waiting` | Gauge | 无 | 当前等待中的锁数量 |
| `openviking_lock_stale` | Gauge | 无 | 可能 stale 的锁数量 |

这些指标适合回答：

- 是否有队列堆积？
- 是否有锁竞争或 stale lock？

### 任务与 Task Tracker

| 指标族 | 类型 | 常见标签 | 含义 |
|--------|------|----------|------|
| `openviking_task_pending` | Gauge | `task_type` | task tracker 待执行任务数 |
| `openviking_task_running` | Gauge | `task_type` | task tracker 执行中任务数 |
| `openviking_task_completed` | Gauge | `task_type` | task tracker 已完成任务数 |
| `openviking_task_failed` | Gauge | `task_type` | task tracker 失败任务数 |

### Cache

| 指标族 | 类型 | 常见标签 | 含义 |
|--------|------|----------|------|
| `openviking_cache_hits_total` | Counter | `level` | Cache 命中次数 |
| `openviking_cache_misses_total` | Counter | `level` | Cache 未命中次数 |

### Session

| 指标族 | 类型 | 常见标签 | 含义 |
|--------|------|----------|------|
| `openviking_session_lifecycle_total` | Counter | `account_id, action, status` | session 生命周期事件次数 |
| `openviking_session_contexts_used_total` | Counter | `account_id, action` | session contexts used 累计量 |
| `openviking_session_archive_total` | Counter | `account_id, status` | session archive 次数 |

### 探针与健康状态

| 指标族 | 类型 | 常见标签 | 含义 |
|--------|------|----------|------|
| `openviking_service_readiness` | Gauge | 可含 `valid` | 服务主 readiness |
| `openviking_api_key_manager_readiness` | Gauge | 可含 `valid` | API Key Manager readiness |
| `openviking_storage_readiness` | Gauge | `probe, valid` | 存储探针，例如 `agfs` |
| `openviking_model_provider_readiness` | Gauge | `provider, valid` | 模型提供方 readiness |
| `openviking_async_system_readiness` | Gauge | `probe, valid` | 异步系统 readiness |
| `openviking_retrieval_backend_readiness` | Gauge | `probe, valid` | 检索后端 readiness |
| `openviking_encryption_component_health` | Gauge | `valid` | 加密组件总体健康 |
| `openviking_encryption_root_key_ready` | Gauge | `valid` | 根密钥是否就绪 |
| `openviking_encryption_kms_provider_ready` | Gauge | `provider, valid` | KMS provider readiness |

`valid` 的意义：

- `valid="1"`：当前样本是本次成功刷新得到的结果
- `valid="0"`：当前样本是失败回退值或 stale fallback，说明该探针/状态当前不可完全信任

### 加密（运行指标）

| 指标族 | 类型 | 常见标签 | 含义 |
|--------|------|----------|------|
| `openviking_encryption_operations_total` | Counter | `account_id, operation, status` | encrypt/decrypt 操作次数 |
| `openviking_encryption_duration_seconds` | Histogram | `account_id, operation, status` | encrypt/decrypt 耗时分布 |
| `openviking_encryption_bytes_total` | Counter | `account_id, operation` | encrypt/decrypt 处理字节数累计 |
| `openviking_encryption_payload_size_bytes` | Histogram | `account_id, operation` | encrypt/decrypt payload size 分布 |
| `openviking_encryption_auth_failed_total` | Counter | `account_id, status` | auth failed 次数 |
| `openviking_encryption_key_derivation_total` | Counter | `account_id, status` | key derivation 次数 |
| `openviking_encryption_key_derivation_duration_seconds` | Histogram | `account_id, status` | key derivation 耗时分布 |
| `openviking_encryption_key_load_duration_seconds` | Histogram | `account_id, status, provider` | key load 耗时分布 |
| `openviking_encryption_key_cache_hits_total` | Counter | `account_id, provider` | key cache hits 次数 |
| `openviking_encryption_key_cache_misses_total` | Counter | `account_id, provider` | key cache misses 次数 |
| `openviking_encryption_key_version_usage_total` | Counter | `account_id, key_version` | key version 使用次数 |

### 组件与 Observer 聚合指标

| 指标族 | 类型 | 常见标签 | 含义 |
|--------|------|----------|------|
| `openviking_component_health` | Gauge | `component, valid` | 组件健康状态 |
| `openviking_component_errors` | Gauge | `component, valid` | 组件错误状态 |
| `openviking_observer_components_total` | Gauge | `valid` | observer 观测到的组件数量 |
| `openviking_observer_components_unhealthy` | Gauge | `valid` | 不健康组件数量 |
| `openviking_observer_components_with_errors` | Gauge | `valid` | 有错误组件数量 |

典型 `component` 包括：

- `queue`
- `models`
- `lock`
- `retrieval`
- `vikingdb`

### VikingDB 与模型使用统计

| 指标族 | 类型 | 常见标签 | 含义 |
|--------|------|----------|------|
| `openviking_vikingdb_collection_health` | Gauge | `collection, valid` | collection 健康状态 |
| `openviking_vikingdb_collection_vectors` | Gauge | `collection, valid` | collection 当前向量数 |
| `openviking_model_usage_available` | Gauge | `model_type, valid` | 模型使用统计是否可用 |

其中 `model_type` 可能包括：

- `vlm`
- `embedding`
- `rerank`

## 配置示例

### 启用 Metrics

在 `ov.conf` 中，可以通过 `server.observability.metrics` 显式启用 metrics 子系统：

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

推荐理解方式：

- `server.observability.metrics.enabled`：指标体系总开关
- `server.observability.metrics.account_dimension`：控制 `account_id` 标签是否启用以及启用范围

### Exporters 配置

默认情况下，OpenViking 会通过 Prometheus exposition 格式在 `/metrics` 输出指标。
如果希望在保留 `/metrics` 的同时把同一份进程内指标导出到 OTLP 后端，可以在 `server.observability.metrics.exporters` 下启用 exporter。

关键字段：

- `server.observability.metrics.exporters.prometheus.enabled`：是否启用 Prometheus exporter（提供 `/metrics`）
- `server.observability.metrics.exporters.otel.enabled`：是否启用 OTLP 导出（复用同一份 registry）
- `server.observability.metrics.exporters.otel.protocol`：`"grpc"` 或 `"http"`
- `server.observability.metrics.exporters.otel.tls.insecure`：仅对 OTLP/gRPC 生效；`true` 表示明文连接（无 TLS）
- `server.observability.metrics.exporters.otel.endpoint`：OTLP 端点（gRPC 用 `host:4317`；HTTP 必须是完整 URL）

示例：

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
            "export_interval_ms": 10000
          }
        }
      }
    }
  }
}
```

### `account_id` 标签的使用建议

- 默认开启，但仅对白名单指标启用（`metric_allowlist` 为空时仍会输出为 `__unknown__`）
- 不要把 `user_id`、`session_id`、`resource_uri` 这类高基数字段做成标签
- 对于看板和告警，只对少量关键指标族打开租户维度
- `metric_allowlist` 支持有限通配符：仅支持**末尾 `*` 的前缀匹配**（例如 `openviking_rerank_*`、`openviking_embedding_*`）
- 不支持单独的 `*`（空前缀），也不支持中间通配、完整 glob 或正则


## 相关文档

- [架构概述](./01-architecture.md) - OpenViking 总体架构
- [多租户](./11-multi-tenant.md) - `account/user/agent` 隔离模型
- [数据加密](./10-encryption.md) - 存储层加密与隔离
- [Metrics API](../api/09-metrics.md) - `/metrics` 端点用法
- [指标体系设计](../../design/metric-design.md) - 指标体系设计细节
