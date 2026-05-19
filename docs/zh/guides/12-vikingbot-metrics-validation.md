# 使用真实问答验证 Vikingbot 指标

这份文档用于验证一条完整链路是否已经打通：

```text
用户提问 -> /bot/v1/chat -> Vikingbot 会话持久化 -> /bot/v1/feedback 或 follow-up -> /metrics -> Prometheus -> Grafana
```

它和 [使用 Prometheus 和 Grafana 查看 OpenViking 指标](11-grafana-prometheus.md) 的区别是：

- `11-grafana-prometheus.md` 解决的是“怎么把链路搭起来”
- 本文解决的是“链路搭起来以后，怎么用真实问答场景确认指标真的在变”

本文只使用当前仓库里已经确认存在、而且比较容易观察到的指标，尤其是：

- `openviking_service_readiness`
- `openviking_component_health`
- `openviking_queue_pending`
- `openviking_vikingdb_collection_vectors`
- `openviking_model_usage_available`
- `openviking_feedback_*`
- `openviking_feedback_channel_*`

## 前提条件

开始前请先确认：

- OpenViking Server 已启动，并且启用了 `server.observability.metrics.enabled=true`
- Vikingbot 已通过 `openviking-server --with-bot` 启动
- `curl http://127.0.0.1:30300/bot/v1/health` 返回 `200`
- `curl http://127.0.0.1:30300/metrics` 能返回 Prometheus exposition 文本
- Prometheus 已经能抓到 OpenViking
- Grafana 已经连上 Prometheus

如果你使用的是本文前面已经补好的 Linux localhost 方案，默认地址是：

- Prometheus: `http://127.0.0.1:30909`
- Grafana: `http://127.0.0.1:13000`
- OpenViking: `http://127.0.0.1:30300`

## 先理解这批反馈指标的口径

这一步很重要，否则很容易把现象看错。

当前仓库里，Vikingbot 反馈相关指标不是在线单调累加 counter，而是 `scrape-time snapshot gauge`：Prometheus 每次抓取 `/metrics` 时，`FeedbackCollector` 会去扫描 `bot/sessions/*.jsonl`，从持久化 session 的 `metadata.feedback_events` 和 `metadata.response_outcomes` 里重新聚合一份最新快照。

这意味着：

- 更适合看“当前总量/占比/按 channel 分布”
- 更适合直接看当前值或短时间内的阶梯变化
- 不建议把它当成普通 counter 去优先看 `rate()`
- 查询时建议优先过滤 `valid="1"`

可以先在 Prometheus 或 Grafana Explore 里执行：

```promql
{__name__=~"openviking_feedback.*"}
```

如果你看到的是 `valid="0"`，说明当前样本是 fallback/stale 快照，不建议把它当成主验证结果。

## 按真实问答场景执行的验收用例

如果你不想按“指标类别”理解文档，而是更希望像真实用户一样逐轮发问、观察 Vikingbot 回复、再去 Prometheus / Grafana 里核对指标变化，可以直接按下面这 7 个场景顺序执行。

建议使用一组全新的 `session_id`，这样更容易看出单次操作带来的阶梯变化。下面示例统一使用：

- `realcase-01-chat`
- `realcase-02-thumb-up`
- `realcase-03-thumb-down`
- `realcase-04-reask`
- `realcase-05-followup-no-feedback`
- `realcase-06-resolved`
- `realcase-07-channel-demo`

### 场景 1：真实用户先发一条正常问题，验证会话落盘与响应计数

用户提问：

```text
请用一句话介绍 OpenViking，控制在 20 个字以内。
```

预期 Vikingbot 回复特征：

- 返回 200
- 返回体包含 `session_id`
- 返回体包含 `response_id`
- `message` 非空

执行命令：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-01-chat",
    "user_id": "metrics-validation-user",
    "message": "请用一句话介绍 OpenViking，控制在 20 个字以内。"
  }'
```

重点观察：

- `openviking_feedback_responses_total{valid="1"}`
- `openviking_feedback_sessions_scanned_total{valid="1"}`

PromQL：

```promql
openviking_feedback_responses_total{valid="1"}
```

```promql
openviking_feedback_sessions_scanned_total{valid="1"}
```

预期变化：

- 会变：`responses_total` 通常增加 `1`；如果这是全新的 session 文件，`sessions_scanned_total` 也可能增加 `1`。
- 不该变：`events_total`、`thumb_up_total`、`thumb_down_total`、`responses_with_feedback_total` 以及各类 outcome total 在这一步通常不应直接增加，因为这里只发生了一次普通问答，没有显式 feedback，也没有 follow-up。
- 可能不明显：rate 类指标通常不会因为这一轮普通问答出现明显波动；这个场景优先看 total 是否跳变，不要把 rate 没变化当成异常。

### 场景 2：用户认为回答有帮助，点一个赞，验证正向反馈链路

用户提问：

```text
帮我总结一下 OpenViking 的主要作用。
```

预期 Vikingbot 回复特征：

- 会给出一段简短介绍
- 返回体里能拿到 `response_id`

随后用户行为：

```text
这次回答有帮助，我给一个赞。
```

执行步骤：

1. 先调用 `/bot/v1/chat` 发送问题。
2. 记录返回里的 `response_id`。
3. 再调用 `/bot/v1/feedback` 提交 `thumb_up`。

聊天命令：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-02-thumb-up",
    "user_id": "metrics-validation-user",
    "message": "帮我总结一下 OpenViking 的主要作用。"
  }'
```

反馈命令：

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

重点观察：

- `openviking_feedback_events_total{valid="1"}`
- `openviking_feedback_thumb_up_total{valid="1"}`
- `openviking_feedback_responses_with_feedback_total{valid="1"}`
- `openviking_feedback_positive_outcomes_total{valid="1"}`
- `openviking_feedback_coverage{valid="1"}`
- `openviking_feedback_thumbs_up_rate{valid="1"}`
- `openviking_feedback_one_turn_resolution_rate{valid="1"}`

PromQL：

```promql
openviking_feedback_events_total{valid="1"}
```

```promql
openviking_feedback_thumb_up_total{valid="1"}
```

```promql
openviking_feedback_positive_outcomes_total{valid="1"}
```

```promql
openviking_feedback_one_turn_resolution_rate{valid="1"}
```

预期变化：

- 会变：`feedback_events_total`、`thumb_up_total`、`responses_with_feedback_total`、`positive_outcomes_total` 通常各增加 `1`。
- 不该变：`thumb_down_total`、`negative_outcomes_total`、`reasked_outcomes_total`、`follow_up_without_feedback_outcomes_total` 在这个场景通常不应直接增加，因为当前动作是对同一条 response 提交显式正向反馈，而不是差评或追问。
- 可能不明显：`coverage`、`thumbs_up_rate`、`one_turn_resolution_rate` 通常会上升或保持不变，但如果历史 response 和历史 feedback 已经很多，显示上也可能接近不变；其中 `one_turn_resolution_rate` 会受当前实现把 `positive_feedback` 也计入 one-turn resolution 的影响。

### 场景 3：用户认为回答没帮助，点一个踩，验证负向反馈链路

用户提问：

```text
请告诉我 OpenViking 的部署步骤，越短越好。
```

预期 Vikingbot 回复特征：

- 会给出一个简短部署说明
- 返回体里能拿到 `response_id`

随后用户行为：

```text
这次回答不够有帮助，我给一个踩。
```

执行步骤：

1. 先调用 `/bot/v1/chat`。
2. 记录 `response_id`。
3. 调用 `/bot/v1/feedback`，提交 `thumb_down`。

聊天命令：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-03-thumb-down",
    "user_id": "metrics-validation-user",
    "message": "请告诉我 OpenViking 的部署步骤，越短越好。"
  }'
```

反馈命令：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/feedback" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-03-thumb-down",
    "response_id": "<response_id>",
    "feedback_type": "thumb_down",
    "feedback_text": "not helpful"
  }'
```

重点观察：

- `openviking_feedback_events_total{valid="1"}`
- `openviking_feedback_thumb_down_total{valid="1"}`
- `openviking_feedback_negative_outcomes_total{valid="1"}`
- `openviking_feedback_thumbs_down_rate{valid="1"}`
- `openviking_feedback_negative_feedback_rate{valid="1"}`

PromQL：

```promql
openviking_feedback_thumb_down_total{valid="1"}
```

```promql
openviking_feedback_negative_outcomes_total{valid="1"}
```

```promql
openviking_feedback_negative_feedback_rate{valid="1"}
```

预期变化：

- 会变：`thumb_down_total`、`negative_outcomes_total` 通常各增加 `1`，`events_total` 也应随这次显式 feedback 增加 `1`。
- 不该变：`thumb_up_total`、`positive_outcomes_total`、`reasked_outcomes_total`、`resolved_outcomes_total`、`follow_up_without_feedback_outcomes_total` 在这个场景通常不应直接增加，因为这里只提交了显式负向反馈，没有发生追问或静默结束。
- 可能不明显：`thumbs_down_rate`、`negative_feedback_rate` 通常会上升或保持不变，但如果历史样本很多，比例变化可能很小，因此仍然优先看 total 是否按预期跳变。

### 场景 4：用户没有显式反馈，但马上追问，验证 `reasked`

用户第一问：

```text
请解释 OpenViking 的 metrics 是做什么的。
```

Vikingbot 第一轮回复特征：

- 会给出解释，但用户主观上仍觉得不够具体

用户第二问：

```text
我还是没明白，请再具体一点。
```

执行步骤：

1. 用同一个 `session_id` 先发第一问。
2. 不要调用 `/bot/v1/feedback`。
3. 在 10 分钟内用同一个 `session_id` 发第二问。

第一轮命令：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-04-reask",
    "user_id": "metrics-validation-user",
    "message": "请解释 OpenViking 的 metrics 是做什么的。"
  }'
```

第二轮命令：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-04-reask",
    "user_id": "metrics-validation-user",
    "message": "我还是没明白，请再具体一点。"
  }'
```

重点观察：

- `openviking_feedback_reasked_outcomes_total{valid="1"}`
- `openviking_feedback_reask_rate{valid="1"}`

PromQL：

```promql
openviking_feedback_reasked_outcomes_total{valid="1"}
```

```promql
openviking_feedback_reask_rate{valid="1"}
```

预期变化：

- `reasked_outcomes_total` 通常增加 `1`
- `reask_rate` 通常会上升，或在历史样本很多时保持不变
- 核心不是第二条回复内容本身，而是第一条 assistant response 的 outcome 被评估成了 `reasked`
- `responses_total` 通常还会因为第二次 `/bot/v1/chat` 再增加 `1`，但这不影响“上一条 response 被改判为 `reasked`”这个核心判断
- `events_total`、`thumb_up_total`、`thumb_down_total`、`responses_with_feedback_total` 通常不变，因为这个场景没有显式 feedback
- `positive_outcomes_total`、`negative_outcomes_total`、`resolved_outcomes_total` 通常不应因为这一步直接增加

补充说明：

1. `reask_rate` 不是严格单调变化的在线 counter 比率，而是基于当前持久化 session 快照重新聚合出来的占比，所以更适合看“是否不下降、是否出现阶梯变化”。
2. 如果第二次 follow-up 已经超过 10 分钟窗口，且仍然没有显式 feedback，那么它更可能落入 `follow_up_without_feedback`，而不是 `reasked`。
3. 如果你在这个场景里看到了 `follow_up_without_feedback_outcomes_total` 增加，优先检查是不是第二次提问时间已经超过 10 分钟，或者混入了别的 session 样本。

### 场景 5：用户不点赞也不点踩，隔一段时间再追问，验证 `follow_up_without_feedback`

用户第一问：

```text
OpenViking 和普通向量数据库有什么关系？
```

Vikingbot 第一轮回复特征：

- 会给出概念性回答

用户稍后继续问：

```text
那你能再举一个更具体的例子吗？
```

执行步骤：

1. 用新的 `session_id` 发第一问。
2. 不调用 `/bot/v1/feedback`。
3. 至少等待 10 分钟以后，再用同一个 `session_id` 继续发 follow-up。

说明：当前实现里，10 分钟内的 follow-up 会优先被归类为 `reasked`；只有超过这个窗口、且没有显式反馈时，才会落入 `follow_up_without_feedback`。

第一轮命令：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-05-followup-no-feedback",
    "user_id": "metrics-validation-user",
    "message": "OpenViking 和普通向量数据库有什么关系？"
  }'
```

第二轮命令：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-05-followup-no-feedback",
    "user_id": "metrics-validation-user",
    "message": "那你能再举一个更具体的例子吗？"
  }'
```

重点观察：

- `openviking_feedback_follow_up_without_feedback_outcomes_total{valid="1"}`

PromQL：

```promql
openviking_feedback_follow_up_without_feedback_outcomes_total{valid="1"}
```

预期变化：

- 会变：`follow_up_without_feedback_outcomes_total` 通常增加 `1`；同时第二次 `/bot/v1/chat` 也会让 `responses_total` 再增加 `1`，但真正被补判为 `follow_up_without_feedback` 的仍然是第一轮 assistant response。
- 不该变：`events_total`、`thumb_up_total`、`thumb_down_total`、`responses_with_feedback_total` 通常不变，因为整个场景没有显式调用 `/bot/v1/feedback`；`positive_outcomes_total`、`negative_outcomes_total`、`reasked_outcomes_total` 也通常不应在这一步直接增长。
- 可能不明显：如果你看到增长落在 `reasked_outcomes_total`，优先检查第二次提问是不是仍在 10 分钟窗口内；rate 类指标同样可能因为历史样本较多而变化不明显，所以这个场景优先看 total 跳变。

### 场景 6：用户问完就结束，不再追问，验证 `resolved`

用户提问：

```text
请用一句话解释什么是 OpenViking 的 readiness 指标。
```

预期 Vikingbot 回复特征：

- 会给出短回答
- 用户不继续追问，也不提交反馈

执行步骤：

1. 新开一个 `session_id` 发一轮问答。
2. 不调用 `/bot/v1/feedback`。
3. 不继续发 follow-up。
4. 等待系统完成持久化与后续 outcome 评估，再观察指标。

命令：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-06-resolved",
    "user_id": "metrics-validation-user",
    "message": "请用一句话解释什么是 OpenViking 的 readiness 指标。"
  }'
```

重点观察：

- `openviking_feedback_resolved_outcomes_total{valid="1"}`

PromQL：

```promql
openviking_feedback_resolved_outcomes_total{valid="1"}
```

预期变化：

- 会变：`resolved_outcomes_total` 有机会增加 `1`，`one_turn_resolution_rate` 也可能上升或保持不变。
- 不该变：`events_total`、`thumb_up_total`、`thumb_down_total`、`responses_with_feedback_total` 在这个场景通常不变，因为既没有显式 feedback，也没有后续追问；`negative_outcomes_total`、`reasked_outcomes_total`、`follow_up_without_feedback_outcomes_total` 也通常不应直接增加。
- 可能不明显：`resolved` 相比 `thumb_up`、`thumb_down`、`reasked` 更适合作为补充验证项，因为它依赖后续 outcome 评估时机，出现时间和幅度都不如显式反馈稳定；因此看到短时间内没有立刻跳变，不一定表示链路异常。

### 场景 7：真实多 channel 用户，从指定 channel 访问，验证 `openviking_feedback_channel_*`

前提：

- 你的 bot 配置中已经启用了 `channel_id="demo"` 对应的 `bot_api` channel

用户提问：

```text
请简单介绍一下 OpenViking。
```

预期 Vikingbot 回复特征：

- 请求需要走 `POST /bot/v1/chat/channel`
- 返回样本最终应落到 `channel="bot_api__demo"`

执行步骤：

1. 用 `POST /bot/v1/chat/channel` 发起聊天。
2. 记录返回中的 `response_id`。
3. 调用 `/bot/v1/feedback`，带上同一个 `channel_id`。
4. 对照 channel 维度指标。

聊天命令：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat/channel" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "realcase-07-channel-demo",
    "user_id": "metrics-validation-user",
    "channel_id": "demo",
    "message": "请简单介绍一下 OpenViking。"
  }'
```

反馈命令：

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

重点观察：

- `openviking_feedback_channel_events_total{channel="bot_api__demo",valid="1"}`
- `openviking_feedback_channel_thumbs_down_rate{channel="bot_api__demo",valid="1"}`
- `openviking_feedback_channel_negative_outcomes_total{channel="bot_api__demo",valid="1"}`

PromQL：

```promql
openviking_feedback_channel_events_total{channel="bot_api__demo",valid="1"}
```

```promql
openviking_feedback_channel_thumbs_down_rate{channel="bot_api__demo",valid="1"}
```

```promql
openviking_feedback_channel_negative_outcomes_total{channel="bot_api__demo",valid="1"}
```

```promql
openviking_feedback_channel_events_total{valid="1"}
```

预期变化：

- 会变：能看到 `channel="bot_api__demo"` 这一组样本，并且该 channel 下的 `events_total`、`negative_outcomes_total` 等 total 会随这次问答和负向反馈跳变。
- 不该变：其他无关 channel 的样本通常不应因为这次请求跳变；如果你误用的是 `POST /bot/v1/chat` 而不是 `POST /bot/v1/chat/channel`，通常也不会得到目标 `bot_api__demo` 的聚合样本。
- 可能不明显：`channel_thumbs_down_rate`、`channel_one_turn_resolution_rate` 这类比例在历史 channel 样本较多时可能变化不明显，所以 channel 验收优先看该 channel 的 total 是否跳变，再看 rate 方向是否合理。

如果你不确定当前环境里有哪些可用 `channel_id`，可以先检查 bot 配置里启用的 `bot_api` channel；若配置不存在，对应请求会返回 `404 Channel '<channel_id>' not found`。

### 一次性人工验收的推荐顺序

如果你只想跑一遍最实用的人工验收，建议按下面顺序执行：

1. 跑“场景 1”，确认 `/bot/v1/chat`、会话持久化和 `responses_total` 正常。
2. 跑“场景 2”，确认 `thumb_up`、`positive_outcomes_total`、`coverage` 正常。
3. 跑“场景 3”，确认 `thumb_down`、`negative_outcomes_total` 正常。
4. 跑“场景 4”，确认 `reasked_outcomes_total` 正常。
5. 跑“场景 5”，确认 `follow_up_without_feedback_outcomes_total` 正常。
6. 跑“场景 6”，把 `resolved` 作为补充验证。
7. 如果你的环境启用了 `bot_api` channel，再跑“场景 7”验证 channel 维度指标。

## 第 0 步：先看基线指标

在真正发问答之前，先确认基础链路指标是活的。

推荐依次执行下面这些查询。

服务 readiness：

```promql
openviking_service_readiness{valid="1"}
```

预期：返回 `1`。

组件健康：

```promql
openviking_component_health{valid="1"}
```

预期：

- 至少能看到 `queue`、`vikingdb` 等 component
- 正常情况下大多数值为 `1`

队列积压：

```promql
openviking_queue_pending
```

预期：

- 通常为 `0` 或较小值
- 如果此时已经长期大于 `0`，后面做问答验证时要注意区分“新流量触发”还是“原本就有积压”

VikingDB collection 当前向量数：

```promql
openviking_vikingdb_collection_vectors{valid="1"}
```

预期：

- 至少存在一部分 collection 样本
- 数值不一定在本次验证中变化，但它能证明这类状态指标已经被 Prometheus 抓到

模型使用统计可用性：

```promql
openviking_model_usage_available{valid="1"}
```

预期：

- 可能看到 `model_type="vlm"`、`embedding`、`rerank`
- 值为 `1` 表示对应统计当前可用

反馈快照总览：

```promql
openviking_feedback_sessions_scanned_total{valid="1"}
```

```promql
openviking_feedback_responses_total{valid="1"}
```

```promql
openviking_feedback_events_total{valid="1"}
```

预期：

- 如果你的环境已有历史 bot session，数值通常大于 `0`
- 如果是全新环境，也可能是 `0`

建议把下面这些值先记下来，后面每个场景都拿它们做对比：

- `openviking_feedback_responses_total{valid="1"}`
- `openviking_feedback_responses_with_feedback_total{valid="1"}`
- `openviking_feedback_events_total{valid="1"}`
- `openviking_feedback_thumb_up_total{valid="1"}`
- `openviking_feedback_thumb_down_total{valid="1"}`
- `openviking_feedback_positive_outcomes_total{valid="1"}`
- `openviking_feedback_negative_outcomes_total{valid="1"}`
- `openviking_feedback_reasked_outcomes_total{valid="1"}`
- `openviking_feedback_resolved_outcomes_total{valid="1"}`
- `openviking_feedback_follow_up_without_feedback_outcomes_total{valid="1"}`

## 第 1 步：先做一轮最小聊天，确认 bot 会话能落盘

这个场景不是为了验证反馈指标，而是为了确认 `/bot/v1/chat` 这条链路本身正常。

用户输入示例：

```text
请用一句话介绍 OpenViking，控制在 20 个字以内。
```

期望的 Vikingbot 回复特征：

- 返回 200
- 返回体里包含 `session_id`
- 返回体里包含 `response_id`
- `message` 非空

执行命令：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "metrics-validation-session-a",
    "user_id": "metrics-validation-user",
    "message": "请用一句话介绍 OpenViking，控制在 20 个字以内。"
  }'
```

返回体示例形态：

```json
{
  "session_id": "metrics-validation-session-a",
  "response_id": "<response_id>",
  "message": "..."
}
```

这一轮之后，最值得观察的不是点赞/踩指标，而是：

```promql
openviking_feedback_responses_total{valid="1"}
```

预期：

- 相比第 0 步基线，通常增加 `1`
- 如果 scrape 还没刷新，等 15-30 秒再看一次

可选观察：

```promql
openviking_queue_pending
```

预期：

- 可能短暂波动，但不保证一定变化
- 它更适合用来确认系统没有因为 bot 请求出现异常积压

## 第 2 步：显式 thumb up，验证正向反馈指标

这是最推荐先做的主路径，因为现象最稳定、最好理解。

用户问答场景：

1. 用户提问：

```text
帮我总结一下 OpenViking 的作用。
```

2. Vikingbot 给出一段简短回答。

3. 用户主观判断“这次回答有帮助”，提交 `thumb_up`。

先发起聊天：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "metrics-validation-positive",
    "user_id": "metrics-validation-user",
    "message": "帮我总结一下 OpenViking 的作用。"
  }'
```

记下返回里的 `response_id`，然后提交反馈：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/feedback" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "metrics-validation-positive",
    "response_id": "<response_id>",
    "feedback_type": "thumb_up",
    "feedback_text": "helpful"
  }'
```

期望反馈返回：

```json
{
  "accepted": true,
  "response_id": "<response_id>",
  "session_id": "metrics-validation-positive",
  "feedback_type": "thumb_up",
  "feedback_delay_sec": 1.234,
  "timestamp": "..."
}
```

重点看这些查询。

反馈事件总量：

```promql
openviking_feedback_events_total{valid="1"}
```

点赞总量：

```promql
openviking_feedback_thumb_up_total{valid="1"}
```

带反馈响应数：

```promql
openviking_feedback_responses_with_feedback_total{valid="1"}
```

正向 outcome 总量：

```promql
openviking_feedback_positive_outcomes_total{valid="1"}
```

覆盖率：

```promql
openviking_feedback_coverage{valid="1"}
```

点赞率：

```promql
openviking_feedback_thumbs_up_rate{valid="1"}
```

一轮解决率：

```promql
openviking_feedback_one_turn_resolution_rate{valid="1"}
```

预期变化：

- `openviking_feedback_events_total{valid="1"}` 增加 `1`
- `openviking_feedback_thumb_up_total{valid="1"}` 增加 `1`
- `openviking_feedback_responses_with_feedback_total{valid="1"}` 通常增加 `1`
- `openviking_feedback_positive_outcomes_total{valid="1"}` 通常增加 `1`
- `openviking_feedback_coverage{valid="1"}` 可能上升，也可能不变，取决于历史总样本
- `openviking_feedback_thumbs_up_rate{valid="1"}` 可能上升，也可能不变，取决于历史反馈结构
- `openviking_feedback_one_turn_resolution_rate{valid="1"}` 可能上升，因为当前实现把 `resolved + positive_feedback` 都计入 one-turn resolution

如果你想看“这次操作是否已经刷新进 Prometheus”，最稳妥的做法是把查询改成表格视图，连续刷新 1-2 次，而不是一开始就看时间序列折线。

## 第 3 步：显式 thumb down，验证负向反馈指标

第二个主路径是 `thumb_down`，它同样稳定，而且和正向反馈形成对照。

用户问答场景：

1. 用户提问：

```text
请告诉我 OpenViking 的部署步骤，越短越好。
```

2. Vikingbot 返回一个回答。

3. 假设用户认为这次回答没有帮助，提交 `thumb_down`。

先发聊天：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "metrics-validation-negative",
    "user_id": "metrics-validation-user",
    "message": "请告诉我 OpenViking 的部署步骤，越短越好。"
  }'
```

再提交踩：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/feedback" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "metrics-validation-negative",
    "response_id": "<response_id>",
    "feedback_type": "thumb_down",
    "feedback_text": "not helpful"
  }'
```

重点查询。

```promql
openviking_feedback_events_total{valid="1"}
```

```promql
openviking_feedback_thumb_down_total{valid="1"}
```

```promql
openviking_feedback_negative_outcomes_total{valid="1"}
```

```promql
openviking_feedback_thumbs_down_rate{valid="1"}
```

```promql
openviking_feedback_negative_feedback_rate{valid="1"}
```

预期变化：

- `openviking_feedback_events_total{valid="1"}` 再增加 `1`
- `openviking_feedback_thumb_down_total{valid="1"}` 增加 `1`
- `openviking_feedback_negative_outcomes_total{valid="1"}` 通常增加 `1`
- `openviking_feedback_thumbs_down_rate{valid="1"}` 可能上升
- `openviking_feedback_negative_feedback_rate{valid="1"}` 可能上升

这一步完成后，你已经验证了：

- `/bot/v1/feedback` 能关联到历史 `response_id`
- feedback event 已被落入 session metadata
- scrape-time feedback 聚合已经反映到 `/metrics`

## 第 4 步：follow-up 提问，验证 `reasked`

这个场景用来验证“没有显式反馈，但用户很快追问，上一条回答被判定为 `reasked`”。

用户问答场景：

1. 用户第一问：

```text
请解释 OpenViking 的 metrics 是做什么的。
```

2. Vikingbot 给出回答。

3. 用户在同一个 `session_id` 里继续追问：

```text
我还是没明白，请再具体一点。
```

第一轮聊天：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "metrics-validation-reask",
    "user_id": "metrics-validation-user",
    "message": "请解释 OpenViking 的 metrics 是做什么的。"
  }'
```

第二轮 follow-up，注意要在 10 分钟内发送：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "metrics-validation-reask",
    "user_id": "metrics-validation-user",
    "message": "我还是没明白，请再具体一点。"
  }'
```

重点查询：

```promql
openviking_feedback_reasked_outcomes_total{valid="1"}
```

```promql
openviking_feedback_reask_rate{valid="1"}
```

预期变化：

- `openviking_feedback_reasked_outcomes_total{valid="1"}` 通常增加 `1`
- `openviking_feedback_reask_rate{valid="1"}` 可能上升

这里的关键点不是第二次 `/chat` 的回复内容，而是第一条 assistant response 的 outcome 已经被落成 `reasked`。

## 第 5 步：不提交显式反馈但继续追问，验证 `follow_up_without_feedback`

这个场景和上一步类似，但要验证的是另一个 outcome 维度。

用户问答场景：

1. 用户第一问：

```text
OpenViking 和普通向量数据库有什么关系？
```

2. Vikingbot 给出回答。

3. 用户没有点赞也没有点踩，而是在 10 分钟之后继续问：

```text
那你能再举一个更具体的例子吗？
```

执行方式和第 4 步类似，但这里不要在 10 分钟内追问。应使用新的 `session_id`，不调用 `/bot/v1/feedback`，并在 assistant 回复后至少等待 10 分钟，再发送下一条 follow-up。

推荐查询：

```promql
openviking_feedback_follow_up_without_feedback_outcomes_total{valid="1"}
```

预期变化：

- 该值通常增加 `1`

说明：

- 当前实现里，`reasked` 和 `follow_up_without_feedback` 都依赖后续 user turn
- 但它们的分析口径不同，前者强调“回答后 10 分钟内被追问/重问”，后者强调“用户有 follow-up、没有显式反馈，而且 follow-up 已超出 10 分钟窗口”
- 如果你的环境里历史数据很多，单次验证造成的比例变化可能不明显，所以优先看 total 指标是否跳变

## 第 6 步：`resolved` 作为补充场景验证

`resolved` 不建议作为第一优先验证项，因为它不像 thumb up / thumb down 那样“做完一个动作就容易立刻观察”。

根据当前实现规则：

- 无显式反馈
- 没有新的 user follow-up
- 该 response 被 outcome evaluator 评估为已解决

推荐验证方法：

1. 新开一个 session 发一轮简短问答
2. 不做 `/bot/v1/feedback`
3. 不继续追问
4. 等待系统完成相关持久化与后续评估
5. 再看：

```promql
openviking_feedback_resolved_outcomes_total{valid="1"}
```

预期：

- 该值有机会增加 `1`

如果这一步没有明显变化，不应优先判定系统有问题，建议先以第 2、3、4 步的结果作为主验收依据。

## 第 7 步：按 channel 看反馈指标

当前实现支持通过 `POST /bot/v1/chat/channel` 携带 `channel_id`，把请求路由到 `bot_api__<channel_id>` 这类 channel 维度里，因此可以验证 `openviking_feedback_channel_*`。

这一步是可选项，建议在主路径都通过后再做。

聊天请求示例：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat/channel" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "metrics-validation-channel-demo",
    "user_id": "metrics-validation-user",
    "channel_id": "demo",
    "message": "请简单介绍一下 OpenViking。"
  }'
```

注意：普通 `POST /bot/v1/chat` 不会根据 `channel_id` 自动切到 `bot_api` 路由；按 channel 验证时应使用 `POST /bot/v1/chat/channel`。

反馈请求示例：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/feedback" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "metrics-validation-channel-demo",
    "response_id": "<response_id>",
    "channel_id": "demo",
    "feedback_type": "thumb_down",
    "feedback_text": "not helpful"
  }'
```

重点查询：

```promql
openviking_feedback_channel_events_total{channel="bot_api__demo",valid="1"}
```

```promql
openviking_feedback_channel_thumbs_down_rate{channel="bot_api__demo",valid="1"}
```

```promql
openviking_feedback_channel_negative_outcomes_total{channel="bot_api__demo",valid="1"}
```

如果你想同时对比默认 channel 和 `bot_api__demo`，可以查：

```promql
openviking_feedback_channel_events_total{valid="1"}
```

预期：

- 能看到 `channel="bot_api__demo"`
- 对应数值会随该 channel 下的聊天/反馈变化

注意：如果你当前 bot 配置没有启用对应的 `bot_api` channel，`POST /bot/v1/chat/channel` 会直接返回 `404 Channel 'demo' not found`，需要先确认 bot 配置中存在相应 channel。最直接的方法是查看 bot 配置文件里已启用的 `bot_api` channel 定义。

## 推荐的 Grafana Explore 查询清单

如果你只想快速做一遍人工验收，可以按这个顺序执行。

基础链路：

```promql
openviking_service_readiness{valid="1"}
```

```promql
openviking_component_health{valid="1"}
```

```promql
openviking_queue_pending
```

反馈总览：

```promql
openviking_feedback_responses_total{valid="1"}
```

```promql
openviking_feedback_events_total{valid="1"}
```

```promql
openviking_feedback_coverage{valid="1"}
```

正向反馈：

```promql
openviking_feedback_thumb_up_total{valid="1"}
```

```promql
openviking_feedback_positive_outcomes_total{valid="1"}
```

负向反馈：

```promql
openviking_feedback_thumb_down_total{valid="1"}
```

```promql
openviking_feedback_negative_outcomes_total{valid="1"}
```

追问与解决：

```promql
openviking_feedback_reasked_outcomes_total{valid="1"}
```

```promql
openviking_feedback_follow_up_without_feedback_outcomes_total{valid="1"}
```

```promql
openviking_feedback_resolved_outcomes_total{valid="1"}
```

按 channel：

```promql
openviking_feedback_channel_events_total{valid="1"}
```

```promql
openviking_feedback_channel_thumbs_up_rate{valid="1"}
```

```promql
openviking_feedback_channel_thumbs_down_rate{valid="1"}
```

## 常见误判

### 1. 为什么我刚发完 `/chat`，某些反馈指标没有变化

因为反馈类指标主要来自：

- `metadata.feedback_events`
- `metadata.response_outcomes`

只有普通聊天、还没有显式 feedback 或 follow-up 时，不是所有反馈指标都会立刻动。

### 2. 为什么 total 变了，但 rate 看起来不明显

因为它们本质上是 snapshot gauge，不是面向 `rate()` 设计的 counter。优先看当前值、表格视图或短时间范围内的阶梯变化。

### 3. 为什么我只看到 `valid="0"`

说明 collector 本次刷新失败，当前暴露的是上一次成功快照。主验证时优先使用 `valid="1"`。

### 4. 为什么 channel 维度没有出现 `bot_api__demo`

可能原因通常有三个：

- 你调用的是 `POST /bot/v1/chat`，而不是 `POST /bot/v1/chat/channel`
- 请求里没带 `channel_id`
- bot 配置里并没有启用对应 `bot_api` channel

## 验收建议

如果你只做一次最小验收，建议以这四项作为“通过”标准：

1. `openviking_service_readiness{valid="1"}` 为 `1`
2. 一次 `thumb_up` 后，`openviking_feedback_events_total{valid="1"}` 和 `openviking_feedback_thumb_up_total{valid="1"}` 增加
3. 一次 `thumb_down` 后，`openviking_feedback_thumb_down_total{valid="1"}` 和 `openviking_feedback_negative_outcomes_total{valid="1"}` 增加
4. 一次 follow-up 后，`openviking_feedback_reasked_outcomes_total{valid="1"}` 增加

只要这四项通过，基本就能说明：

- bot 会话已持久化
- feedback/outcome 已写入 metadata
- `/metrics` 已成功聚合反馈快照
- Prometheus / Grafana 已能正确看到这批指标

## 相关文档

- [可观测性与排障](05-observability.md)
- [使用 Prometheus 和 Grafana 查看 OpenViking 指标](11-grafana-prometheus.md)
- [指标与 Metrics](../concepts/12-metrics.md)
- [Metrics API](../api/09-metrics.md)
- [Vikingbot 真实用户问答指标验证案例](../../../bot/docs/vikingbot-real-user-metrics-cases.md)
- [Vikingbot Phase 2 反馈链路验证指南](../../../bot/docs/vikingbot-phase2-feedback-validation-with-openviking-server.md)
- [Vikingbot Phase 3 outcome 验证指南](../../../bot/docs/vikingbot-phase3-outcome-validation-with-openviking-server.md)
