# 使用真实问答验证 Vikingbot 指标

完成 [Prometheus 和 Grafana 配置](11-grafana-prometheus.md) 后，用本文验证完整链路：

```text
问答 → bot 会话持久化 → feedback / follow-up → /metrics → Prometheus → Grafana
```

## 前提条件

- 启用 `server.observability.metrics.enabled=true`，通过 `openviking-server --with-bot --port 30300` 启动服务。
- 确认 `/bot/v1/health` 返回 HTTP 200，`/metrics` 返回 Prometheus exposition 文本。
- 确认 Prometheus 能抓取 OpenViking，Grafana 使用该 Prometheus 数据源。

上一篇的 localhost 方案使用 OpenViking `http://127.0.0.1:30300`、Prometheus `http://127.0.0.1:30909` 和 Grafana `http://127.0.0.1:13000`。如果启用了认证，请为每个请求添加部署要求的凭据，参见 [认证](04-authentication.md)。

## 指标口径

反馈指标是 **snapshot gauge**，包括以 `_total` 结尾的指标。`FeedbackCollector` 从 `<bot_data_path>/sessions/*.jsonl` 的 `metadata.feedback_events` 和 `metadata.response_outcomes` 聚合快照。默认刷新 TTL 为 30 秒，多次 scrape 可能复用同一份快照。对比结果前，需要等待 collector 刷新和下一次 Prometheus 抓取。

优先查看当前值或短时间内的变化，不要按 counter 使用 `rate()`。查询过滤 `valid="1"`；`valid="0"` 是采集失败后的 fallback 快照，不适合作为验收依据。gauge 与 counter 的区别见 [Prometheus 指标类型](https://prometheus.io/docs/concepts/metric_types/)。

每个场景使用新的 session ID。指标覆盖全部持久化会话，其他流量也会影响结果；历史样本多时，单次操作带来的比例变化可能很小。已有 response 的 outcome 被重新分类时，相应 outcome total 可以下降。

## 记录基线

在 Prometheus 或 Grafana Explore 的表格视图中逐条执行：

```promql
openviking_service_readiness{valid="1"}
openviking_component_health{valid="1"}
openviking_queue_pending
openviking_vikingdb_collection_vectors{valid="1"}
openviking_model_usage_available{valid="1"}
```

readiness 应为 `1`，健康组件通常为 `1`，队列积压应为零或较小值。向量数和模型统计可用性取决于服务配置，不要求在问答过程中变化。

同时记录反馈快照：

```promql
{__name__=~"openviking_feedback.*",valid="1"}
```

## 1. 发起问答，确认持久化

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-01-chat",
    "user_id": "metrics-validation-user",
    "message": "请用一句话介绍 OpenViking。"
  }'
```

预期 HTTP 200，返回 `session_id`、`response_id` 和非空 `message`。保存 response ID，后续反馈需要使用。

刷新后，`openviking_feedback_responses_total{valid="1"}` 通常增加 `1`。新 session 文件也会使 `openviking_feedback_sessions_scanned_total{valid="1"}` 增加。普通问答本身不会产生显式反馈事件。

## 2. 提交正向反馈

使用 `session_id: "realcase-02-thumb-up"` 再发起一次问答，将下面的 `<response_id>` 替换为该轮返回的 ID：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/feedback" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-02-thumb-up",
    "response_id": "<response_id>",
    "feedback_type": "thumb_up",
    "feedback_text": "helpful"
  }'
```

预期返回 `accepted: true`。对于此前没有反馈的 response，以下 gauge 通常各增加 `1`：

```promql
openviking_feedback_events_total{valid="1"}
openviking_feedback_thumb_up_total{valid="1"}
openviking_feedback_responses_with_feedback_total{valid="1"}
openviking_feedback_positive_outcomes_total{valid="1"}
```

`coverage`、`thumbs_up_rate` 和 `one_turn_resolution_rate` 也可能变化。当前实现将 `resolved` 和 `positive_feedback` 都计入一轮解决率。优先核对 total，比例取决于全部历史 response 和 outcome。

## 3. 提交负向反馈

用新 session `realcase-03-thumb-down` 发起问答，再按上面的方式提交反馈，使用该轮 response ID，并设置 `feedback_type: "thumb_down"`、`feedback_text: "not helpful"`。

刷新后，以下 total 通常各增加 `1`：

```promql
openviking_feedback_events_total{valid="1"}
openviking_feedback_thumb_down_total{valid="1"}
openviking_feedback_negative_outcomes_total{valid="1"}
```

`openviking_feedback_thumbs_down_rate` 和 `openviking_feedback_negative_feedback_rate` 也可能变化。仅这次操作不应增加正向反馈或 reask total。

## 4. 十分钟内追问

用 `realcase-04-reask` 发起问答，不提交反馈，在 assistant 回复后的十分钟内向同一 session 发送第二条消息：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-04-reask",
    "user_id": "metrics-validation-user",
    "message": "请解释得更具体一些。"
  }'
```

**上一条** assistant response 会被分类为 `reasked`。该判断依据时间，不会检查两次问题的语义相似度。

```promql
openviking_feedback_reasked_outcomes_total{valid="1"}
openviking_feedback_reask_rate{valid="1"}
```

reasked total 通常增加 `1`。第二次 assistant 回复也会增加 `responses_total`，显式反馈 total 应保持不变。

## 5. 超过十分钟再追问

用 `realcase-05-followup-no-feedback` 发起问答，不提交反馈，在 assistant 回复后等待 **超过十分钟**，再向同一 session 发送消息。

```promql
openviking_feedback_follow_up_without_feedback_outcomes_total{valid="1"}
```

上一条 response 应被分类为 `follow_up_without_feedback`，对应 total 增加 `1`。恰好十分钟或更早的 follow-up 仍属于 `reasked`；显式反馈 total 保持不变。

## 6. 理解 `resolved`

没有后续 user 消息的 response 会在 **outcome evaluator 执行时** 被分类为 `resolved`。metrics collector 只读取已保存的 outcome，不会运行静默计时器，也不会在十分钟后自动生成 resolved outcome。

```promql
openviking_feedback_resolved_outcomes_total{valid="1"}
```

因此，发起问答后保持静默不能作为该指标的确定性验收用例。如果预期存在 resolved 样本，请检查持久化的 `metadata.response_outcomes`。可重复验收应使用上面的正向、负向和追问场景。

## 7. 验证 channel 维度

此场景要求已启用 ID 为 `demo` 的 `bot_api` channel。通过 `/bot/v1/chat/channel` 发起问答：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat/channel" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-07-channel-demo",
    "user_id": "metrics-validation-user",
    "channel_id": "demo",
    "message": "请简要介绍 OpenViking。"
  }'
```

使用返回的 response ID 提交反馈，并带上相同的 `channel_id`：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/feedback" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-07-channel-demo",
    "response_id": "<response_id>",
    "channel_id": "demo",
    "feedback_type": "thumb_down",
    "feedback_text": "not helpful"
  }'
```

查询目标 channel：

```promql
openviking_feedback_channel_events_total{channel="bot_api__demo",valid="1"}
openviking_feedback_channel_negative_outcomes_total{channel="bot_api__demo",valid="1"}
openviking_feedback_channel_thumbs_down_rate{channel="bot_api__demo",valid="1"}
```

该 channel 的 event 和 negative-outcome total 应增加；其他 channel 不应因这次请求变化。未配置对应 channel 时会返回 `404`；普通 `/bot/v1/chat` 不经过此 channel 路由。

## 验收与排障

最小验收要求 readiness 为 `1`、问答已持久化、正负反馈 total 增加，以及十分钟内追问后 reasked total 增加。使用 channel 的部署再单独验证 channel total。

| 现象 | 检查项 |
| --- | --- |
| 问答成功，反馈 total 不变 | 是否提交了显式反馈；普通问答不会创建反馈事件。 |
| 看不到刚提交的事件 | 等待 30 秒 collector TTL、后台刷新和下一次 Prometheus 抓取，再检查会话持久化。 |
| total 变化，比例几乎不变 | 历史样本会稀释单次变化，先在表格视图比较当前 total。 |
| 只有 `valid="0"` | 检查 collector 错误和 bot session 目录的访问权限。 |
| 没有 `bot_api__demo` | 检查 channel 是否启用、是否调用 `/chat/channel`，以及反馈中的 `channel_id` 是否一致。 |

## 相关文档

- [可观测性与排障](05-observability.md)
- [Prometheus 和 Grafana 配置](11-grafana-prometheus.md)
- [指标与 Metrics](../concepts/12-metrics.md)
- [Metrics API](../api/09-metrics.md)
