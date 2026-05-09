# 指标与 Metrics

OpenViking 提供 `/metrics` 端点，用于向 Prometheus、Grafana Agent 等监控系统导出运行时指标。

与 `/api/v1/observer/*` 和 `/api/v1/stats/*` 不同，`/metrics` 的定位是：

- 面向机器抓取，而不是面向人工阅读
- 返回 Prometheus exposition 文本，而不是统一 JSON 包装
- 偏系统运行态与服务运行质量，不承担业务分析接口职责

## API 参考

### metrics()

导出当前进程内的 Prometheus 指标文本。

该端点通常被 Prometheus 定时抓取，也可以用于本地调试或手工排查。

**认证**

- 当前实现中，`/metrics` 未接入 `get_request_context` 等鉴权依赖，因此可直接访问。
- 也就是说，从代码实现角度看，`/metrics` 当前等价于公开抓取端点。
- 如果后续通过网关、反向代理或服务端策略收紧访问控制，应以实际部署配置为准。

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
- `/metrics` 返回的是 Prometheus 文本，不是标准 OpenViking API 的 `{status, result, time}` JSON 结构。
- 业务分析类统计（如 memory health、staleness、session extraction）应继续使用 `/api/v1/stats/*`，而不是迁移到 `/metrics`。
- 人工查看组件瞬时状态更适合使用 `/api/v1/observer/*`。

## 相关文档

- [系统与监控](07-system.md) - 健康检查、系统状态与 Observer API
- [API 概览](01-overview.md) - 所有 API 端点共享约定
