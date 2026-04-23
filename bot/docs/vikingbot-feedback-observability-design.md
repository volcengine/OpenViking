# Vikingbot 问答效果反馈观测方案设计

**Author:** OpenViking Team
**Status:** Draft
**Date:** 2026-04-21

---

## 1. 背景

当前 vikingbot 已经具备基础运行观测能力：

- 会话维度：`session_id`、`user_id`
- 响应维度：`time_cost`、`token_usage`、`iteration`、`tools_used_names`
- 过程维度：`REASONING`、`TOOL_CALL`、`TOOL_RESULT`、`ITERATION`
- Langfuse 维度：conversation trace、LLM generation、tool span
- 持久化维度：session JSONL 历史消息

这些信息足以回答“系统有没有运行”“模型和工具有没有被调用”，但还不足以回答更关键的问题：

1. 用户是否觉得这次回答有效。
2. 回答是否真正解决了用户问题。
3. 哪类问题效果差。
4. 效果差是因为模型、工具、时延，还是对话策略。
5. 改 prompt、改模型、改工具后，效果是否提升。

本方案的目标是为 vikingbot 建立一套面向“问答效果反馈”的观测体系，形成从回答生成到用户反馈、再到问题归因的完整闭环。

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

---

## 4. 指标分层设计

## 4.1 用户反馈层

这层是问答效果评估的核心，优先级最高。

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

建议至少支持以下切片维度：

- `channel`
- `chat_type`
- `model`
- `provider`
- `session_type`
- `tool_used`
- `tool_name`
- `language`
- `query_category`
- `user_segment`
- `time_bucket`
- `prompt_version`
- `bot_version`

如果不支持这些维度切片，最终只能看到“整体效果一般”，但无法定位具体问题来源。

---

## 5. 北极星指标与结果分级

## 5.1 北极星指标

如果第一阶段只能盯少量核心指标，建议优先使用以下五个：

1. `good_answer_rate`
2. `one_turn_resolution_rate`
3. `reask_rate`
4. `thumbs_down_rate`
5. `response_latency_p95`

其中 `good_answer_rate` 建议作为综合指标，定义如下：

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

建议新增三类核心事件。

## 6.1 `response_completed`

该事件在最终回答产生时记录，是整套分析的主事实表。

建议字段：

| 字段名 | 说明 |
| --- | --- |
| `response_id` | 回答唯一 ID |
| `trace_id` | 对应 Langfuse trace ID |
| `session_id` | 会话 ID |
| `user_id` | 用户 ID |
| `channel` | 渠道 |
| `chat_type` | 单聊/群聊等 |
| `model` | 模型名 |
| `provider` | provider 名 |
| `message_id` | 原始消息 ID |
| `time_cost_ms` | 端到端耗时 |
| `prompt_tokens` | 输入 token |
| `completion_tokens` | 输出 token |
| `total_tokens` | 总 token |
| `iteration_count` | 迭代次数 |
| `tool_count` | 工具调用数 |
| `tools_used_names` | 工具名列表 |
| `finish_reason` | LLM 结束原因 |
| `has_reasoning` | 是否有 reasoning 内容 |
| `response_length` | 回答长度 |
| `query_category` | 问题分类 |
| `prompt_version` | prompt 版本 |
| `bot_version` | bot 版本 |

## 6.2 `feedback_submitted`

该事件在用户提交点赞、点踩、评分或文字反馈时记录。

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

建议字段：

| 字段名 | 说明 |
| --- | --- |
| `response_id` | 回答 ID |
| `resolved_in_one_turn` | 是否单轮解决 |
| `reask_within_10m` | 10 分钟内是否重问 |
| `clarification_turns` | 后续澄清轮次 |
| `abandoned` | 是否被放弃 |
| `outcome_label` | 最终结果标签 |

---

## 7. Query 分类设计

问答效果不能只看总体平均值，必须按问题类型分层。

第一阶段建议至少支持以下分类：

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

1. 先使用规则或关键字分类。
2. 再引入离线模型分类。
3. 最终沉淀为稳定的业务问题 taxonomy。

---

## 8. 与 Langfuse 的集成设计

Langfuse 适合作为 trace 容器、坏样本入口和链路诊断工具，但不建议把全部业务分析都压在 Langfuse 查询上。

## 8.1 Langfuse 中应承载的内容

建议在 trace 或 generation metadata 中补充以下字段：

- `response_id`
- `channel`
- `chat_type`
- `query_category`
- `session_type`
- `iteration_count`
- `tool_count`
- `tool_names`
- `final_outcome_label`
- `prompt_version`
- `bot_version`

## 8.2 Langfuse score 建议

建议将关键结果写入 Langfuse score，方便直接筛 trace：

- `user_feedback_score`
- `implicit_resolution_score`
- `response_quality_score`
- `tool_execution_score`
- `latency_satisfaction_score`

其中：

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

### 9.1.1 业务效果看板

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
- `llm_error_rate`

支持按以下维度切片：

- provider
- tool_name
- prompt_version

### 9.1.3 差评分析看板

建议展示：

- 差评原因分布
- 差评样本 Top N
- 差评 trace 中常见工具链
- 差评 query_category 排名

## 9.2 告警建议

建议优先配置以下五类告警：

1. `thumbs_down_rate` 突增
2. `good_answer_rate` 突降
3. `response_latency_p95` 超阈值
4. `tool_success_rate` 突降
5. `max_iteration_hit_rate` 突增

这些告警比单纯盯错误日志更接近真实用户体验变化。

---

## 10. 分阶段落地计划

## 10.1 Phase 1: MVP

第一阶段先做最小可用版本，目标是快速建立结果反馈闭环。

建议优先落地：

1. `response_id` 机制
2. `response_completed` 事件
3. 点赞/点踩反馈入口与 `feedback_submitted` 事件
4. 最小指标集
5. Langfuse trace 关联 `response_id`

MVP 指标集建议为：

1. `feedback_coverage`
2. `thumbs_up_rate`
3. `thumbs_down_rate`
4. `good_answer_rate`
5. `one_turn_resolution_rate`
6. `reask_rate`
7. `response_latency_p95`
8. `tool_success_rate`
9. `max_iteration_hit_rate`
10. `negative_rate_by_query_category`

## 10.2 Phase 2: 增强归因能力

第二阶段重点提升分析和归因能力。

建议增加：

1. `response_outcome_evaluated` 后处理
2. query 分类
3. `outcome_label`
4. `recover_after_negative_rate`
5. `tool_helpfulness_rate_by_name`
6. `tokens_per_positive_answer`
7. `latency_vs_feedback_correlation`
8. `model_comparison_by_query_category`

## 10.3 Phase 3: 离线评测与评审模型

第三阶段再考虑引入离线质量评审能力。

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

1. 在最终 `OutboundMessage` 生成处补 `response_id`。
2. 在 session message 中保存 bot 回答与 `response_id` 的关联。
3. 在 OpenAPI 和各 channel 增加反馈接口或反馈事件。
4. 在 Langfuse trace / generation metadata 中写入 `response_id`、`query_category`、`outcome_label`。
5. 在会话后处理流程中计算隐式成功信号，如重问、单轮解决和放弃。

第一阶段无需大改 agent 主循环，只需要把“最终回答”和“后续反馈”关联起来。

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

---

## 13. 结论

vikingbot 的问答效果反馈观测，不能只停留在 token、trace 和工具调用层面，必须建立从“执行过程”到“最终结果”的完整链路。

本方案建议的主线是：

1. 以 `response_completed` 为核心事实事件。
2. 用显式反馈和隐式结果共同定义回答质量。
3. 通过 `good_answer_rate`、`one_turn_resolution_rate`、`reask_rate`、`thumbs_down_rate` 和 `response_latency_p95` 形成北极星指标组合。
4. 通过 Langfuse trace + 业务事件聚合实现从趋势发现到坏样本回溯的闭环。

最终目标不是“记录更多日志”，而是让团队能够明确回答：

- 哪些回答真的好。
- 哪些回答正在变差。
- 为什么变差。
- 改完以后是否真的变好。
