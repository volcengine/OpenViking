# 概念

## Memory Base
- **Notation**: —
- **Definition**: 面向长期 LLM 交互持久状态的数据管理范式，核心特征是选择性抽取高价值记忆、记忆状态持续演化，以及可跨应用域复用的通用抽象。
- **Boundary conditions**: 适用于长期、有状态、原始交互流超出有效上下文或需要生命周期管理的 LLM 应用；论文未说明其是否适用于纯静态知识库检索。
- **Related concepts**: Event, Entity, Memory Base Management System, Event-Entity Paradigm

## Memory Base Management System (MBMS)
- **Notation**: MBMS
- **Definition**: 实现 Memory Base 的系统层；本文将 VikingMem 定义为构建在 VikingDB 上的端到端 MBMS。
- **Boundary conditions**: 论文描述的是基于 VikingDB 的云原生实现；OpenViking 是其开源能力子集，PDF 中未给出非 VikingDB 部署细节。
- **Related concepts**: VikingMem, VikingDB, OpenViking, Memory Base

## Event
- **Notation**: —
- **Definition**: 从原始交互流中选择性抽取的离散、带时间戳、episodic、schema 约束的记忆记录；它捕获单个显著信息点，是摄取单位。
- **Boundary conditions**: 完整 event instance 包含 timestamp 等元数据，但 Figure 1 为清晰起见省略部分不可变元数据。Event 不是原始的 recency-based context window。
- **Related concepts**: Event Schema, Entity, Event-Centric Partitioning

## Event Schema
- **Notation**: EventType, Description, Properties
- **Definition**: 可定制模板，指定事件类型、描述和属性列表。每个属性包含 PropertyName、PropertyType 与 Description，用于约束抽取。
- **Boundary conditions**: schema 由用户/应用方定义，并编译进抽取 prompt；schema 质量被隐含假设会影响输出质量。
- **Related concepts**: Event, One-pass Memory Extraction, Memory Schema

## Entity
- **Notation**: —
- **Definition**: 持久、持续演化的状态表示，例如用户画像或 agent 工具使用 profile；它随时间整合与合并事件信息，形成连贯长期记忆。
- **Boundary conditions**: Entity 不是普通压缩笔记，而是通过显式聚合表达式和算子从事件中物化出的状态。
- **Related concepts**: Entity Schema, AggregateExpression, Operator

## Entity Schema
- **Notation**: EntityType, Description, Properties, AggregateExpression
- **Definition**: 定义实体类型和属性的 schema；每个属性可包含 AggregateExpression，指定触发该属性更新的 event type/property、更新算子以及是否主键。
- **Boundary conditions**: 论文给出 JSON-like schema 概念，但没有给出完整形式语法。
- **Related concepts**: Entity, Operator, Event Schema

## Operator
- **Notation**: SUM, MAX, AVG, COUNT, LLM_MERGE, TIME_COMPRESS
- **Definition**: 控制实体状态如何响应事件变化的应用定义函数。
- **Boundary conditions**: 统计算子避免 LLM 调用并处理数值聚合；LLM-based 算子处理复杂合成与压缩。论文未给出每个算子的完整实现语义。
- **Related concepts**: AggregateExpression, Entity Memory Update, TIME_COMPRESS

## One-pass Memory Extraction
- **Notation**: —
- **Definition**: schema 驱动的抽取范式：把多个 event/entity memory type 编译为单个 prompt，使 LLM 只处理一次输入流就抽取所有定义的记忆输出。
- **Boundary conditions**: 依赖 LLM in-context learning 与 schema prompt 编译；§5.3 在 LOCOMO 上用 one event memory + two entity memories 评估。
- **Related concepts**: Event Schema, Entity Schema, Prefix Cache, EUA

## Entity Update Algorithm (EUA)
- **Notation**: EUA
- **Definition**: 补丁式实体更新算法：对旧实体字段应用 field-wise SEARCH/REPLACE patch，并用 approximate span matching 找到最佳替换位置，从而避免字符串实体更新时额外调用 LLM。
- **Boundary conditions**: 论文称部署中只检索 top-5 相关既有实体用于 patch 生成；具体 edit-distance 实现细节未说明。
- **Related concepts**: Faster Entity Update, Patch, BestApproxSpan

## Intelligent Memory Segmentation Method (IMSM)
- **Notation**: IMSM
- **Definition**: 面向 event-intertwined sessions 的两阶段分段策略：semantic saliency filtering 剪除低价值片段；event-centric partitioning 确定 coherent topic 的起止位置，并可合并非连续片段。
- **Boundary conditions**: 论文用 prose 和 Figure 4 解释该策略；除 ≥20 messages batching 观察外，完整 prompt 与阈值未给出。
- **Related concepts**: Semantic Saliency Filtering, Event-Centric Partitioning, Selective Extraction

## TIME_COMPRESS
- **Notation**: TIME_COMPRESS
- **Definition**: 长期记忆生命周期算子：把相关事件按 topic-centric timeline 分组，保留近期高保真事件，对不活跃的较旧 timeline 懒合并为高层摘要，给底层事件设置 TTL，并在摘要保留显著信息后剪除过期低层事件。
- **Boundary conditions**: weekly/monthly summary 只是示例；论文未给出精确压缩调度与 TTL 默认值。
- **Related concepts**: Temporal Compression, Timeline, TTL, LLM_MERGE

## Multi-path Recall
- **Notation**: dense + sparse hybrid retrieval, keyword graph path
- **Definition**: 检索机制：主路径使用 dense/sparse hybrid retrieval，并叠加 time-decay 与 business-importance 分数；辅助路径使用 keyword graph 召回补充候选。
- **Boundary conditions**: 论文评测中由于数据集多为事实类问题，默认关闭 time weighting；生产配置依应用而定。
- **Related concepts**: Keyword Graph, Time-Decay Score, Business Score, Multi-vector Rerank

## Multi-vector Rerank
- **Notation**: ColBERT-style late interaction
- **Definition**: 受 ColBERT 启发的重排策略：在抽取阶段预计算 memory vectors，并用 quantization、token-merge 等压缩技术实现高效 late-interaction reranking。
- **Boundary conditions**: 论文未提供 quantization/token merge 的完整参数；Table 1 和 Table 4 报告了延迟与消融效果。
- **Related concepts**: Rerank, Retrieval Latency, ColBERT
