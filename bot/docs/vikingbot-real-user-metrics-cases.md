# Vikingbot 真实用户问答指标验证案例

这份文档给出一组可以直接拿来执行的真实用户问答案例，用来逐项验证本次补充的 feedback / outcome 指标是否正常。

本文只覆盖当前代码里已经落地的指标口径：

- `openviking_feedback_responses_total`
- `openviking_feedback_responses_with_feedback_total`
- `openviking_feedback_events_total`
- `openviking_feedback_thumb_up_total`
- `openviking_feedback_thumb_down_total`
- `openviking_feedback_positive_outcomes_total`
- `openviking_feedback_negative_outcomes_total`
- `openviking_feedback_reasked_outcomes_total`
- `openviking_feedback_resolved_outcomes_total`
- `openviking_feedback_follow_up_without_feedback_outcomes_total`
- `openviking_feedback_coverage`
- `openviking_feedback_thumbs_up_rate`
- `openviking_feedback_thumbs_down_rate`
- `openviking_feedback_positive_feedback_rate`
- `openviking_feedback_negative_feedback_rate`
- `openviking_feedback_reask_rate`
- `openviking_feedback_one_turn_resolution_rate`
- `openviking_feedback_channel_*`

## 1. 验证前说明

当前指标不是在线自增 counter 语义，而是 scrape `/metrics` 时基于 `bot/sessions/*.jsonl` 聚合出来的快照值。

因此验证时建议遵循两个原则：

1. 优先看 total 是否按预期跳变。
2. rate 类指标只看方向是否合理，不要求每次都出现明显大幅变化。

建议所有查询都先带上 `valid="1"`。

当前分母口径说明：

1. `responses_total` 统计的是 session JSONL 中所有带 `response_id` 的 assistant 最终回答，不要求这些回答已经进入 `response_outcomes`。
2. `feedback_coverage`、`positive_feedback_rate`、`negative_feedback_rate`、`reask_rate`、`one_turn_resolution_rate` 都是基于 `responses_total` 重新聚合出来的快照比例。
3. `thumbs_up_rate`、`thumbs_down_rate` 仍然只在显式 feedback 样本内部计算，因此它们的分母是 `feedback_total`，不是 `responses_total`。

## 2. 环境前提

需要先确认：

1. `http://127.0.0.1:30300/bot/v1/health` 返回 `200`。
2. `http://127.0.0.1:30300/metrics` 能正常返回 Prometheus 文本。
3. 使用的是当前这一版已包含 feedback/outcome 聚合逻辑的服务。

建议先记一份基线值：

```promql
openviking_feedback_responses_total{valid="1"}
openviking_feedback_responses_with_feedback_total{valid="1"}
openviking_feedback_events_total{valid="1"}
openviking_feedback_thumb_up_total{valid="1"}
openviking_feedback_thumb_down_total{valid="1"}
openviking_feedback_positive_outcomes_total{valid="1"}
openviking_feedback_negative_outcomes_total{valid="1"}
openviking_feedback_reasked_outcomes_total{valid="1"}
openviking_feedback_resolved_outcomes_total{valid="1"}
openviking_feedback_follow_up_without_feedback_outcomes_total{valid="1"}
```

## 3. 案例 1：正常问答落盘

目的：验证一次普通问答至少会进入 `responses_total`。

真实用户问题：

```text
请用一句话介绍 OpenViking，控制在 20 个字以内。
```

预期 Vikingbot 回答特征：

- 正常返回文本
- 返回体中带 `response_id`
- 语义上是简短介绍

请求示例：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "real-metrics-case-01",
    "user_id": "metrics-user",
    "message": "请用一句话介绍 OpenViking，控制在 20 个字以内。"
  }'
```

重点核对指标：

```promql
openviking_feedback_responses_total{valid="1"}
```

预期结果：

1. 会变：`openviking_feedback_responses_total{valid="1"}` 通常增加 `1`；如果这是一个全新的 session 文件，`openviking_feedback_sessions_scanned_total{valid="1"}` 也可能增加 `1`。
2. 不该变：`events_total`、`thumb_up_total`、`thumb_down_total`、`responses_with_feedback_total` 以及各类 outcome total 在这一步通常不应直接增加，因为这里只发生了一次普通 `/bot/v1/chat`，没有显式 feedback，也没有 follow-up。
3. 可能不明显：如果历史样本很多，rate 类指标通常不会因为这一轮普通问答出现明显波动；这个场景优先看 total 是否跳变，不要把 rate 没变化当成异常。

## 4. 案例 2：用户点赞，验证正向反馈链路

目的：验证 `thumb_up`、`positive_feedback`、`one_turn_resolution_rate` 相关口径。

真实用户问题：

```text
帮我总结一下 OpenViking 的主要作用。
```

预期 Vikingbot 回答特征：

- 是一段简短总结
- 返回体中带 `response_id`

真实用户后续行为：

```text
这次回答有帮助，我给一个赞。
```

执行步骤：

1. 先发 `/bot/v1/chat`。
2. 记下返回里的 `response_id`。
3. 对同一条响应提交 `thumb_up`。

聊天请求：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "real-metrics-case-02",
    "user_id": "metrics-user",
    "message": "帮我总结一下 OpenViking 的主要作用。"
  }'
```

反馈请求：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/feedback" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "real-metrics-case-02",
    "response_id": "<response_id>",
    "feedback_type": "thumb_up",
    "feedback_text": "helpful"
  }'
```

重点核对指标：

```promql
openviking_feedback_events_total{valid="1"}
openviking_feedback_thumb_up_total{valid="1"}
openviking_feedback_responses_with_feedback_total{valid="1"}
openviking_feedback_positive_outcomes_total{valid="1"}
openviking_feedback_coverage{valid="1"}
openviking_feedback_thumbs_up_rate{valid="1"}
openviking_feedback_one_turn_resolution_rate{valid="1"}
```

预期结果：

1. 会变：`events_total`、`thumb_up_total`、`responses_with_feedback_total`、`positive_outcomes_total` 通常各增加 `1`；如果你先看过基线，再在一次 scrape 之后复查，这几个 total 最容易确认。
2. 不该变：`thumb_down_total`、`negative_outcomes_total`、`reasked_outcomes_total`、`follow_up_without_feedback_outcomes_total` 在这个场景通常不应直接增加，因为当前动作是对同一条 response 提交显式正向反馈，而不是差评或追问。
3. 可能不明显：`coverage`、`thumbs_up_rate`、`one_turn_resolution_rate` 通常会上升或保持不变，但如果历史 response 和历史 feedback 已经很多，显示上也可能接近不变；其中 `thumbs_up_rate` 的分母是 `feedback_total`，`one_turn_resolution_rate` 的分母是 `responses_total`，并且当前实现会把 `resolved + positive_feedback` 都算入 one-turn resolution。

## 5. 案例 3：用户点踩，验证负向反馈链路

目的：验证 `thumb_down`、`negative_feedback` 相关口径。

真实用户问题：

```text
请告诉我 OpenViking 的部署步骤，越短越好。
```

预期 Vikingbot 回答特征：

- 返回了一个部署说明
- 返回体中带 `response_id`

真实用户后续行为：

```text
这次回答不够有帮助，我给一个踩。
```

聊天请求：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "real-metrics-case-03",
    "user_id": "metrics-user",
    "message": "请告诉我 OpenViking 的部署步骤，越短越好。"
  }'
```

反馈请求：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/feedback" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "real-metrics-case-03",
    "response_id": "<response_id>",
    "feedback_type": "thumb_down",
    "feedback_text": "not helpful"
  }'
```

重点核对指标：

```promql
openviking_feedback_events_total{valid="1"}
openviking_feedback_thumb_down_total{valid="1"}
openviking_feedback_negative_outcomes_total{valid="1"}
openviking_feedback_thumbs_down_rate{valid="1"}
openviking_feedback_negative_feedback_rate{valid="1"}
```

预期结果：

1. 会变：`events_total`、`thumb_down_total`、`negative_outcomes_total` 通常各增加 `1`；如果基线样本不多，这几个 total 的跳变会比较直观。
2. 不该变：`thumb_up_total`、`positive_outcomes_total`、`reasked_outcomes_total`、`resolved_outcomes_total`、`follow_up_without_feedback_outcomes_total` 在这个场景通常不应直接增加，因为这里只提交了显式负向反馈，没有发生追问或静默结束。
3. 可能不明显：`thumbs_down_rate`、`negative_feedback_rate` 通常会上升或保持不变，但如果历史样本很多，比例变化可能很小；其中 `thumbs_down_rate` 的分母是 `feedback_total`，`negative_feedback_rate` 的分母是 `responses_total`，因此仍然优先看 total 是否按预期跳变。

## 6. 案例 4：用户马上追问，验证 `reasked`

目的：验证“无显式反馈，但 10 分钟内追问”会被判定为 `reasked`。

真实用户第一问：

```text
请解释 OpenViking 的 metrics 是做什么的。
```

预期 Vikingbot 第一轮回答特征：

- 给出解释
- 用户主观感受是“还不够具体”

真实用户第二问：

```text
我还是没明白，请再具体一点。
```

执行要求：

1. 两次 `/bot/v1/chat` 必须使用同一个 `session_id`。
2. 第二次提问必须发生在第一条 assistant 回复后的 10 分钟内。
3. 不要提交 `/bot/v1/feedback`。

第一轮请求：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "real-metrics-case-04",
    "user_id": "metrics-user",
    "message": "请解释 OpenViking 的 metrics 是做什么的。"
  }'
```

第二轮请求：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "real-metrics-case-04",
    "user_id": "metrics-user",
    "message": "我还是没明白，请再具体一点。"
  }'
```

重点核对指标：

```promql
openviking_feedback_reasked_outcomes_total{valid="1"}
openviking_feedback_reask_rate{valid="1"}
```

预期结果：

1. `reasked_outcomes_total` 增加 `1`。
2. `reask_rate` 不下降，通常会上升或保持不变；它的分母是全量历史 `responses_total`，不是已经写入 outcome 的回答子集。
3. `responses_total` 通常还会因为第二次 `/bot/v1/chat` 再增加 `1`，但真正被打上 `reasked` 的是第一轮 assistant response，而不是第二轮新生成的 response。
4. `events_total`、`thumb_up_total`、`thumb_down_total`、`responses_with_feedback_total` 通常不变，因为这个场景没有显式调用 `/bot/v1/feedback`。
5. `positive_outcomes_total`、`negative_outcomes_total`、`resolved_outcomes_total`、`follow_up_without_feedback_outcomes_total` 通常不应因为这一步直接增加；如果你看到增长，通常说明混入了其他验证样本，或第二次提问已经越过 10 分钟窗口而被判成了别的 outcome。

补充说明：

1. `reask_rate` 之所以写成“通常上升或保持不变”，而不是“必然上升”，是因为这是一个基于全量历史 response 重新聚合的比例；如果历史样本很多，分母同步变化后也可能在显示上保持不变。
2. 这个场景最关键的验证点不是第二条回复内容，而是第二次 user turn 到来后，第一条 assistant response 的 outcome 被回填成了 `reasked`。
3. `reasked` 和 `follow_up_without_feedback` 的边界是 follow-up 是否发生在上一条 assistant response 之后 10 分钟内：10 分钟内优先归类为 `reasked`，超过该窗口且又没有显式反馈时，才更可能落入 `follow_up_without_feedback`。

## 7. 案例 5：用户过一段时间再追问，验证 `follow_up_without_feedback`

目的：验证“没有点赞/点踩，也不是 10 分钟内追问”的 follow-up 会被归为 `follow_up_without_feedback`。

真实用户第一问：

```text
OpenViking 和普通向量数据库有什么关系？
```

真实用户第二问：

```text
那你能再举一个更具体的例子吗？
```

执行要求：

1. 使用同一个 `session_id`。
2. 不提交 `/bot/v1/feedback`。
3. 第二次提问必须晚于第一条 assistant 回复 10 分钟之后。

第一轮请求：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "real-metrics-case-05",
    "user_id": "metrics-user",
    "message": "OpenViking 和普通向量数据库有什么关系？"
  }'
```

第二轮请求：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "real-metrics-case-05",
    "user_id": "metrics-user",
    "message": "那你能再举一个更具体的例子吗？"
  }'
```

重点核对指标：

```promql
openviking_feedback_follow_up_without_feedback_outcomes_total{valid="1"}
```

预期结果：

1. 会变：`follow_up_without_feedback_outcomes_total` 通常增加 `1`；同时第二次 `/bot/v1/chat` 也会让 `responses_total` 再增加 `1`，但真正被补判为 `follow_up_without_feedback` 的仍然是第一轮 assistant response。
2. 不该变：`events_total`、`thumb_up_total`、`thumb_down_total`、`responses_with_feedback_total` 通常不变，因为整个场景没有显式调用 `/bot/v1/feedback`；`positive_outcomes_total`、`negative_outcomes_total`、`reasked_outcomes_total` 也通常不应在这一步直接增长。
3. 可能不明显：如果你看到增长落在 `reasked_outcomes_total`，优先检查第二次提问是不是仍在 10 分钟窗口内；rate 类指标同样可能因为历史样本较多而变化不明显，所以这个场景优先看 total 跳变。

## 8. 案例 6：用户问完就结束，验证 `resolved`

目的：补充验证“没有追问、没有负向反馈”的响应会被统计为 `resolved`。

真实用户问题：

```text
请用一句话解释什么是 OpenViking 的 readiness 指标。
```

预期 Vikingbot 回答特征：

- 给出简短说明
- 用户不继续追问
- 用户不提交 feedback

请求示例：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "real-metrics-case-06",
    "user_id": "metrics-user",
    "message": "请用一句话解释什么是 OpenViking 的 readiness 指标。"
  }'
```

重点核对指标：

```promql
openviking_feedback_resolved_outcomes_total{valid="1"}
openviking_feedback_one_turn_resolution_rate{valid="1"}
```

预期结果：

1. 会变：`resolved_outcomes_total` 有机会增加 `1`，`one_turn_resolution_rate` 也可能上升或保持不变；当前该 rate 的分母是全量 `responses_total`。
2. 不该变：`events_total`、`thumb_up_total`、`thumb_down_total`、`responses_with_feedback_total` 在这个场景通常不变，因为既没有显式 feedback，也没有后续追问；`negative_outcomes_total`、`reasked_outcomes_total`、`follow_up_without_feedback_outcomes_total` 也通常不应直接增加。
3. 可能不明显：`resolved` 相比 `thumb_up`、`thumb_down`、`reasked` 更适合作为补充验证项，因为它依赖后续 outcome 评估时机，出现时间和幅度都不如显式反馈稳定；因此看到短时间内没有立刻跳变，不一定表示链路异常。

说明：`resolved` 相比 `thumb_up`、`thumb_down`、`reasked` 更适合作为补充验证项，不建议当成唯一验收依据。

## 9. 案例 7：多 channel 验证 `openviking_feedback_channel_*`

目的：验证 channel 维度聚合是否正常。

前提：环境里已经配置了 `bot_api` 的某个 channel，例如 `demo`，对应聚合维度应为 `bot_api__demo`。

真实用户问题：

```text
请简单介绍一下 OpenViking。
```

真实用户后续行为：

```text
这次回答没帮助，我给一个踩。
```

聊天请求：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat/channel" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "real-metrics-case-07",
    "user_id": "metrics-user",
    "channel_id": "demo",
    "message": "请简单介绍一下 OpenViking。"
  }'
```

反馈请求：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/feedback" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "real-metrics-case-07",
    "channel_id": "demo",
    "response_id": "<response_id>",
    "feedback_type": "thumb_down",
    "feedback_text": "not helpful"
  }'
```

重点核对指标：

```promql
openviking_feedback_channel_events_total{channel="bot_api__demo",valid="1"}
openviking_feedback_channel_thumbs_down_rate{channel="bot_api__demo",valid="1"}
openviking_feedback_channel_negative_outcomes_total{channel="bot_api__demo",valid="1"}
openviking_feedback_channel_one_turn_resolution_rate{channel="bot_api__demo",valid="1"}
```

预期结果：

1. 会变：`channel_events_total{channel="bot_api__demo"}`、`channel_negative_outcomes_total{channel="bot_api__demo"}` 通常增加；如果你同时看 channel 维度和全局维度，两边都应体现这次负向反馈。
2. 不该变：其他无关 channel 的样本通常不应因为这次请求跳变；如果你误用的是 `/bot/v1/chat` 而不是 `/bot/v1/chat/channel`，通常也不会得到目标 `channel="bot_api__demo"` 的聚合样本。
3. 可能不明显：`channel_thumbs_down_rate{channel="bot_api__demo"}`、`channel_one_turn_resolution_rate{channel="bot_api__demo"}` 这类比例在历史 channel 样本较多时可能变化不明显，所以 channel 验收优先看该 channel 的 total 是否跳变，再看 rate 方向是否合理。

## 10. 最小验收顺序

如果只想用最少案例覆盖这次新增指标，建议按下面顺序执行：

1. 案例 1：确认问答落盘，`responses_total` 正常。
2. 案例 2：确认 `thumb_up`、`positive_outcomes_total`、`coverage`、`one_turn_resolution_rate` 正常。
3. 案例 3：确认 `thumb_down`、`negative_outcomes_total` 正常。
4. 案例 4：确认 `reasked_outcomes_total`、`reask_rate` 正常。
5. 案例 5：确认 `follow_up_without_feedback_outcomes_total` 正常。
6. 案例 6：把 `resolved` 作为补充验证。
7. 案例 7：如果你的环境启用了多 channel，再验证 channel 维度。

## 11. 通过标准

满足下面条件，可以认为本次补充的主要指标链路正常：

1. 普通问答后，`openviking_feedback_responses_total{valid="1"}` 增加。
2. 点赞后，`openviking_feedback_events_total`、`openviking_feedback_thumb_up_total`、`openviking_feedback_positive_outcomes_total` 增加。
3. 点踩后，`openviking_feedback_thumb_down_total`、`openviking_feedback_negative_outcomes_total` 增加。
4. 10 分钟内追问后，`openviking_feedback_reasked_outcomes_total` 增加。
5. 10 分钟后 follow-up 且无 feedback 时，`openviking_feedback_follow_up_without_feedback_outcomes_total` 增加。
6. 如环境启用了多 channel，`openviking_feedback_channel_*` 会按目标 channel 出现并跳变。

补充判定原则：如果 rate 没有明显跳变，但对应 total 已按预期变化，通常仍可视为链路正常，因为这些 rate 都是对全量历史回答重新聚合的快照值。

## 12. 相关文档

- `bot/docs/vikingbot-feedback-observability-design.md`
- `bot/docs/vikingbot-phase3-outcome-validation-with-openviking-server.md`
- `docs/zh/guides/12-vikingbot-metrics-validation.md`
