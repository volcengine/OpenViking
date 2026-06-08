# 方法

## Memory Base：Event Log + Entity Materialized Views
论文的核心方法是把记忆视为 event/entity 上的可复用代数，而不是一组场景专用 summary prompt。Event store 是 schema 约束的 episodic records 通用日志；Entity store 是 event log 上的一组持久 materialized views。

论文给出关系表达式：

```text
entity := SELECT OP(event.content) FROM Events
WHERE filters(event) GROUP BY keys(event).
```

其中 `keys` 定义分组方式，`filters` 约束可参与聚合的事件，`OP` 来自固定可复用算子库。

## Event 与 Entity Schema
Event 通过 `EventType`、`Description`、`Properties` 配置。Entity 通过 `EntityType`、`Description`、`Properties` 和每个属性的 `AggregateExpression` 配置。AggregateExpression 指明哪个 event type/property 驱动实体更新，以及使用哪个 operator。

## One-pass Memory Extraction
传统 multi-prompt 系统对每个 memory type 重复处理同一 raw input。VikingMem 将所有 event/entity schemas 编译到一个 prompt，使 LLM 在一次输入处理里抽取所有定义的 memory types。论文还指出 fixed prefix 包含 system instruction 与 memory schema，可通过 prefix-cache 复用。

## Entity Update Algorithm (EUA)
对于字符串实体字段，常规方式可能需要额外 LLM 调用来合成新实体。EUA 改为让 extractor 输出 field-wise SEARCH/REPLACE patches。算法解析 patch，在旧实体字段中通过 edit-distance-based approximate search 找到最佳 span，并用 replacement text 替换。论文称部署中仅检索 top-5 相关既有实体用于 patch 生成，以约束 prompt 长度。

## Intelligent Memory Segmentation Method
该方法处理低信息密度、主题交错的 sessions。

1. **Semantic saliency filtering**：隔离有意义片段，剪除 greetings 等 filler。
2. **Event-centric partitioning**：确定每个 coherent topic 的精确 start/end positions，输出 tuples，并可合并语义相关但非连续的 dialogue segments。

目标是在排除无关 topic 噪声的同时保留完整 topic memory。

## Memory Management Operators
VikingMem 包含统计类和 LLM-based 算子。

- `SUM`, `COUNT`, `AVG`, `MAX`：数值/统计聚合，避免 LLM 调用与算术错误。
- `LLM_MERGE`：增量文本合并，用于去重、冲突处理、合成新旧信息。
- `TIME_COMPRESS`：生命周期算子。它把相关事件组织成 topic-centric timelines，近期事件保持高保真，较旧且不活跃的 timeline 被懒合成为 higher-level summary；底层 events 被赋 TTL，并在摘要保留显著信息后剪除。

## Keyword Graph
默认 hybrid retrieval 可能漏掉“Do you remember my nickname?” 这类与目标记忆语义相似度低的 query。VikingMem 构建 keyword graph：keyword embedding 由包含该词的 memory segments embeddings 平均得到，keywords 连接到关联 memories。

## Multi-path Recall with Time and Business Weights
主路径使用 dense/sparse hybrid retrieval。最终分数是 normalized original retrieval score、temporal score 与 business score 的加权组合。Temporal score 在 configurable freshness window 内为满分，之后按 fast-then-slow exponential curve 衰减。Business score 可来自 type-level 或 instance-level 权重。辅助 keyword graph path 提供补充候选。论文报告：相比简单合并，先独立排序各路径、分配不同 quota 再合并效果更好。

## Multi-vector Rerank
为了满足交互延迟，VikingMem 避免较慢的 cross-encoder reranking，而采用 ColBERT-style late interaction。它在抽取阶段预计算并存储 memory token vectors，并使用 quantization、token merge 等压缩技术，使存储开销接近 dense vectors。

## 应用场景
论文点名五类部署场景：

1. Social & Companionship。
2. Search & Recommendation。
3. Efficiency & Collaboration。
4. Education。
5. Agent Memory。

Figure 5 给出 Agent Memory 示例：tool invocation events 演化为持久 tool profile，其中包含 tool_call_times、success_rate、avg_token_usage、avg_time_cost、suitable_for、failure_cases 与 suggestions。
