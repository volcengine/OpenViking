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

## VLM 已上报的缓存与 reasoning 子集

官方 `vlm.call` 事件新增可空的 `prompt_cached_tokens` 与
`completion_reasoning_tokens`。未上报或无效值保持 `null`，真实上报的 0 仍为 0。
OpenAI、LiteLLM、VolcEngine Chat Completions 与媒体用量复用同一规范化入口。
[DeepSeek Chat Completions 契约](https://api-docs.deepseek.com/api/create-chat-completion/)
将 `prompt_cache_hit_tokens` 与 `prompt_tokens_details.cached_tokens` 定义为同一事实，
这里只作别名兼容，不相加。别名冲突、非整数、负数或大于所属输入/输出的值保持未知。
缓存未命中与缓存写入不作为缓存命中。

现有 VLM collector 新增三组指标，标签为 `provider`、`model_name` 和有界的
`subset`（`cached_prompt` 或 `reasoning_completion`）：

| 指标 | 含义 |
| --- | --- |
| `openviking_vlm_usage_subset_tokens_total` | 实际上报的子集 Token |
| `openviking_vlm_usage_subset_parent_tokens_total` | 仅上报该子集的调用对应的输入/输出 Token |
| `openviking_vlm_usage_subset_reports_total` | 上报有效值的成功事件数，包含真实 0 |

现有输入、输出和总 Token 的含义不变，**不能把子集再加进总 Token**。
未知字段不增加子集上报计数；有明确 provider-attempt 归属的失败/取消流会保留已报告用量，旧失败事件仍不推断用量。0 不递增只允许正增量的
registry Counter；通过上报计数区分真实 0 和缺失。

在父项分母大于 0 时，可计算已上报调用的缓存命中比例：

```promql
(
  rate(openviking_vlm_usage_subset_tokens_total{subset="cached_prompt"}[5m])
  or
  0 * rate(openviking_vlm_usage_subset_reports_total{subset="cached_prompt"}[5m])
)
/
rate(openviking_vlm_usage_subset_parent_tokens_total{subset="cached_prompt"}[5m])
```

分母不能混入未上报缓存用量的调用，否则会低估命中比例。只有存在上报证据时才补 0。
上报覆盖可按相同 provider/model/account 筛选与成功的 VLM 事件数比较，但不代表全部
物理 RPC 的覆盖。既有尽力交付、provider 用量缺失、进程重启及抓取间隙仍是限制。
这些指标不是持久收据账本或完整账单，也不包含 workflow/performance Console API/UI。

开启账户指标时，新指标遵循现有 allowlist 和基数策略（如 `openviking_vlm_*`），
不记录提示、回答、任务 ID 或新增无界标签。


## VLM 调用尝试事实

现有 `vlm.call` 在实际接入的 SDK 调用点只结算一次；缺少 usage、失败、内容拒绝和
取消也会生成事件。新增 `attempt_id`（与 `event_id` 相同）、`api`、`outcome/status`、
`observation_scope`、`usage_known_fields`、`usage_complete` 和逐字段 `usage_provenance`。
输入/输出与缓存/reasoning 均可为 `null`；未知不会转成实测 0。完整性要求两个主字段
均有有效报告，且调用没有失败/中断；局部字段仍可保留。重复 usage 帧是快照，不累加
成多次调用。工具响应的 usage 字典省略未知主字段，明确报告的 0 仍保留。

`model_name` 对应选中的 SDK 请求型号，包括流式覆盖或 LiteLLM 路由前缀。事件不包含
提示、模型回复、凭据或错误正文，沿用既有 Task 归属。主字段计数器分别累加已有报告；
总 Token 计数仅覆盖两个主字段均已知的群体。失败/取消前已报告的流用量会计入，不能
把缓存/reasoning 子集再次加到总量。只有子集与其父字段都已知，才进入命中率分母。

OpenAI/Azure、继承路径 Kimi/GLM、LiteLLM、Ark 文本/视觉及 Responses 模型调用共用
同一观测 owner。文件上传、轮询及删除不属于模型推理。Codex 内部 Responses/auth
适配可能包含多次物理请求，显式标为 `adapter_call`，不假称全部 RPC；普通遗留更新
标为 `legacy_usage`。RetryVLM/降级包装不额外重算用量，不改变重试、拒绝或取消策略。

`provider_attempt` 是关闭 SDK 内部重试后的一次 SDK 调用尝试，可能在发送字节前就
失败，不是已计费物理 RPC 的证明。Embedding/rerank 与共享批次分配仍由各自 owner
负责，本改动不会从一个共享调用虚构多个 Task 的用量。

新增 `openviking_vlm_usage_field_reports_total`，固定 `field` 与 `state=known/unknown`
表示报告覆盖，不以 attempt ID 为指标标签。旧累计/操作遥测继续提供已知小计，不能
证明完整性；对账应读取官方事件中的空值和覆盖信息。既有远程出口保留这些字段，
自定义消费者需兼容可空主用量，不能强转为实测 0。事件交付仍为尽力而为，不是完整账单。
