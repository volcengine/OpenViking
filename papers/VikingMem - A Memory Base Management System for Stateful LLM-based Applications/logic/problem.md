# 问题规格

## Observations

### O1: 扩展上下文不能替代持久状态管理
- **Statement**: 论文指出，即使上下文窗口持续扩展（例如脚注提到 Gemini 上下文长度达 2 million tokens），上下文仍然是有限、昂贵、延迟敏感且瞬时的资源。
- **Evidence**: §1；脚注 1；引用 [47]、[50]。
- **Implication**: 长期 LLM 应用需要持久状态基座，而不是简单把更多历史塞进 prompt。

### O2: 生产记忆流低密度且主题交错
- **Statement**: 会议转写、调试日志等生产流中有价值信息稀疏且主题交错；盲目截断或总结会导致 context pollution 或 over-squashing。
- **Evidence**: §2.1 Observation 1；Figure 3；Figure 4。
- **Implication**: 原始 chunk、整段 session 存储或消息级存储都可能带来噪声、碎片化或遗漏。

### O3: 真实世界状态持续变化
- **Statement**: 用户、学习者、工具和 agent 工作流都会随时间变化；静态 insert-and-retrieve 向量库只能累积历史，不能更新底层状态。
- **Evidence**: §2.1 Observation 2；Figure 1；Figure 5。
- **Implication**: 记忆系统需要显式生命周期：更新、合并、纠错、加权和遗忘。

### O4: 不同下游场景需要不同记忆结构
- **Statement**: 论文对比了陪伴式用户偏好、agent SOP、教育学习轨迹、协作待办、搜索/推荐画像等差异化需求。
- **Evidence**: §2.1 Observation 3；§4.1；Table 5。
- **Implication**: 单场景 prompt 或垂直系统难以跨应用迁移。

### O5: 评测工作负载长且多样
- **Statement**: LOCOMO 包含 10 段长期对话且每段平均 1000+ messages；LongMemEval_s 包含 500 段长对话且平均约 115,000 tokens；论文称 LongMemEval_s token 长度是 LOCOMO 的 346×。
- **Evidence**: §5.1.1；data/dataset.md。
- **Implication**: 记忆系统必须同时优化有效性、延迟与存储效率。

### O6: 生产规模超出普通 prompt 工程假设
- **Statement**: 论文脚注称单个生产租户每天可产生超过 1 billion tokens 的记忆数据。
- **Evidence**: §1 脚注 2；§1 效率讨论。
- **Implication**: 多轮抽取和原始日志保留在经济与运维上不可持续。

## Gaps

### G1: 现有方法要么抽取不足，要么过度存储
- **Statement**: 现有记忆系统常用简单抽取导致记忆不完整，或存粗粒度/原始 chunk 导致检索上下文噪声高。
- **Caused by**: O1, O2, O5。
- **Existing attempts**: Naive RAG chunking、Full-Context、记忆抽取 prompt、图记忆与模块化记忆系统。
- **Why they fail**: 它们没有同时解决信号选择、非连续语义片段合并和生命周期化状态管理。

### G2: 状态演化不是一等公民
- **Statement**: 静态向量检索管线存储 episode，却缺少显式的持久实体演化机制。
- **Caused by**: O3。
- **Existing attempts**: insert-and-retrieve 向量库、prompt 驱动摘要、图记忆。
- **Why they fail**: 它们主要累积旧事实，而不是通过明确聚合/更新规则物化新状态。

### G3: 面向场景的 prompt 工程不可泛化
- **Statement**: 窄域系统和硬编码 prompt 不能为不同记忆结构提供稳定、可复用接口。
- **Caused by**: O4。
- **Existing attempts**: 聊天画像记忆、按 memory type 分 prompt 的抽取、任务专用 summarizer。
- **Why they fail**: 每个新场景都需要重新 prompt engineering，难以共享能力。

### G4: 多轮抽取对生产工作负载成本过高
- **Statement**: 每种记忆类型单独调用 LLM 会重复处理同一原始输入，成本随记忆类型数增长。
- **Caused by**: O2, O6。
- **Existing attempts**: §3.1 与 Table 2 的 Multiple Prompts 基线代表传统多 prompt 范式。
- **Why they fail**: token 消耗重复；Table 2 显示其成本高于 one-pass 变体。

### G5: 高精度检索可能不满足交互延迟
- **Statement**: 一些强基线存在多秒级 p50/p95 延迟；论文还指出 cross-encoder 重排在大候选集上 p99 可达秒级。
- **Caused by**: O1, O5。
- **Existing attempts**: 模块化记忆系统、cross-encoder reranker。
- **Why they fail**: Table 1 报告多个基线高 p95；§3.3 说明 cross-encoder 不适合实时应用。

## Key Insight

- **Insight**: 将长期 LLM 状态视为数据库式 **Memory Base**：schema 约束的事件日志 + 由可复用算子更新的实体物化视图 + 带权多路径检索。
- **Derived from**: O1-O6。
- **Enables**: 高价值事件选择性摄取、状态化实体演化、时间压缩/遗忘、跨域 schema 配置、一次性抽取、确定性补丁更新和高效检索/重排。

## Assumptions

- A1: 应用开发者能为目标场景定义有用的 event/entity schema。
- A2: LLM 能较可靠地遵循 schema 约束抽取事件、实体相关更新和 patch。
- A3: ANN/向量检索能为 patch 生成和记忆召回提供相关候选。
- A4: LOCOMO/LongMemEval 上的 LLM-as-a-judge 与 token-level F1 能作为长期记忆有效性的代理指标。
- A5: 时间衰减和业务权重可由应用方配置；论文未给出完整生产默认值。
