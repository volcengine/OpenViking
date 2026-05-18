# Usage/Audit 使用说明

Usage/Audit 是 OpenViking Server 给 Console 使用的产品统计与请求审计模块。它复用
OpenViking 已有的 observability 事件，不要求 Console 直接依赖 Prometheus，也不会在正常
API 请求链路里同步写统计库。

## 适用场景

当前模块主要服务 Console P0 页面：

- 总览页首屏：上下文数据量、今日 Token、今日检索、Agent 概览
- Token 趋势：按日期范围查询模型 Token 消耗
- 上下文提交热力图：按日期和小时段查询上下文写入活动
- 请求日志：分页查询请求明细、状态、耗时和成功率

这套数据是产品语义数据，不是运维指标。QPS、latency histogram、queue depth、cache
hit/miss 等仍然应该看 Prometheus metrics。

## 工作方式

数据流如下：

```text
业务请求 / 模型调用
        |
        v
Observability Event Bus
        |
        +--> Metrics subscriber
        |
        +--> Usage/Audit subscriber
                  |
                  v
            Usage/Audit worker
                  |
                  v
            Usage/Audit store
                  |
                  v
          /api/v1/console/* BFF
```

关键点：

- Usage/Audit 订阅共享事件总线，不重复散落 Console 专用打点。
- `server.observability.metrics.enabled=false` 不影响 Usage/Audit。
- 请求路径只做非阻塞事件投递；写库由后台 worker 批量完成。
- worker 使用 bounded queue；队列满时会丢弃统计事件并增加 `dropped_count`。
- 服务关闭时会尽量 flush 剩余事件；如果已有 batch 正在写入，会等待它完成，避免尾部审计丢失或重复写入。

## 配置

默认启用 Usage/Audit。最小配置可以不写任何字段。

完整配置示例：

```json
{
  "server": {
    "observability": {
      "usage_audit": {
        "enabled": true,
        "backend": "sqlite",
        "sqlite_path": "/path/to/usage_audit.sqlite3",
        "queue_size": 10000,
        "batch_size": 500,
        "flush_interval_seconds": 1.0,
        "shutdown_flush_timeout_seconds": 3.0,
        "usage_retention_days": 14,
        "audit_retention_days": 7,
        "audit_retention_per_account": 1000,
        "timezone": "local",
        "inventory_ttl_seconds": 10.0
      }
    }
  }
}
```

字段说明：

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 是否启用 Usage/Audit |
| `backend` | `"sqlite"` | 当前仅支持 SQLite |
| `sqlite_path` | `null` | SQLite 文件路径；为空时使用 OpenViking workspace 下的 `usage_audit.sqlite3` |
| `queue_size` | `10000` | 后台写入队列大小 |
| `batch_size` | `500` | 单次批量写入的最大事件数 |
| `flush_interval_seconds` | `1.0` | worker 定时 flush 间隔 |
| `shutdown_flush_timeout_seconds` | `3.0` | 服务关闭时 flush 等待时间 |
| `usage_retention_days` | `14` | 统计聚合数据保留天数，包含 Token、检索、上下文写入热力图、Agent 活跃；`0` 表示不按天裁剪 |
| `audit_retention_days` | `7` | 请求审计日志保留天数；`0` 表示不按天裁剪 |
| `audit_retention_per_account` | `1000` | 每个 account 保留的最新请求审计条数；`0` 表示不按条数裁剪 |
| `timezone` | `"local"` | 统计日期使用的时区；可填 `"local"`、`"UTC"` 或 IANA 时区名 |
| `inventory_ttl_seconds` | `10.0` | 上下文当前数据量查询缓存时间 |

本地版使用 SQLite 没问题。分布式生产环境如果多实例同时提供 Console，建议后续增加共享
store backend，而不是让多个实例各写各的本地 SQLite。

## 数据口径

### Token

来自模型调用事件：

| 事件 | 当前 Console 展示口径 |
| --- | --- |
| `vlm.call` | `prompt_tokens` 计入 `vlm_input`，`completion_tokens` 计入 `vlm_output` |
| `embedding.call` | `prompt_tokens` 计入 `embedding_input` |
| `rerank.call` | 已可落库，当前 Console summary/series 暂不展示 |

### 今日检索

来自 HTTP 请求完成事件 `http.request`：

| API route | operation |
| --- | --- |
| `POST /api/v1/search/find` | `find` |
| `POST /api/v1/search/search` | `search` |

`2xx` 和 `3xx` 记为 `success`，`4xx/5xx` 记为 `error`。Dashboard 今日检索只展示成功请求数。

### 上下文提交热力图

来自成功的公开写请求：

| API route | operation |
| --- | --- |
| `POST /api/v1/resources` | `add_resource` |
| `POST /api/v1/skills` | `add_skill` |
| `POST /api/v1/sessions/{session_id}/messages` | `session_add_message` |
| `POST /api/v1/sessions/{session_id}/commit` | `session_commit` |

只有 `2xx/3xx` 会进入上下文提交统计。

### 上下文数据量

Dashboard 首屏的上下文数据量是当前状态查询，不是历史事件累加：

- `files`：读取 `viking://resources` 的 `stat.count`
- `skills`：读取当前 Agent `skills` 根目录的 `stat.count`
- `memories`：读取当前 User 和当前 Agent 的 `memories` 根目录 `stat.count` 后求和

`stat.count` 是底层 `VikingFS.stat()` 暴露的目录计数字段。Usage/Audit 不自己拼
vector filter，也不从历史写入事件累计当前库存。

这部分会走 `inventory_ttl_seconds` 缓存，避免 Console 刷新频繁打到底层存储。业务根目录
不存在时按 0 处理，避免新环境或空租户反复刷 warning。

### 请求审计

请求审计来自 `http.request`，保留字段：

- `request_id`
- `account_id`
- `user_id`
- `agent_id`
- `method`
- `route`
- `api_type`
- `status_code`
- `duration_ms`
- `created_at`

以下 route 不进入审计：

- `/metrics`
- `/health`
- `/ready`
- `/docs`
- `/docs/oauth2-redirect`
- `/redoc`
- `/openapi.json`
- `/favicon.ico`
- `/favicon.png`
- `/apple-touch-icon.png`
- `/console/*`
- `/api/v1/console/*`

## Console BFF API

所有接口都在 OV Server 侧：

```text
/api/v1/console/*
```

Console 前端通过 Console server 的 allowlist proxy 访问：

```text
/console/api/v1/ov/console/dashboard/summary
/console/api/v1/ov/console/tokens
/console/api/v1/ov/console/context-commits
/console/api/v1/ov/console/audit
```

权限要求：

- `ROOT` 和 `ADMIN` 可以访问
- 普通 `USER` 返回 `403 PERMISSION_DENIED`

### Dashboard Summary

```text
GET /api/v1/console/dashboard/summary
```

返回示例：

```json
{
  "status": "ok",
  "result": {
    "context_counts": {
      "files": 12,
      "skills": 3,
      "memories": 8,
      "total": 23
    },
    "today_tokens": {
      "vlm_input": 1000,
      "vlm_output": 500,
      "embedding_input": 200,
      "total": 1700
    },
    "today_retrievals": {
      "find": 10,
      "search": 4,
      "total": 14
    },
    "agent_overview": {
      "total": 2,
      "items": [
        {
          "agent_id": "agent-1",
          "last_seen_at": "2026-05-12T10:00:00+08:00"
        }
      ]
    }
  }
}
```

如果 Usage/Audit 被关闭或尚未初始化，返回：

```json
{
  "status": "ok",
  "result": {
    "enabled": false,
    "message": "Usage/Audit is disabled or not initialized."
  }
}
```

### Token Series

```text
GET /api/v1/console/tokens?start_date=2026-05-01&end_date=2026-05-12&bucket=day
```

参数：

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `start_date` | 是 | 开始日期，格式 `YYYY-MM-DD` |
| `end_date` | 是 | 结束日期，格式 `YYYY-MM-DD` |
| `bucket` | 否 | 当前仅支持 `day` |

返回中会补齐日期范围内没有数据的日期。

### Context Commits

```text
GET /api/v1/console/context-commits?start_date=2026-05-01&end_date=2026-05-12&bucket=4h
```

参数：

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `start_date` | 是 | 开始日期，格式 `YYYY-MM-DD` |
| `end_date` | 是 | 结束日期，格式 `YYYY-MM-DD` |
| `bucket` | 否 | `hour` 或 `4h`，默认 `hour` |

返回中会补齐日期和小时段范围内没有数据的 bucket。

### Audit Logs

```text
GET /api/v1/console/audit?page=1&page_size=20&status=success,error&api_type=search.find
```

参数：

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `page` | 否 | 页码，从 `1` 开始 |
| `page_size` | 否 | 每页条数，范围 `1..100` |
| `request_id` | 否 | 精确匹配 request id |
| `status` | 否 | 可重复传，也可逗号分隔 |
| `api_type` | 否 | 可重复传，也可逗号分隔 |

`status` 支持：

- `success` / `ok`：`2xx` 和 `3xx`
- `2xx`
- `3xx`
- `error` / `failed`：`4xx/5xx`
- `4xx`、`5xx` 等通配段
- 具体状态码，例如 `404`

返回字段：

```json
{
  "status": "ok",
  "result": {
    "total": 123,
    "success_rate": 0.98,
    "page": 1,
    "page_size": 20,
    "items": []
  }
}
```

`success_rate` 使用当前筛选条件下的 `2xx/3xx` 占比。

## 本地验证

启动 server 后，可以通过请求触发数据：

```bash
curl -X POST "http://127.0.0.1:1933/api/v1/search/find" \
  -H "Authorization: Bearer $OPENVIKING_API_KEY" \
  -H "X-OpenViking-Account: default" \
  -H "X-OpenViking-User: default" \
  -H "X-OpenViking-Agent: default" \
  -H "Content-Type: application/json" \
  -d '{"query":"hello","limit":3}'
```

查询 Console BFF：

```bash
curl "http://127.0.0.1:1933/api/v1/console/dashboard/summary" \
  -H "Authorization: Bearer $OPENVIKING_API_KEY" \
  -H "X-OpenViking-Account: default" \
  -H "X-OpenViking-User: default" \
  -H "X-OpenViking-Agent: default"
```

如果使用 Console server，则访问 `/console/api/v1/ov/console/*` 代理路径。

## 测试

相关单测：

```bash
.venv/bin/python -m pytest \
  tests/observability/test_events.py \
  tests/observability/test_usage_audit_store.py \
  tests/observability/test_usage_audit_worker.py \
  tests/observability/test_console_router.py \
  tests/observability/test_usage_audit_runtime.py \
  tests/observability/test_usage_audit_inventory.py \
  tests/misc/test_console_proxy.py
```

相关 lint：

```bash
.venv/bin/python -m ruff check \
  openviking/observability/events.py \
  openviking/observability/usage_audit \
  openviking/server/routers/console.py \
  openviking/console/app.py \
  tests/observability \
  tests/misc/test_console_proxy.py
```

## 常见问题

### Console 查询为什么返回 `enabled=false`？

通常是 `server.observability.usage_audit.enabled=false`，或者 Usage/Audit runtime 没有初始化成功。
先看 server 启动日志中是否有 `Usage/Audit store initialized with sqlite backend`。

### 为什么 Dashboard 今天没有 Token？

确认模型调用事件是否触发：

- VLM 需要产生 `vlm.call`
- Embedding 需要产生 `embedding.call`

统计日期使用 `server.observability.usage_audit.timezone`。如果时区配置不同，数据可能落在另一天。

### 为什么请求日志里没有 Console 自己的请求？

这是预期行为。`/api/v1/console/*` 和 `/console/*` 会被排除，避免 Console 页面刷新污染产品请求审计。

### 为什么普通用户访问 Console BFF 是 403？

Console BFF 查询的是账号级聚合和审计明细，当前只允许 `ROOT` / `ADMIN` 访问。

### SQLite 文件在哪里？

如果没有配置 `sqlite_path`，默认在 OpenViking workspace 下：

```text
<workspace>/usage_audit.sqlite3
```

可以通过 `server.observability.usage_audit.sqlite_path` 显式指定。

### 生产多实例怎么部署？

当前实现只有 SQLite backend，更适合单机本地版。多实例生产环境需要共享 store backend，避免每个实例只持有自己的局部统计。后续扩展时应实现 `UsageAuditStore` 协议，而不是改 Console BFF。
