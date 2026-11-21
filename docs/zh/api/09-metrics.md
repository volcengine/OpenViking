# 指标与 Metrics

OpenViking 提供 `/metrics` 端点，用于向 Prometheus、Grafana Agent 等监控系统导出运行时指标。

该端点返回供监控系统定时抓取的 Prometheus 文本，内容是系统运行状态和服务质量信号，不用于业务分析。需要组件状态的 JSON 快照时，使用 `/api/v1/observer/*`。

## API 参考

### metrics()

导出当前进程内的 Prometheus 指标文本。

该端点通常被 Prometheus 定时抓取，也可以用于本地调试或手工排查。

**认证**

服务端不对 `/metrics` 请求鉴权。若网关或反向代理限制访问，抓取任务需使用该层要求的凭据。

**HTTP API**

```
GET /metrics
```

```bash
curl -X GET http://localhost:1933/metrics
```

如果你的部署环境在网关或代理层要求鉴权，可以按网关要求附加请求头，例如：

```bash
curl -X GET http://localhost:1933/metrics \
  -H "Authorization: Bearer your-key"
```

**响应格式**

成功时返回 `text/plain; version=0.0.4; charset=utf-8`，内容为 Prometheus exposition 格式文本，例如：

```text
# HELP openviking_http_requests_total Total number of HTTP requests
# TYPE openviking_http_requests_total counter
openviking_http_requests_total{method="GET",route="/api/v1/system/status",status="200"} 12

# HELP openviking_http_inflight_requests Number of inflight HTTP requests
# TYPE openviking_http_inflight_requests gauge
openviking_http_inflight_requests{route="/api/v1/system/status"} 0
```

当指标系统未启用时，返回：

- HTTP 状态码：`404`
- 响应体：

```text
Prometheus metrics are disabled.
```

**示例：Prometheus 抓取配置**

```yaml
scrape_configs:
  - job_name: openviking
    metrics_path: /metrics
    static_configs:
      - targets: ["localhost:1933"]
```

如果你的部署环境对 `/metrics` 做了网关鉴权，可以通过反向代理、service discovery，或 Prometheus 支持的鉴权方式为该抓取任务配置请求头。

**注意事项**

- `/metrics` 适合高频抓取，因此其中的指标应保持低基数、低成本。
- `/metrics` 返回的是 Prometheus 文本，不是标准 OpenViking API 的 `{status, result}` JSON 结构。
- 人工查看组件瞬时状态更适合使用 `/api/v1/observer/*`。
- `/metrics` 现在也包含 VikingBot feedback observability 指标，这些指标来自对持久化 session 数据的 scrape-time 聚合；具体指标族与示例可参见 Metrics 概念文档中的 feedback 章节。

## Embedding 用量观测

具体 Embedding 入口通过共享事件总线，为现有操作发布一条不含内容的 `embedding.call`。字段包括 `attempt_id`、`outcome`、`observation_scope`、可空 Token 计数、`usage_known_fields`、`usage_complete`、`usage_quality` 和 `usage_provenance`。重试分别结算；等待文档准入时取消不算已发起的 provider attempt。

`embedding_operation` 表示适配器操作（包括本地推理），不能当作物理 HTTP 请求数。Ark 为每次 SDK 调用记录 `provider_attempt`，因此多模态 hybrid 的两次请求分别保留。SDK 调用可能在发送数据前失败，这些事件也不是完整账单。现有 query 准入、重试及模型输出保持原行为。

缺失用量保留 `null`，显式零值保留零。缺少输入计数时，不把 Embedding 的 total 推算成生成输出。字符估算仍保留在事件中，标为 `usage_quality="estimated"` 和 `usage_provenance="character_estimate"`；支持读取服务端用量的适配器优先使用已报告输入。`openviking_embedding_tokens_*` 及 ingestion Token 计数现仅累计服务端已报告的已知值，排除估算。`openviking_embedding_usage_reports_total{quality="actual|estimated|unknown"}` 展示观测质量。总 Token 计数要求输入与输出均已知。为兼容已有调用，历史 pull model 计数保留已知和估算小计；它不能当作完整账单，也不能与事件计数相加。

事件不新增提示词、响应正文、密钥、原始错误或来源文档内容。本改动不引入跨请求合批，也不把一次操作的用量分摊给不同主体。

## 相关文档

- [指标与 Metrics](../concepts/12-metrics.md) - 指标族、标签、feedback 指标与 PromQL 示例
- [VikingBot 问答效果反馈观测方案设计](https://github.com/volcengine/OpenViking/blob/main/bot/docs/zh/design/vikingbot-feedback-observability-design.md) - feedback 指标与阶段性落地背景
- [系统与监控](07-system.md) - 健康检查、系统状态与 Observer API
- [API 概览](01-overview.md) - 所有 API 端点共享约定
