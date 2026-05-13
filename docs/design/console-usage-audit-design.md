# OpenViking Console 统计与审计能力技术方案

## 1. 背景

OpenViking Console 本期要从基础 Web UI 演进为面向开发者的上下文管理工作台。飞书 PRD 中，Console 首屏和请求日志页面都需要 OV Server 提供产品化统计数据：

- 总览页首屏指标：
  - 上下文数据量：文件、技能、记忆
  - 今日 Tokens 消耗：VLM 输入、VLM 输出、Embedding 输入
  - 今日检索次数：find、search
  - Agent 访问概览：Agent ID、最近访问时间
- 总览页趋势图：
  - Tokens 消耗统计，支持按日期范围查询
  - 上下文提交统计，以日期和时间段热力图展示
- 请求日志：
  - Request ID、Account ID、User ID、API 类型、调用时间、响应时长、状态码
  - 支持筛选、分页、总调用次数、成功率
  - 保留最新 1000 条审计记录

当前 OpenViking 已经有 Prometheus metrics、operation telemetry 和一部分事件数据源，但这些能力主要服务于运维监控，不适合作为 Console 产品数据源直接使用。

核心判断：

- Console 不应该直接依赖 Prometheus。
- Prometheus 继续负责 QPS、latency、错误率、内部队列等运维监控。
- Console 需要的是产品语义稳定、可分页、可筛选、可按时间范围查询的 Usage/Audit 数据。
- 统计写入不能阻塞正常 API 请求。
- Console 前端不应该拼装多个 OV 内部接口，应由 OV Server 提供稳定的 `/api/v1/console/*` BFF。

因此，本方案建议在 OV Server 内增加统一的 Usage/Audit 模块，复用已有 metric 事件和 HTTP 请求完成事件，并通过异步后台 worker 写入独立存储。

## 2. 目标

- 覆盖 PRD 中 Console P0 的 Dashboard、Tokens 趋势、上下文提交热力图、请求日志需求。
- 避免 Console UI 与 OV Server 内部实现严重耦合。
- 避免 Console 依赖 Prometheus。
- 统一维护现有零散统计，减少后续继续散落打点。
- 统计写入不阻塞正常请求。
- 支持本地单机版，也为分布式生产部署预留共享存储实现。
- 为后续任务状态、检索质量、资源处理进度等 Console 能力预留扩展点。

## 3. 非目标

本期不把所有内部运维指标都产品化。

以下指标继续主要服务 Prometheus 或日志系统，不进入 Console P0：

- cache hit/miss
- encryption metrics
- queue depth
- resource processing stage latency
- low-level retrieval latency histogram
- worker 内部状态

这些数据如果后续要进入 Console，可以继续复用本方案的事件总线和 Usage/Audit 存储，但不作为当前 P0 范围。

## 4. 当前代码现状

### 4.1 Console 边界

当前 Console 已有代理边界：

```text
/console/api/v1
```

但 OV Server 侧当前没有 PRD 需要的 Console 后端接口：

```text
GET /api/v1/console/dashboard
GET /api/v1/console/tokens
GET /api/v1/console/context-commits
GET /api/v1/console/audit
```

建议新增 OV Server 接口统一放在：

```text
/api/v1/console/*
```

Console 前端通过现有代理访问明确 allowlist 的 BFF 路由：

```text
/console/api/v1/ov/console/dashboard/summary
/console/api/v1/ov/console/tokens
/console/api/v1/ov/console/context-commits
/console/api/v1/ov/console/audit
```

这样前端只依赖 Console BFF，不感知 OV 内部存储、Prometheus、metrics collector 或 telemetry 细节。
代理层不使用 wildcard 拼接上游路径，避免 `..` 这类路径规范化绕过 `/api/v1/console/*` 边界。

### 4.2 现有可复用统计信号

OpenViking 现有代码中已经有一些可以复用的事件和指标来源：

| 现有信号 | 当前用途 | Console 可复用方式 |
| --- | --- | --- |
| `http.request` | HTTP Prometheus 指标 | 请求审计、Agent last seen、find/search 次数、上下文提交热力图 |
| `vlm.call` | VLM token/latency 指标 | VLM input/output token 统计 |
| `embedding.call` | Embedding token/latency 指标 | Embedding input token 统计 |
| `rerank.call` | Rerank token/latency 指标 | 后续成本统计扩展，P0 可先不展示 |
| `retrieval.completed` | 检索 Prometheus 指标 | 继续服务运维检索质量指标，不作为 Console find/search 次数口径 |
| `session.lifecycle` | Session 操作指标 | 继续服务 Prometheus；Console 提交热力图从 HTTP 成功写请求推导 |
| `resource.stage` / `resource.wait` | 资源处理过程指标 | 属于内部处理进度，不等价于上下文提交统计 |
| `telemetry.summary` | 操作级 telemetry 汇总 | 可继续用于调试/Prometheus，不建议作为 Console 主数据源 |

### 4.3 当前缺口

- 当前事件路由偏 metrics 设计，不适合一个事件同时分发给 Prometheus 和 Usage/Audit。
- `metrics.enabled=false` 时，不应该影响 Console 统计。
- 请求审计缺少持久化存储和查询接口。
- 检索统计缺少持久化 projection，需要从 `http.request` 的 route 推导 `find` / `search`。
- 上下文提交统计需要持久化 projection，可以从成功的公开写 API 请求推导。
- Agent 访问概览没有统一投影。
- 上下文数据量是“当前状态”，不应只靠历史打点累加。

## 5. 总体架构

建议新增统一的观测事件总线和 Usage/Audit 管道：

```text
OV API / 模型调用 / 检索调用 / 上下文写入
        |
        v
Observability Event Bus
        |
        +--> Prometheus collectors
        |
        +--> Usage/Audit queue
                  |
                  v
            Usage/Audit worker
                  |
                  v
            Usage/Audit store
                  |
                  v
          /api/v1/console/* BFF
                  |
                  v
              Console UI
```

关键设计：

- 事件源统一，不再为 Console 单独散落打点。
- Prometheus 是一个 subscriber。
- Usage/Audit 是另一个 subscriber。
- Usage/Audit 是否启用不依赖 Prometheus metrics 开关。
- 请求路径只负责轻量投递事件。
- 后台 worker 异步批量聚合和落库。
- Console 只读 `/api/v1/console/*` BFF。

## 6. 事件总线设计

新增中立事件总线，例如：

```text
openviking/observability/events.py
```

事件总线需要支持：

- 一个事件发布给多个 subscriber。
- subscriber 失败不影响其他 subscriber。
- 事件投递失败不影响正常请求。
- 从当前 request/root span 上下文补充公共字段。
- 与现有 metrics data source 兼容。

事件 envelope：

```json
{
  "event_name": "http.request",
  "timestamp": "2026-05-12T12:00:00+08:00",
  "request_id": "req_xxx",
  "account_id": "default",
  "user_id": "user_xxx",
  "agent_id": "agent_xxx",
  "payload": {}
}
```

建议调整方向：

- `EventMetricDataSource._emit()` 作为兼容层，内部发布到统一 event bus。
- 现有 Prometheus collector 注册为 metrics subscriber。
- 新增 Usage/Audit subscriber 订阅同一批事件。
- 保持现有 Prometheus 指标口径尽量不变。

这样可以避免“metrics 一套事件、console 一套事件”的重复系统。

## 7. 异步 Usage/Audit Worker

统计写入不应进入主请求关键路径。

请求路径行为：

```text
try_emit(event) -> bounded queue -> return
```

后台 worker 行为：

- 按时间 flush，例如每 1 秒。
- 按批大小 flush，例如每 500 条事件。
- 写库前先在内存中按 key 聚合。
- 聚合类数据使用 upsert。
- audit 明细按批插入。
- queue 满时丢弃低优先级统计事件，并记录 dropped count。
- 服务关闭时执行一次有限时间 flush。

默认配置建议：

```yaml
observability:
  usage_audit:
    enabled: true
    queue_size: 10000
    batch_size: 500
    flush_interval_seconds: 1
    shutdown_flush_timeout_seconds: 3
```

失败策略：

- token、retrieval、context write 统计全部 best effort。
- request audit 也不阻塞请求，但可设置更高优先级。
- 统计落库失败只打日志和内部错误计数，不影响原 API 返回。

## 8. 统计口径

### 8.1 Token 消耗

来源事件：

- `vlm.call`
- `embedding.call`
- 后续可扩展 `rerank.call`

Console P0 展示字段：

| Console 字段 | 来源事件 | 来源字段 |
| --- | --- | --- |
| VLM Input tokens | `vlm.call` | `prompt_tokens` |
| VLM Output tokens | `vlm.call` | `completion_tokens` |
| Embedding input tokens | `embedding.call` | `input_tokens` |

不统计缓存 token。

聚合维度：

- account_id
- user_id，如果可获取
- agent_id，如果可获取
- date
- source：`vlm` / `embedding` / `rerank`
- token_type：`input` / `output`
- provider
- model_name

### 8.2 检索次数

PRD 要展示今日 `find` 和 `search` 次数。

`retrieval.completed` 是底层检索质量事件，可能一次公开 API 请求产生多条底层检索记录，也缺少稳定的公开 API operation 维度。因此 Console 不直接使用它统计今日检索次数。

Console 从 `http.request` 请求完成事件投影检索次数：

- `POST /api/v1/search/find` -> `find`
- `POST /api/v1/search/search` -> `search`

成功和失败都可以入库，但 Dashboard 默认展示成功次数，失败情况通过请求日志体现。

### 8.3 上下文提交统计

PRD 定义上下文提交热力图计数为：

```text
add_resource + add_skill + session.add_message() + session.commit()
```

Console 从 `http.request` 请求完成事件投影上下文提交，不在 route 中增加额外 Console 专属打点。

允许的 operation：

- `add_resource`
- `add_skill`
- `session.add_message`
- `session.commit`

只在业务操作成功后记录。失败请求进入 audit，但不增加提交热力图。

### 8.4 请求审计

请求审计应从 HTTP middleware 的请求完成事件中统一产生，不应散落在每个 route 中。

记录字段：

| 字段 | 说明 |
| --- | --- |
| `request_id` | 单次 API 调用唯一标识 |
| `account_id` | 租户/账户 |
| `user_id` | 用户 |
| `agent_id` | Agent，如果存在 |
| `method` | HTTP method |
| `route` | route template，优先使用模板而不是原始 path |
| `api_type` | Console 展示用 API 类型 |
| `status_code` | 真实返回状态码 |
| `duration_ms` | 响应耗时 |
| `created_at` | 调用完成时间 |

`api_type` 建议保持稳定、产品可理解：

| Route | API type |
| --- | --- |
| `/api/v1/search/find` | `search.find` |
| `/api/v1/search/search` | `search.search` |
| `/api/v1/resources/*` | `resources` |
| `/api/v1/sessions/*` | `sessions` |
| `/api/v1/fs/*` | `filesystem` |
| `/api/v1/admin/*` | `admin` |
| 其他 `/api/v1/*` | 第一个稳定 route segment |

默认排除：

- health check
- metrics endpoint
- docs/openapi
- static assets
- Console 前端资源

### 8.5 Agent 访问概览

Agent 概览可以从 `http.request` 事件维护。

当请求中存在 `agent_id` 时，更新：

- account_id
- agent_id
- last_seen_at
- request_count_today

Dashboard 展示：

- 去重 Agent 数量
- 最近访问 Agent 列表，按 last_seen_at 倒序

### 8.6 上下文数据量

上下文数据量表达的是当前状态，不建议通过打点累计。

原因：

- 删除、迁移、重建索引、后台修复都会让历史事件累计值和真实状态不一致。
- PRD 需要的是当前资源、技能、记忆数量。

建议新增：

```text
ContextInventoryProvider
```

P0 实现：

- 直接读取 `VikingFS.stat()` 暴露的目录 `count` 字段。
- 加短 TTL cache，例如 5 到 30 秒。
- 通过业务根目录限定统计范围，不在 Usage/Audit 层二次拼 temp/queue 过滤。
- 业务根目录不存在时按 0 处理；其他异常记录 warning 后降级为 0。

后续优化：

- 使用 write/delete 事件维护 projection 表。
- 定期 reconcile projection 和真实存储。

PRD 计数口径：

| 类型 | 口径 |
| --- | --- |
| files | `stat("viking://resources").count` |
| skills | `stat("{canonical_agent_root}/skills").count` |
| memories | `stat("{canonical_user_root}/memories").count + stat("{canonical_agent_root}/memories").count` |

Console Usage/Audit 不直接拼 vector filter，也不通过历史写入事件累计当前库存。`stat`
作为核心 FS 原语负责数量语义，BFF 只组合这些结果。

## 9. 存储设计

新增抽象接口：

```python
class UsageAuditStore:
    async def record_batch(self, events): ...
    async def get_dashboard_summary(self, scope, now): ...
    async def get_token_series(self, scope, start_date, end_date, bucket): ...
    async def get_context_commit_heatmap(self, scope, start_date, end_date, bucket): ...
    async def query_audit_logs(self, scope, filters, page, page_size): ...
```

### 9.1 本地版

本地版使用 SQLite。

建议：

- 开启 WAL。
- 后台 worker 批量写入。
- 聚合表使用 upsert。
- 按 account/date 建索引。
- audit 表按 account/created_at 建索引。

SQLite 适合本地版，因为：

- 单实例写入。
- 写入量有限。
- 不需要跨节点一致性。

### 9.2 分布式生产环境

分布式生产环境不应使用每实例 SQLite。

推荐：

- P0 生产：Postgres/RDS 兼容数据库。
- 大规模统计：Kafka + ClickHouse/ByteHouse。

关键要求：

- 所有 OV Server 实例写入同一个共享 store 或 event sink。
- Console API 读取共享 store。
- 切换存储实现不影响 `/api/v1/console/*` API。
- 聚合 key 必须包含 account_id 和 date。

### 9.3 逻辑表

Token 聚合表：

```text
usage_token_daily(
  account_id,
  user_id,
  agent_id,
  date,
  source,
  token_type,
  provider,
  model_name,
  token_count,
  updated_at
)
```

检索聚合表：

```text
usage_retrieval_daily(
  account_id,
  user_id,
  agent_id,
  date,
  operation,
  status,
  request_count,
  result_count,
  updated_at
)
```

上下文提交聚合表：

```text
usage_context_write_bucket(
  account_id,
  user_id,
  agent_id,
  date,
  hour_bucket,
  operation,
  count,
  updated_at
)
```

Agent 活跃表：

```text
usage_agent_activity_daily(
  account_id,
  agent_id,
  date,
  request_count,
  last_seen_at,
  updated_at
)
```

请求审计表：

```text
request_audit(
  id,
  request_id,
  account_id,
  user_id,
  agent_id,
  method,
  route,
  api_type,
  status_code,
  duration_ms,
  created_at
)
```

Retention：

- audit 默认每个 account 保留最新 1000 条。
- 聚合数据可长期保留，后续通过配置控制保留天数。

## 10. Console BFF API

### 10.1 Dashboard Summary

```http
GET /api/v1/console/dashboard/summary
```

响应示例：

```json
{
  "context_counts": {
    "total": 151111,
    "files": 55322,
    "skills": 35338,
    "memories": 60451
  },
  "today_tokens": {
    "total": 555475,
    "vlm_input": 262434,
    "vlm_output": 224392,
    "embedding_input": 68649
  },
  "today_retrievals": {
    "total": 131,
    "find": 46,
    "search": 85
  },
  "agent_overview": {
    "total": 3,
    "items": [
      {
        "agent_id": "agent_3387",
        "last_seen_at": "2026-04-24T19:47:00+08:00"
      }
    ]
  }
}
```

### 10.2 Tokens 趋势

```http
GET /api/v1/console/tokens?start_date=2026-04-09&end_date=2026-04-22&bucket=day
```

响应示例：

```json
{
  "start_date": "2026-04-09",
  "end_date": "2026-04-22",
  "bucket": "day",
  "items": [
    {
      "date": "2026-04-09",
      "vlm_input": 100,
      "vlm_output": 80,
      "embedding_input": 20
    }
  ]
}
```

### 10.3 上下文提交热力图

```http
GET /api/v1/console/context-commits?start_date=2026-04-09&end_date=2026-04-22&bucket=hour
```

响应示例：

```json
{
  "start_date": "2026-04-09",
  "end_date": "2026-04-22",
  "bucket": "hour",
  "items": [
    {
      "date": "2026-04-09",
      "hour": 0,
      "total": 12,
      "add_resource": 4,
      "add_skill": 1,
      "session_add_message": 6,
      "session_commit": 1
    }
  ]
}
```

### 10.4 请求审计

```http
GET /api/v1/console/audit?page=1&page_size=10&request_id=&status=&api_type=
```

响应示例：

```json
{
  "total": 128,
  "success_rate": 0.984,
  "page": 1,
  "page_size": 10,
  "items": [
    {
      "request_id": "req_xxx",
      "account_id": "default",
      "user_id": "user_xxx",
      "api_type": "search.find",
      "created_at": "2026-05-12T12:00:00+08:00",
      "duration_ms": 132,
      "status_code": 200
    }
  ]
}
```

筛选：

- `request_id`：精确匹配
- `status`：支持多选状态码或状态码类型
- `api_type`：支持多选
- 默认排序：按调用时间倒序
- 默认 page size：10

## 11. 配置设计

建议新增配置：

```yaml
observability:
  metrics:
    enabled: false
  usage_audit:
    enabled: true
    backend: sqlite
    sqlite_path: null
    queue_size: 10000
    batch_size: 500
    flush_interval_seconds: 1
    shutdown_flush_timeout_seconds: 3
    audit_retention_per_account: 1000
    timezone: local
    inventory_ttl_seconds: 10
```

语义：

- `metrics.enabled=false` 不影响 Usage/Audit。
- `usage_audit.enabled=false` 时，Console 历史统计返回空值或 capability disabled 状态。
- `backend=sqlite` 用于本地版。
- 后续可扩展 `backend=postgres` 或 `backend=clickhouse`。

## 12. 性能与可靠性

请求路径只允许做：

- 构造小事件对象。
- 读取当前 request/root span 上下文。
- 尝试入队。

请求路径不允许做：

- 打开数据库事务。
- 执行聚合查询。
- 等待统计 flush。
- 因统计落库失败而让原请求失败。

写入放大控制：

- worker 内存聚合后批量写。
- 聚合表使用 upsert。
- audit 表批量 insert。
- retention 批量裁剪。

分布式生产要求：

- 不能使用每实例 SQLite。
- 多实例写同一个共享 store 或 event sink。
- 查询走共享 store。
- 使用 account_id/date 作为主要聚合维度。

## 13. 实施计划

### Phase 1：事件管道

- 新增中立 Observability Event Bus。
- 让现有 metrics data source 通过 event bus 发布事件。
- Prometheus collector 作为 subscriber 注册。
- Usage/Audit subscriber 独立注册。
- 确保关闭 Prometheus 不影响 Console 统计。

### Phase 2：Store 与 Worker

- 新增 `UsageAuditStore` 抽象。
- 实现 SQLite backend。
- 新增 bounded queue 和后台 batch flush worker。
- 实现 audit retention。
- 接入 server lifecycle，启动 worker，关闭时 flush。

### Phase 3：HTTP 事件补齐

- 扩展 HTTP request completion 事件，补充 audit 字段。
- 从 `http.request` route 投影 `find` 和 `search`。
- 从成功的 `http.request` route 投影上下文提交：
  - `add_resource`
  - `add_skill`
  - `session.add_message`
  - `session.commit`
- 复用 VLM 和 Embedding 事件维护 token 统计。
- 从 HTTP request 事件维护 Agent activity。

### Phase 4：Console BFF

- 新增 `/api/v1/console/dashboard/summary`。
- 新增 `/api/v1/console/tokens`。
- 新增 `/api/v1/console/context-commits`。
- 新增 `/api/v1/console/audit`。
- Console proxy 增加明确 allowlist 的 `/ov/console/...` 转发。

### Phase 5：Context Inventory

- 新增 `ContextInventoryProvider`。
- P0 使用 `VikingFS.stat().count` 加短 TTL cache。
- 通过业务根目录限定统计范围，不在 Usage/Audit 层二次拼 temp/queue 过滤。
- 后续再优化为 projection 加定期 reconcile。

## 14. 测试方案

### 单元测试

- 一个事件可以分发给多个 subscriber。
- 某个 subscriber 抛错不影响其他 subscriber。
- `metrics.enabled=false` 时 Usage/Audit 仍可写入。
- queue full 时不阻塞、不抛异常。
- SQLite batch upsert 能正确聚合 token、retrieval、context write。
- audit retention 每个 account 只保留最新 1000 条。

### 集成测试

- 调用 `search/find` 后 find 统计增加。
- 调用 `search/search` 后 search 统计增加。
- mock VLM 调用后 VLM input/output tokens 增加。
- mock Embedding 调用后 embedding input tokens 增加。
- 成功执行上下文写入后 heatmap bucket 增加。
- 失败请求进入 audit，并记录真实 status code。
- 带 `agent_id` 的请求会更新 Agent last seen。

### API 测试

- Dashboard summary 返回四个首屏卡片数据。
- Tokens 趋势支持任意日期范围，不限定 14 天。
- Context commits 支持日期范围和 bucket 参数。
- Audit 支持分页、request_id 精确匹配、status 筛选、api_type 筛选。
- Console proxy 能转发明确 allowlist 的 `/console/api/v1/ov/console/...` 路由。
- Console proxy 拒绝路径穿越，不会把 `/ov/console/../admin/accounts` 转发到 `/api/v1/admin/accounts`。

## 15. 待确认问题

- 生产 P0 是否必须同步实现 Postgres backend，还是本地版先落 SQLite + store 抽象。
- Audit retention 是每个 account 最新 1000 条，还是全局最新 1000 条。本方案建议按 account 隔离。
- Dashboard 的今日检索次数是否只展示成功调用。本方案建议默认展示成功调用，失败通过请求日志体现。
- 上下文数据量最终采用物理文件数量还是逻辑上下文数量。本方案按 PRD 的业务目录 `stat.count` 口径实现 P0。

## 16. 结论

Console 需要的是产品化 Usage/Audit 数据，而不是运维监控指标。因此不建议让 Console 直接依赖 Prometheus。

推荐方案是在 OV Server 内新增统一 Usage/Audit 模块：通过中立事件总线复用现有 metric 事件，用 HTTP request completion 事件投影 Console 需要的检索、上下文提交、审计和 Agent 活跃数据，用异步 worker 批量写入可插拔存储，再通过 `/api/v1/console/*` 提供稳定 BFF。

这个设计可以同时满足本地版易用性、生产环境扩展性、请求路径性能和 Console/OV Server 解耦要求。
