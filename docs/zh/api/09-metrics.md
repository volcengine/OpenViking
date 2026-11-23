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

## 相关文档

- [指标与 Metrics](../concepts/12-metrics.md) - 指标族、标签、feedback 指标与 PromQL 示例
- [VikingBot 问答效果反馈观测方案设计](https://github.com/volcengine/OpenViking/blob/main/bot/docs/zh/design/vikingbot-feedback-observability-design.md) - feedback 指标与阶段性落地背景
- [系统与监控](07-system.md) - 健康检查、系统状态与 Observer API
- [API 概览](01-overview.md) - 所有 API 端点共享约定
