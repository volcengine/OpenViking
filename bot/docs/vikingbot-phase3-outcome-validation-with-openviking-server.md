# Vikingbot Phase 3 outcome 验证指南

**Author:** OpenViking Team
**Status:** Draft
**Date:** 2026-04-30

---

## 1. 目的

本文用于验证 Vikingbot feedback observability Phase 3 当前已落地的 response outcome 链路，重点覆盖：

1. 历史 assistant response 能基于 session 上下文被评估为 `response_outcome_evaluated`
2. 显式反馈能够驱动 outcome 落为 `positive_feedback` 或 `negative_feedback`
3. 后续 user turn 能驱动上一条 assistant response 被隐式评估为 `reasked`
4. session JSONL metadata 中会写入 `response_outcomes`
5. Langfuse 中可看到与 outcome 对应的 trace event 和 observation score
6. `response_outcome_evaluated` 作为 analytics-only 事件，不会泄漏到 OpenAPI 对外响应或用户可见 channel

本阶段仍然是最小规则版 outcome evaluator，不是完整的离线 judge 或模型评审链路。

---

## 2. 适用范围

本文适用于以下启动方式：

```bash
OPENVIKING_CLI_CONFIG_FILE=ov_conf/ovcli.conf openviking-server --with-bot --config ov_conf/ov.conf
```

在这种模式下：

- OpenViking Server 对外监听业务端口
- 内部自动启动 vikingbot gateway
- Bot API 通过 OpenViking Server 代理暴露在 `/bot/v1`

因此本文默认验证地址形如：

- `http://127.0.0.1:30300/bot/v1`

本文优先验证真实代理路径，而不是直接访问 `http://127.0.0.1:18790`。

在执行本文前，建议先完成 Phase 1 与 Phase 2 的最小验证，至少确认以下前置条件已经成立：

1. `/bot/v1/chat` 返回体中稳定包含 `response_id`
2. session JSONL metadata 首行中已存在按 `response_id` 组织的 `response_facts`
3. 显式 feedback 已能写入 `metadata.feedback_events`

Phase 3 的 `response_outcomes` 评估与回写，默认建立在这几条 response identity / response facts / feedback persistence 基础链路已经打通之上。

---

## 3. 当前已验证结论

截至 2026-04-30，本轮已完成的验证分为三类。

### 3.1 已通过的代码级验证

已通过如下定向测试：

```bash
./.venv/bin/python -m pytest -o addopts='' bot/tests/test_outcome_evaluator.py
./.venv/bin/python -m pytest -o addopts='' bot/tests/test_agent_loop_outcome.py
./.venv/bin/python -m pytest -o addopts='' bot/tests/test_langfuse_outcome_metadata.py
./.venv/bin/python -m pytest -o addopts='' bot/tests/test_openapi_auth.py -k feedback
```

对应验证点：

1. outcome evaluator 能产出 `resolved`
2. 显式 `thumb_up` 能产出 `positive_feedback`
3. 显式 `thumb_down` 能产出 `negative_feedback`
4. 新 user turn 到来时，上一条 assistant response 能被评估为 `reasked`
5. outcome 会写入 `session.metadata["response_outcomes"]`
6. bus 会发布 `OutboundEventType.RESPONSE_OUTCOME_EVALUATED`
7. Langfuse 能记录 `response_outcome_evaluated` event 与 `response_outcome_label` score

### 3.2 已验证的对外契约约束

当前实现已明确保证：

1. `response_outcome_evaluated` 是 analytics-only 事件
2. chat / email / mochat 等用户可见 channel 明确忽略该事件
3. OpenAPI `/chat` 与 `/feedback` 对外返回中不会新增暴露 `response_outcome_evaluated` 事件流

### 3.3 当前建议完成的真实链路闭环

本阶段建议至少完成以下两个真实代理路径闭环：

1. `/chat -> /feedback`，验证 outcome 为 `positive_feedback` 或 `negative_feedback`
2. `/chat -> follow-up user turn`，验证上一条 assistant response outcome 为 `reasked`

这两个闭环合起来，足以验证当前 Phase 3 最核心的“显式 + 隐式” outcome 评估链路。

---

## 4. 当前规则说明

当前 Phase 3 使用最小规则版 evaluator，规则来源为：

1. session message 历史
2. `session.metadata["feedback_events"]`

当前规则为：

1. `thumb_down` -> `negative_feedback`
2. `thumb_up` -> `positive_feedback`
3. 无显式反馈，但 10 分钟内出现新的 user turn -> `reasked`
4. 无后续 user turn -> `resolved`
5. 有 follow-up 且无反馈 -> `follow_up_without_feedback`
6. 其他情况 -> `follow_up`

当前写入的 outcome payload 至少包含：

- `response_id`
- `resolved_in_one_turn`
- `reask_within_10m`
- `clarification_turns`
- `follow_up_without_feedback`
- `outcome_label`
- `evaluated_at`
- `evidence`

---

## 5. 推荐验证步骤

### 5.1 启动服务

```bash
OPENVIKING_CLI_CONFIG_FILE=ov_conf/ovcli.conf openviking-server --with-bot --config ov_conf/ov.conf
```

预期日志至少包含类似内容：

```text
Bot API proxy enabled, forwarding to http://127.0.0.1:18790
Starting vikingbot gateway...
Vikingbot gateway started (PID: ...)
OpenViking HTTP Server is running on 127.0.0.1:30300
```

### 5.2 健康检查

```bash
curl -sS http://127.0.0.1:30300/bot/v1/health
```

预期返回 HTTP 200。

### 5.3 场景 A：显式反馈驱动 `positive_feedback`

先发送一条真实聊天请求：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "phase3-outcome-feedback-session",
    "user_id": "phase3-outcome-user",
    "message": "请简单回复一句：用于验证 phase3 positive outcome"
  }'
```

预期返回至少包含：

```json
{
  "session_id": "phase3-outcome-feedback-session",
  "response_id": "<response_id>",
  "message": "..."
}
```

拿到 `response_id` 后，提交显式反馈：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/feedback" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "phase3-outcome-feedback-session",
    "response_id": "<response_id>",
    "feedback_type": "thumb_up",
    "feedback_text": "helpful"
  }'
```

预期 `/feedback` 返回类似：

```json
{
  "accepted": true,
  "response_id": "<response_id>",
  "session_id": "phase3-outcome-feedback-session",
  "feedback_type": "thumb_up",
  "feedback_delay_sec": 1.234,
  "timestamp": "..."
}
```

然后检查对应 session JSONL 首行 metadata，预期至少包含：

```json
{
  "metadata": {
    "feedback_events": [
      {
        "response_id": "<response_id>",
        "feedback_type": "thumb_up",
        "feedback_text": "helpful"
      }
    ],
    "response_outcomes": {
      "<response_id>": {
        "response_id": "<response_id>",
        "outcome_label": "positive_feedback",
        "resolved_in_one_turn": true,
        "reask_within_10m": false
      }
    }
  }
}
```

如需验证负向路径，可对另一条新 response 提交：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/feedback" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "phase3-outcome-feedback-session-neg",
    "response_id": "<another_response_id>",
    "feedback_type": "thumb_down",
    "feedback_text": "not helpful"
  }'
```

预期 `response_outcomes["<another_response_id>"].outcome_label == "negative_feedback"`。

### 5.4 场景 B：后续 user turn 驱动 `reasked`

先创建一条新的对话响应：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "phase3-outcome-reask-session",
    "user_id": "phase3-outcome-user",
    "message": "请简单回复一句：用于验证 phase3 reasked"
  }'
```

记录返回中的 `response_id=<response_id_1>`。

在 10 分钟内，对同一个 `session_id` 再发送一条 follow-up user message：

```bash
curl -sS -X POST "http://127.0.0.1:30300/bot/v1/chat" \
  -H "Content-Type: application/json" \
  -d '{
    "session_id": "phase3-outcome-reask-session",
    "user_id": "phase3-outcome-user",
    "message": "我还是没明白，请再解释一下"
  }'
```

当前实现会在处理这条新 user turn 前，先评估上一条 assistant response 的 outcome。

随后检查 session JSONL 首行 metadata，预期至少包含：

```json
{
  "metadata": {
    "response_outcomes": {
      "<response_id_1>": {
        "response_id": "<response_id_1>",
        "outcome_label": "reasked",
        "reask_within_10m": true,
        "resolved_in_one_turn": false,
        "clarification_turns": 1,
        "follow_up_without_feedback": false
      }
    }
  }
}
```

这里的关键点不是第二次 `/chat` 的返回内容，而是上一条 response 的 outcome 是否已被持久化为 `reasked`。

### 5.5 检查 Langfuse trace event 与 observation score

这一步要求已启用 Langfuse。

完成 5.3 或 5.4 后，在 Langfuse UI 中找到对应 trace / generation，重点检查：

1. trace 下新增一个 event：`response_outcome_evaluated`
2. 对应 generation / observation 下新增一个 score：`response_outcome_label`

原因说明：

1. Phase 3 outcome 往往发生在 `/chat` 请求结束之后
2. Langfuse v3 中 generation 一旦结束，不适合再事后修改其 metadata
3. 因此当前实现采用“event + score”记录 outcome，而不是回写已结束 generation 的 metadata

预期至少包含：

- trace event 名称：`response_outcome_evaluated`
- score 名称：`response_outcome_label`
- score 值：如 `positive_feedback`、`negative_feedback`、`reasked`

对于显式正向反馈路径，典型 event 预期类似：

```json
{
  "name": "response_outcome_evaluated",
  "metadata": {
    "response_id": "<response_id>",
    "outcome_label": "positive_feedback",
    "response_outcome_evaluated": {
      "response_id": "<response_id>",
      "outcome_label": "positive_feedback"
    }
  }
}
```

对应 score 预期类似：

```json
{
  "name": "response_outcome_label",
  "value": "positive_feedback",
  "data_type": "CATEGORICAL"
}
```

对于 follow-up 路径，典型 event 预期类似：

```json
{
  "name": "response_outcome_evaluated",
  "metadata": {
    "response_id": "<response_id_1>",
    "outcome_label": "reasked",
    "response_outcome_evaluated": {
      "response_id": "<response_id_1>",
      "outcome_label": "reasked",
      "reask_within_10m": true
    }
  }
}
```

对应 score 预期类似：

```json
{
  "name": "response_outcome_label",
  "value": "reasked",
  "data_type": "CATEGORICAL"
}
```

### 5.6 验证 analytics-only 事件未对外泄漏

当前实现要求：

1. `/bot/v1/chat` 正常返回用户可见回复
2. `/bot/v1/feedback` 只返回 feedback ack
3. `response_outcome_evaluated` 不应出现在 OpenAPI 对外响应体中
4. chat / email / mochat 等用户可见 channel 不应向终端用户输出该 analytics 事件

因此应确认：

1. `/chat` 响应中没有新增 `response_outcome_evaluated` 事件对象
2. `/feedback` 响应中只有 `accepted`、`response_id`、`session_id`、`feedback_type`、`feedback_delay_sec`、`timestamp` 等反馈确认字段
3. 终端用户只能看到正常 assistant reply，而不会看到内部 outcome JSON

---

## 6. 通过标准

如果以下条件全部满足，可以认为当前 Phase 3 最小实现验证通过：

1. `/bot/v1/chat` 仍能稳定返回 `response_id`
2. 对某条 response 提交 `thumb_up` 后，session JSONL `metadata.response_outcomes[response_id].outcome_label == "positive_feedback"`
3. 对某条 response 提交 `thumb_down` 后，session JSONL `metadata.response_outcomes[response_id].outcome_label == "negative_feedback"`
4. 在同一 session 中追加 follow-up user turn 后，上一条 response 的 outcome 被写为 `reasked`
5. Langfuse trace 中可看到 `response_outcome_evaluated` event，且 observation 上可看到 `response_outcome_label` score
6. OpenAPI 对外响应和用户可见 channel 中没有泄漏 `response_outcome_evaluated`

---

## 7. 排查建议

### 7.1 `/bot/v1/health` 不通

优先检查：

1. 是否确实使用了 `openviking-server --with-bot`
2. 服务端口是否与 `ov_conf/ov.conf` 中配置一致
3. `18790` 是否被旧的 vikingbot 进程占用

### 7.2 `/feedback` 成功，但 session 里没有 `response_outcomes`

优先检查：

1. 当前 `response_id` 是否确实来自同一 `session_id`
2. session 首行 metadata 中是否已经写入 `feedback_events`
3. 当前运行代码是否已包含 Phase 3 outcome evaluator 接入

### 7.3 follow-up 后没有看到 `reasked`

优先检查：

1. follow-up 是否发送到了同一个 `session_id`
2. 第一条 assistant message 是否已持久化且带有 `response_id`
3. follow-up 与上一条 assistant response 的间隔是否明显超过 10 分钟
4. 该 response 是否已经先被显式 `thumb_up` 或 `thumb_down` 覆盖为更强信号

### 7.4 Langfuse 中看不到 outcome 记录

优先检查：

1. 是否已安装并启用 `bot-langfuse`
2. `ov_conf/ov.conf` 中 Langfuse 配置是否生效
3. 原始 generation metadata 中是否已有 `response_id`
4. 是否在 trace 视图中查看了 event，而不是只盯着 generation metadata
5. 是否在 observation / generation 详情中查看了 score
6. outcome 是否已经成功写入 session JSONL 的 `metadata.response_outcomes`

---

## 8. 结论口径

截至当前，Phase 3 应使用如下口径描述：

1. `response_outcome_evaluated` 的最小规则版实现已落地
2. outcome 可由显式 feedback 或后续 user turn 隐式推导
3. 评估结果会写入 session JSONL 的 `metadata.response_outcomes`
4. Langfuse 当前通过 trace event 和 observation score 记录 outcome，而不是事后回写已结束 generation 的 metadata
5. 该事件目前仍是 analytics-only，不属于对外用户契约
6. 当前实现可支撑后续更复杂的 judge / 打分链路，但当前版本尚不是完整评审系统
