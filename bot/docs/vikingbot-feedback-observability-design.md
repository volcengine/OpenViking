# Vikingbot 问答效果反馈观测方案设计

**Author:** OpenViking Team
**Status:** Revised Draft
**Date:** 2026-04-30

---

## 1. 背景

基于当前代码复核，vikingbot 现状更准确地说是“过程可观测”，而不是“结果可观测”。

当前已经存在的能力包括：

- `AgentLoop` 最终返回的 `OutboundMessage` 已携带 `time_cost`、`token_usage`、`iteration`、`tools_used_names`
- bus 已支持 `REASONING`、`TOOL_CALL`、`TOOL_RESULT`、`ITERATION`、`NO_REPLY` 等过程事件
- Langfuse 已接入 `session_id` / `user_id` 透传、LLM generation、tool span
- session 以 JSONL 持久化历史消息，assistant 消息可保存 `token_usage` 与 `tools_used`

但原方案的若干前提与当前实现已经有偏差，主要体现在：

- Phase 1 已经补齐 `response_id`，并打通到 `OutboundMessage`、session JSONL、OpenAPI 返回体与 Langfuse metadata
- Phase 2 当前工作树已经补齐显式反馈入口，OpenAPI 提供 `POST /bot/v1/feedback`
- `response_completed` 与 `feedback_submitted` 已经进入实现范围；`response_outcome_evaluated` 仍然属于 Phase 3，但当前工作树已经落地最小规则版实现
- session JSONL 目前不是面向分析的响应事实表，`time_cost`、`iteration`、`tools_used_names` 也没有以统一结构持久化到响应记录里
- OpenAPI 当前只透出 `response`、`reasoning`、`tool_call`、`tool_result`，并未把 `ITERATION` 等过程事件完整暴露出来
- Langfuse 当前写入的 generation metadata 已覆盖 `response_id`；`query_category`、`prompt_version`、`bot_version` 仍未稳定覆盖；Phase 3 outcome 当前通过 trace event 与 observation score 记录，而不是事后回写已结束 generation metadata
- 代码中还没有稳定的 `query_category`、`prompt_version`、`bot_version` 字段来源，因此原方案里大量切片分析暂时没有数据基础

### 1.1 截至 2026-04-30 的实现状态更新

为避免后续讨论继续把“设计目标”误写成“当前能力”，这里先明确当前代码状态：

1. Phase 1 已完成并已单独提交，`response_id` 已经贯通最终回答链路。
2. Phase 2 当前实现已经包含 `POST /bot/v1/feedback`、`FeedbackRequest` / `FeedbackResponse`、`OutboundEventType.FEEDBACK_SUBMITTED`。
3. 显式反馈当前采用最小化落地：反馈事件追加写入 session JSONL 的 `session.metadata["feedback_events"]`，而不是新建独立事实表。
4. `feedback_submitted` 属于 analytics-only 事件，用户侧 channel 明确忽略该事件，避免把分析事件误发到用户可见通道。
5. `response_outcome_evaluated` 仍然是 Phase 3 能力，但当前仅实现 analytics-only 的最小规则版，不应误写成完整离线评测体系。

因此，这份方案需要从“直接建设完整反馈归因体系”调整为“三步走”：

1. 先把一次回答变成可识别、可关联、可沉淀的结构化对象。
2. 再补显式反馈闭环。
3. 最后再做隐式结果判断、问题分类和版本归因。

在这个调整后的前提下，现有信息仍然足以回答“系统有没有运行”“模型和工具有没有被调用”，但还不足以回答更关键的问题：

1. 用户是否觉得这次回答有效。
2. 回答是否真正解决了用户问题。
3. 哪类问题效果差。
4. 效果差是因为模型、工具、时延，还是对话策略。
5. 改 prompt、改模型、改工具后，效果是否提升。

本方案的目标是为 vikingbot 建立一套面向“问答效果反馈”的观测体系，形成从回答生成到用户反馈、再到问题归因的完整闭环，并且保证每一阶段都建立在当前代码已具备的事实基础上。

---

## 2. 设计目标

### 2.1 目标

本方案希望建立一套可持续使用的指标体系，用于衡量 vikingbot 的问答质量和用户体验。

设计目标如下：

1. 能稳定衡量单条回答和单个会话的效果。
2. 能区分“模型回答差”“工具调用差”“执行太慢”等不同失败模式。
3. 能支持按模型、渠道、问题类型、版本进行切片分析。
4. 能与 Langfuse trace 关联，支持从坏样本回溯到具体执行链路。
5. 能渐进式落地，先最小可用，再逐步增强。

### 2.2 非目标

本方案当前不追求以下目标：

1. 不试图用单一指标替代所有人工判断。
2. 不要求第一版就接入复杂的离线评测平台。
3. 不要求所有指标都进入 Prometheus；部分更适合保存在业务事件或分析仓库中。
4. 不要求完全依赖 Langfuse 完成所有聚合分析；Langfuse 更适合作为 trace 容器和样本诊断入口。

---

## 3. 核心问题与总体思路

### 3.1 需要回答的核心问题

该体系主要服务于以下五类问题：

1. 用户觉得这次回答好不好。
2. 这次回答是否一次性解决问题。
3. 哪些问题类型和使用场景效果最差。
4. 坏结果主要集中在哪个执行环节。
5. 系统改动前后，效果是上升、持平还是回退。

### 3.2 总体思路

观测体系分为四层：

1. 用户反馈层：看用户主观评价。
2. 会话结果层：看问题是否被解决。
3. 执行质量层：看耗时、工具、LLM 调用质量。
4. 归因分析层：按模型、渠道、问题类型、版本等维度切片。

核心链路如下：

`一次回答 -> 用户反馈 -> 会话结果 -> trace 归因`

换句话说，系统不能只采“过程”，也必须采“结果”。

### 3.3 现状约束下的方案调整

为了避免方案设计继续偏离当前实现，整体落地顺序调整为以下三层依赖：

1. `response identity`：先给每次最终回答分配稳定的 `response_id`，并把它同时写入 `OutboundMessage`、session message、OpenAPI 返回体和 Langfuse metadata。
2. `response facts`：再沉淀一条结构化 `response_completed` 事件，把当前已经能拿到的字段先稳定保存下来，例如 `session_id`、`user_id`、`time_cost`、`token_usage`、`iteration_count`、`tool_count`、`tools_used_names`、`finish_reason`。
3. `feedback & outcome`：最后在 `response_id` 基础上补反馈事件与隐式结果判断，否则后续指标会缺少关联主键。

这意味着：

- 原方案中依赖 `query_category`、`prompt_version`、`bot_version` 的切片分析，需要从 MVP 下调到增强阶段
- `good_answer_rate`、`one_turn_resolution_rate`、`reask_rate` 等结果指标，不适合作为第一批必须落地的线上指标
- 第一阶段更应聚焦“把已有过程指标可靠地沉淀为响应事实”，而不是急于定义复杂的结果分数

---

## 4. 指标分层设计

## 4.1 用户反馈层

这层是问答效果评估的长期核心，但不应被当成 Phase 1 的落地前提。

### 4.1.1 显式满意度指标

建议定义以下指标：

| 指标名 | 定义 | 说明 |
| --- | --- | --- |
| `feedback_coverage` | 有反馈回答数 / 总回答数 | 衡量样本覆盖率，避免只看好评率 |
| `thumbs_up_rate` | 点赞回答数 / 有反馈回答数 | 基础正反馈指标 |
| `thumbs_down_rate` | 点踩回答数 / 有反馈回答数 | 基础负反馈指标 |
| `csat_score` | 用户评分均值 | 适用于 5 分制或 10 分制满意度 |
| `dissatisfaction_reason_distribution` | 各类差评原因占比 | 用于定位主要失败模式 |

差评原因建议最少支持以下标签：

- `irrelevant`: 答非所问
- `incorrect`: 信息错误
- `incomplete`: 不够完整
- `too_slow`: 太慢
- `tool_failed`: 工具执行失败
- `too_verbose`: 重复或啰嗦
- `not_actionable`: 无法操作
- `bad_format`: 格式不好

### 4.1.2 反馈强度指标

二元点赞不足以表达问题严重程度，因此建议增加：

| 指标名 | 定义 | 说明 |
| --- | --- | --- |
| `strong_negative_rate` | 强负反馈数 / 有反馈回答数 | 例如“错误”或“无法完成任务”类差评 |
| `recover_after_negative_rate` | 差评后被修复的比例 | 衡量 bot 的纠错与恢复能力 |

---

## 4.2 会话结果层

这层用于回答“即便用户没点反馈，这次回答到底算不算成功”。

### 4.2.1 单轮解决率

| 指标名 | 定义 | 说明 |
| --- | --- | --- |
| `one_turn_resolution_rate` | 单轮解决回答数 / 总回答数 | 用户一次提问后 bot 第一次正式回答即解决问题 |

可先使用以下代理信号：

1. 用户显式好评。
2. 回答后短时间内无追问且会话结束。
3. 用户后续切换到新话题，而不是继续纠错或重问。

### 4.2.2 重问率

| 指标名 | 定义 | 说明 |
| --- | --- | --- |
| `reask_rate` | 回答后短时间内同主题再次提问的比例 | 是最重要的隐式失败信号之一 |

重问信号可包括：

- “不是这个意思”
- “你没回答我的问题”
- “重新回答”
- “还是不对”
- 同主题关键词在短时间内重复出现

### 4.2.3 澄清和解决轮次

| 指标名 | 定义 | 说明 |
| --- | --- | --- |
| `clarification_turn_rate` | 需要多轮澄清的会话占比 | 衡量首答命中程度 |
| `avg_turns_to_resolution` | 从首次提问到解决的平均轮次 | 衡量整体问答效率 |

### 4.2.4 放弃和无回复

| 指标名 | 定义 | 说明 |
| --- | --- | --- |
| `no_reply_rate` | `NO_REPLY` 回答占比 | 衡量系统未回复情况 |
| `abandonment_after_answer_rate` | 回答后用户直接离开的比例 | 用于识别体验断点 |

---

## 4.3 执行质量层

这层用于回答“效果差的根因是什么”。

### 4.3.1 响应效率指标

| 指标名 | 定义 | 说明 |
| --- | --- | --- |
| `response_latency_ms_p50/p95/p99` | 端到端回答耗时分位数 | 核心体验指标 |
| `first_tool_latency_ms` | 首次工具调用前耗时 | 用于识别前置 LLM 慢或工具规划慢 |
| `end_to_end_time_cost` | 单条回答总耗时 | 可直接复用现有 `time_cost` |
| `iteration_count_avg` | 平均迭代次数 | 反映 agent 复杂度和稳定性 |
| `tool_count_avg` | 平均工具调用数 | 反映问题依赖工具程度 |

### 4.3.2 LLM 质量代理指标

| 指标名 | 定义 | 说明 |
| --- | --- | --- |
| `answer_length_avg` | 平均回答长度 | 用于识别过短或过长 |
| `reasoning_present_rate` | 含 reasoning 的回答占比 | 适用于支持 reasoning 的模型 |
| `tool_call_rate` | 触发工具调用的回答占比 | 看问题类型与工具依赖 |
| `multi_iteration_rate` | `iteration > 1` 的回答占比 | 迭代过多通常意味着策略不稳 |
| `max_iteration_hit_rate` | 达到最大迭代限制的比例 | 是重要失败信号 |

### 4.3.3 工具执行质量指标

| 指标名 | 定义 | 说明 |
| --- | --- | --- |
| `tool_success_rate` | 成功工具调用数 / 总工具调用数 | 总体工具稳定性 |
| `tool_error_rate_by_name` | 按工具名统计的错误率 | 识别问题工具 |
| `tool_timeout_rate_by_name` | 按工具名统计超时率 | 识别慢工具 |
| `tool_result_used_rate` | 工具结果最终促成有效回答的比例 | 衡量工具有效性 |
| `tool_waste_rate` | 工具被调用但对结果无帮助的比例 | 衡量无效执行 |

### 4.3.4 成本质量比指标

| 指标名 | 定义 | 说明 |
| --- | --- | --- |
| `tokens_per_positive_answer` | 总 token / 正反馈回答数 | 评估成本效率 |
| `latency_per_positive_answer` | 总耗时 / 正反馈回答数 | 评估体验效率 |
| `tool_calls_per_positive_answer` | 总工具数 / 正反馈回答数 | 看质量提升是否依赖复杂调用 |

---

## 4.4 归因分析层

该层不是单独的一组指标，而是要求前面所有指标都支持按关键维度切片。

建议将切片维度分成“当前阶段可稳定支持的”与“后续增强补齐的”。

当前阶段优先支持：

- `channel`
- `chat_type`
- `model`
- `provider`
- `session_type`
- `tool_used`
- `tool_name`
- `language`
- `user_segment`
- `time_bucket`

后续增强再补：

- `query_category`
- `prompt_version`
- `bot_version`

如果不支持这些维度切片，最终只能看到“整体效果一般”，但无法定位具体问题来源。这里尤其要避免把 `query_category`、`prompt_version`、`bot_version` 误写成当前已经稳定存在的字段来源。

---

## 5. 北极星指标与结果分级

## 5.1 北极星指标

考虑到当前实现尚无反馈入口、也无隐式 outcome 计算链路，北极星指标需要分阶段定义。

### Phase 1 北极星指标

如果第一阶段只能盯少量核心指标，建议优先使用以下五个：

1. `response_completed_count`
2. `response_latency_p95`
3. `tool_success_rate`
4. `max_iteration_hit_rate`
5. `no_reply_rate`

这五个指标都可以建立在当前代码已存在或只需极小补充的数据之上，能先回答“系统有没有稳定产出答案”。

### Phase 2 北极星指标

在 `response_id` 和反馈入口稳定后，再升级为以下五个：

1. `good_answer_rate`
2. `one_turn_resolution_rate`
3. `reask_rate`
4. `thumbs_down_rate`
5. `response_latency_p95`

其中 `good_answer_rate` 建议作为 Phase 2 之后的综合指标，定义如下：

```text
good_answer_rate =
(显式正反馈回答数 + 隐式成功回答数) / 总回答数
```

隐式成功回答数可先使用以下判定：

- 非 `NO_REPLY`
- 非错误结束
- 非最大迭代耗尽
- 无短时间内重问
- 无显式负反馈

## 5.2 回答结果分级

建议为每条最终回答打一个离散标签 `outcome_label`，而不是只做散乱的数值统计。

建议标签如下：

- `excellent`
- `good`
- `neutral`
- `bad`
- `failed`

建议规则：

| 标签 | 规则 |
| --- | --- |
| `excellent` | 有显式好评，且无后续重问 |
| `good` | 无显式反馈，但单轮结束，无重问 |
| `neutral` | 有继续追问，但最终解决 |
| `bad` | 有差评，或短时间内重问/纠错 |
| `failed` | 工具失败、LLM error、无回答、达到最大迭代仍未完成 |

这样所有统计都可以统一以 `outcome_label` 为基础聚合。

---

## 6. 事件模型设计

为了支撑上述指标，需要补充结构化事件。当前 vikingbot 已经有过程事件，但还缺少结果与反馈事件。

这里建议把事件模型拆成“必须先落地”和“后续增强”两层，而不是一次性并列设计。

### 6.0 当前已存在的过程事件

当前代码里已经存在但尚未沉淀为分析事实表的过程事件包括：

- `REASONING`
- `TOOL_CALL`
- `TOOL_RESULT`
- `ITERATION`
- `NO_REPLY`

这些事件更适合用于在线流式展示和单次问题排查，不适合作为最终分析主表。后续新增事件应围绕“最终回答”来建立主键和关联关系。

### 6.0.1 分阶段事件落地状态

截至当前代码状态：

1. Phase 1 已经落地 `response_completed` 相关主链路。
2. Phase 2 当前工作树已经落地 `feedback_submitted` 与 `/bot/v1/feedback`。
3. `response_outcome_evaluated` 已进入第三阶段实现，当前版本仅覆盖 session 历史加显式反馈的最小规则推导。

## 6.1 `response_completed`

该事件在最终回答产生时记录，是整套分析的主事实表。

建议字段：

| 字段名 | 说明 |
| --- | --- |
| `response_id` | 回答唯一 ID |
| `trace_id` | 对应 Langfuse trace ID，如当前阶段难以稳定获取可先留空 |
| `session_id` | 会话 ID |
| `user_id` | 用户 ID |
| `channel` | 渠道 |
| `chat_type` | 单聊/群聊等，如当前阶段无统一来源可先从 channel metadata 推断 |
| `model` | 模型名 |
| `provider` | provider 名，如当前阶段无稳定字段可由 provider 配置推导 |
| `message_id` | 原始消息 ID，如 channel 无该概念可为空 |
| `time_cost_ms` | 端到端耗时 |
| `prompt_tokens` | 输入 token |
| `completion_tokens` | 输出 token |
| `total_tokens` | 总 token |
| `iteration_count` | 迭代次数 |
| `tool_count` | 工具调用数 |
| `tools_used_names` | 工具名列表 |
| `finish_reason` | LLM 结束原因，如当前未显式透出则可先根据 provider 返回补齐 |
| `has_reasoning` | 是否有 reasoning 内容；当前阶段也可先退化为“是否产生过 reasoning 事件” |
| `response_length` | 回答长度 |
| `query_category` | 问题分类，第二阶段再补 |
| `prompt_version` | prompt 版本，第二阶段再补 |
| `bot_version` | bot 版本，第二阶段再补 |

其中当前阶段最低要求字段应收敛为：

- `response_id`
- `session_id`
- `user_id`
- `channel`
- `time_cost_ms`
- `prompt_tokens`
- `completion_tokens`
- `total_tokens`
- `iteration_count`
- `tool_count`
- `tools_used_names`
- `response_length`
- `created_at`

## 6.2 `feedback_submitted`

该事件在用户提交点赞、点踩、评分或文字反馈时记录。

该事件不应作为 MVP 前提。只有在 `response_id` 已经能从客户端拿到并可靠回传后，才值得接入。

当前实现状态补充：

1. OpenAPI 已提供 `POST /bot/v1/feedback`。
2. 反馈按 `response_id` 回查 assistant message；找不到时返回 `404 Response not found`。
3. 反馈会追加写入 session JSONL metadata 下的 `feedback_events`。
4. 反馈会发布 `feedback_submitted` analytics 事件，但不会向用户侧 channel 透出。

建议字段：

| 字段名 | 说明 |
| --- | --- |
| `response_id` | 关联的回答 ID |
| `session_id` | 会话 ID |
| `user_id` | 用户 ID |
| `feedback_type` | `thumb_up` / `thumb_down` / `rating` |
| `feedback_score` | 数值评分 |
| `feedback_reason` | 差评原因标签 |
| `feedback_text` | 用户补充说明 |
| `feedback_delay_sec` | 回答到反馈的间隔 |

## 6.3 `response_outcome_evaluated`

该事件由系统后处理产生，用于沉淀隐式结果判断。

当前实现状态补充：

1. 该事件已经以 analytics-only 方式落地，不会透传到用户可见 channel。
2. 当前在两个时机触发：显式反馈写入时，以及新一轮 user turn 到来前对上一条 assistant response 做隐式评估时。
3. 评估结果当前写入 session JSONL metadata 下的 `response_outcomes[response_id]`。
4. 当前规则版优先使用显式 `thumb_up` / `thumb_down`，否则结合 10 分钟内 follow-up、后续 user turn 数和是否缺少反馈来推导 outcome。
5. 当前实现是 Phase 3 的最小可用版本，不等同于完整离线 judge 或评审模型。

该事件建议放在第三阶段持续增强，因为它依赖：

- session 历史中能稳定关联 user / assistant 消息
- `response_id` 已经绑定到 assistant 最终回答
- 对“重问/纠错/切换话题/放弃”已有稳定规则

建议字段：

| 字段名 | 说明 |
| --- | --- |
| `response_id` | 回答 ID |
| `resolved_in_one_turn` | 是否单轮解决 |
| `reask_within_10m` | 10 分钟内是否重问 |
| `clarification_turns` | 后续澄清轮次 |
| `follow_up_without_feedback` | 是否出现 follow-up 且无显式反馈 |
| `outcome_label` | 最终结果标签 |

---

## 7. Query 分类设计

问答效果不能只看总体平均值，必须按问题类型分层。

但结合当前实现，`query_category` 不应作为 Phase 1 前提，而应放到 Phase 2 之后补齐。

第二阶段建议至少支持以下分类：

- `general_qa`
- `code_explanation`
- `bug_diagnosis`
- `file_operation`
- `shell_execution`
- `web_search`
- `workflow_task`
- `memory_or_profile`

后续可以进一步扩展成更稳定的分类：

- `factual`
- `reasoning`
- `retrieval_heavy`
- `tool_heavy`
- `multi_step`
- `social_chitchat`

分类来源可以按阶段逐步演进：

1. 先使用规则或关键字分类，并明确这是增强能力而非现状能力。
2. 再引入离线模型分类。
3. 最终沉淀为稳定的业务问题 taxonomy。

---

## 8. 与 Langfuse 的集成设计

Langfuse 适合作为 trace 容器、坏样本入口和链路诊断工具，但不建议把全部业务分析都压在 Langfuse 查询上。

结合当前实现，需要先明确“已经有的”和“还没有的”。

当前已经有：

- `trace` 装饰器会透传 `session_id`、`user_id`
- provider 会写入 LLM generation
- tool registry 会写入 tool span，并附带 `success`、`duration_ms`

当前还没有：

- 统一写入 generation / trace 的 `query_category`
- 统一写入 generation / trace 的 `prompt_version`、`bot_version`
- 统一且完善的 outcome 聚合视图；当前已落地的是 trace event `response_outcome_evaluated` 与 observation score `response_outcome_label`

## 8.1 Langfuse 中应承载的内容

建议按三类承载信息，而不是把所有字段都塞进 generation metadata：

1. trace / generation metadata：
- `response_id`
- `channel`
- `chat_type`
- `query_category`
- `session_type`
- `iteration_count`
- `tool_count`
- `tool_names`
- `prompt_version`
- `bot_version`

2. outcome event：
- `response_outcome_evaluated`

3. outcome score：
- `response_outcome_label`

对于 Phase 3 当前实现，需要明确避免一种不准确表述：不要写成“在显式 feedback 之后把 `final_outcome_label` 回写到已结束 generation metadata”。真实落地方式是把 outcome 写到原 trace 下的 event，并把离散标签写到对应 observation score。

其中建议的接入优先级是：

1. 先补 `response_id`、`iteration_count`、`tool_count`、`tool_names`
2. 再补 `query_category`
3. Phase 3 outcome 统一使用 `response_outcome_evaluated` event + `response_outcome_label` score
4. 最后再补 `prompt_version`、`bot_version`

## 8.2 Langfuse score 建议

建议将关键结果写入 Langfuse score，方便直接筛 trace：

- `response_outcome_label`
- `user_feedback_score`
- `implicit_resolution_score`
- `response_quality_score`
- `tool_execution_score`
- `latency_satisfaction_score`

其中：

- `response_outcome_label` 适合使用离散枚举值，例如 `positive_feedback`、`negative_feedback`、`reasked`、`resolved`
- `user_feedback_score` 可取 `1 / 0 / -1`
- `implicit_resolution_score` 可取 `1 / 0`
- `response_quality_score` 可为综合分

## 8.3 Langfuse 与分析仓库的关系

建议职责分工如下：

| 系统 | 职责 |
| --- | --- |
| Langfuse | trace 展示、样本回溯、执行链路诊断、坏案例筛选 |
| 业务事件仓库 | 指标聚合、趋势分析、A/B 对比、报表与告警 |

换句话说，Langfuse 用来回答“这条坏样本具体发生了什么”，而聚合分析系统用来回答“最近哪类问题整体变差了”。

---

## 9. Dashboard 与告警设计

## 9.1 建议的三个 Dashboard

Dashboard 同样需要分阶段理解：

- Phase 1 先做“运行稳定性看板”和“执行诊断看板”
- Phase 2 之后再补“业务效果看板”和“差评分析看板”

### 9.1.1 业务效果看板

该看板属于 Phase 2 及之后。

建议展示：

- `good_answer_rate`
- `one_turn_resolution_rate`
- `thumbs_down_rate`
- `reask_rate`

支持按以下维度切片：

- 时间
- 模型
- channel
- query_category

### 9.1.2 执行诊断看板

建议展示：

- `response_latency_p95`
- `tool_error_rate_by_name`
- `max_iteration_hit_rate`
- `response_completed_count`

支持按以下维度切片：

- provider
- tool_name

`prompt_version` 相关切片应放到后续增强阶段，前提是代码中已经有稳定字段来源。

### 9.1.3 差评分析看板

该看板同样属于 Phase 2 及之后。

建议展示：

- 差评原因分布
- 差评样本 Top N
- 差评 trace 中常见工具链
- 差评 query_category 排名

## 9.2 告警建议

建议优先配置以下五类告警：

1. `response_latency_p95` 超阈值
2. `tool_success_rate` 突降
3. `max_iteration_hit_rate` 突增
4. `no_reply_rate` 突增
5. `response_completed_count` 异常下降

在 Phase 2 接入反馈后，再补：

1. `thumbs_down_rate` 突增
2. `good_answer_rate` 突降

这些告警比单纯盯错误日志更接近真实用户体验变化。

---

## 10. 分阶段落地计划

## 10.1 Phase 1: MVP

第一阶段先做最小可用版本，目标是快速建立结构化响应事实闭环。

建议优先落地：

1. `response_id` 机制
2. `response_completed` 事件
3. 在 `OutboundMessage`、session message、OpenAPI 响应中透出 `response_id`
4. Langfuse trace / generation metadata 关联 `response_id`
5. 最小指标集

MVP 指标集建议为：

1. `response_completed_count`
2. `response_latency_p95`
3. `tool_success_rate`
4. `tool_error_rate_by_name`
5. `max_iteration_hit_rate`
6. `no_reply_rate`
7. `avg_iteration_count`
8. `avg_tool_count`

说明：第一阶段不再把 `feedback_coverage`、`thumbs_up_rate`、`thumbs_down_rate`、`good_answer_rate`、`one_turn_resolution_rate`、`reask_rate` 作为必须指标，因为它们当前没有数据入口或没有稳定计算依据。

## 10.2 Phase 2: 增强归因能力

第二阶段重点提升分析和归因能力。

截至当前工作树，以下第 1、2 项已经进入实现状态，并且已经通过 `openviking-server --with-bot` 完成真实代理路径验证；后续重点应放在补充更系统的验证沉淀与指标消费，而不是把尚未完成的 Phase 3 能力提前写成已实现。

建议增加：

1. 点赞/点踩反馈入口与 `feedback_submitted` 事件
2. OpenAPI 反馈接口或统一 feedback webhook
3. query 分类
4. `feedback_coverage`
5. `thumbs_up_rate`
6. `thumbs_down_rate`
7. `negative_rate_by_query_category`
8. `model_comparison_by_query_category`

## 10.3 Phase 3: 离线评测与评审模型

第三阶段再考虑引入隐式 outcome 判断与离线质量评审能力。

截至当前工作树，`response_outcome_evaluated` 的最小规则版已经落地；后续重点从“是否实现”转为“规则是否足够稳健、指标如何消费、是否接入 judge”。

建议先补：

1. 增强 `response_outcome_evaluated` 后处理
2. `one_turn_resolution_rate`
3. `reask_rate`
4. `good_answer_rate`
5. `outcome_label`
6. `recover_after_negative_rate`
7. `tool_helpfulness_rate_by_name`
8. `tokens_per_positive_answer`
9. `latency_vs_feedback_correlation`

建议引入 LLM-as-a-Judge，为每条回答提供辅助分数：

- `relevance_score`
- `correctness_score`
- `completeness_score`
- `actionability_score`
- `tone_score`

这一层只能作为辅助，不应替代真实用户反馈。

---

## 11. 与当前 vikingbot 架构的对应关系

结合当前代码结构，建议的最小落点如下：

1. 在 `vikingbot.bus.events.OutboundMessage` 增加 `response_id` 字段。
2. 在 `AgentLoop._process_message` 生成最终 `OutboundMessage` 前创建 `response_id`。
3. 在 `session.add_message("assistant", ...)` 时把 `response_id` 一并写入 JSONL 消息。
4. 在 OpenAPI `ChatResponse` 与流式最终事件中返回 `response_id`，让客户端具备回传反馈的主键。
5. 新增 `response_completed` 事件写入点，优先在 agent loop 最终回答落盘后触发。
6. 在 Langfuse generation / tool span metadata 中写入 `response_id`、`iteration_count`、`tool_count`、`tool_names`。

第二阶段再补：

1. OpenAPI 反馈接口。
2. channel 侧反馈事件接入。
3. `feedback_submitted` 事件。
4. `query_category`、`prompt_version`、`bot_version` 的字段来源。

第三阶段再补：

1. 基于 session 历史的 `response_outcome_evaluated` 后处理。
2. 更稳健的 `outcome_label` 规则。
3. 单轮解决、重问、放弃等隐式结果指标消费。

因此，第一阶段无需大改 agent 主循环，但也不只是把“最终回答”和“后续反馈”关联起来，而是要先把“最终回答本身”沉淀成结构化、可关联、可复盘的响应事实。

---

## 12. 风险与注意事项

### 12.1 反馈覆盖率不足

如果用户反馈入口不明显，最终会导致显式反馈覆盖率过低。因此不能只依赖点赞/点踩，必须同时建设隐式成功指标。

### 12.2 不能让高基数字段污染通用指标系统

像 `session_id`、`user_id`、完整错误文本、完整问题文本，不适合直接进入高频指标标签。它们更适合存到事件系统或 trace metadata。

### 12.3 LLM Judge 不能替代真实用户

离线模型评分可以帮助排序和筛样本，但不能当成用户体验的真实代表。

### 12.4 反馈体系要支持版本对比

若没有 `prompt_version`、`bot_version`、`model` 等字段，后续几乎无法评估优化是否有效。

### 12.5 不要把“设想中的字段”误当成“现有能力”

本次复核发现，设计文档最容易偏离现状的地方，不在于指标定义本身，而在于把“应该有的字段”写成了“已经稳定存在的字段”。

后续继续推进时需要遵守两个原则：

1. 文档中的“当前已有能力”必须只写代码里已经稳定产出的字段和事件。
2. 文档中的“建议字段”必须明确区分为 MVP 必需、第二阶段补齐、第三阶段增强，避免把实现顺序倒置。

---

## 13. 结论

vikingbot 的问答效果反馈观测，不能只停留在 token、trace 和工具调用层面，必须建立从“执行过程”到“最终结果”的完整链路。

本方案建议的主线是：

1. 以 `response_completed` 为核心事实事件。
2. 先建立响应事实，再叠加显式反馈和隐式结果。
3. Phase 1 先用 `response_completed_count`、`response_latency_p95`、`tool_success_rate`、`max_iteration_hit_rate`、`no_reply_rate` 保障系统稳定性；Phase 2 之后再升级为效果类北极星指标组合。
4. 通过 Langfuse trace + 业务事件聚合实现从趋势发现到坏样本回溯的闭环。

最终目标不是“记录更多日志”，而是让团队能够明确回答：

- 哪些回答真的好。
- 哪些回答正在变差。
- 为什么变差。
- 改完以后是否真的变好。
