# 实验

## E01: 设计原则与数据模型分析
- **Verifies**: C01, C02
- **Setup**:
  - Model: 不适用；这是概念/系统设计分析。
  - Hardware: 不适用。
  - Dataset: 论文中的生产经验观察和五类应用场景。
  - System: VikingMem Memory Base 设计，包括 Event/Entity schema 与算子库。
- **Procedure**:
  1. 识别噪声流、动态状态和碎片化架构三类行业观察。
  2. 将每类观察映射到设计原则。
  3. 定义 Event、Entity 与 Operator 作为可复用原语。
  4. 说明这些原语如何支撑多个下游场景。
- **Metrics**: 需求覆盖度；场景覆盖度；算子使用多样性。
- **Expected outcome**:
  - Event-Entity 模型应能表达多样记忆领域，同时保持抽取、状态演化和检索逻辑可复用。
- **Baselines**: 既有记忆处理方法、raw RAG、prompt-specific systems、vertical systems。
- **Dependencies**: none

## E02: 架构/模块实现检查
- **Verifies**: C02
- **Setup**:
  - Model: §3 与 §5 描述的 LLM-based extraction 和 answer generation。
  - Hardware: src/environment.md 中的生产/评测部署。
  - Dataset: 通用系统工作流；不依赖单一 benchmark。
  - System: VikingMem 的 extract、storage/management、keyword graph、retrieval、rerank 模块。
- **Procedure**:
  1. 将 session messages 和可选 user profile 送入 extraction module。
  2. 用 schema-compiled prompt 产生 event/entity 输出。
  3. 在 VikingDB-backed stores 中存储和更新 event/entity。
  4. 通过 hybrid vector search 和 keyword graph path 检索 long-term memory。
  5. 应用 multi-vector rerank，并把 short-term 与 long-term memory 结合生成回复。
- **Metrics**: 模块存在性与交互关系；端到端记忆生命周期覆盖度。
- **Expected outcome**:
  - 架构应展示从 raw session 到 reply-with-memory 的完整生命周期。
- **Baselines**: 无架构 raw prompt injection；单独 vector-store retrieval。
- **Dependencies**: E01

## E03: 端到端 benchmark 评测
- **Verifies**: C03
- **Setup**:
  - Model: LOCOMO 使用 GPT-4o-mini 与 GPT-4.1-mini；LongMemEval 使用 GPT-4o-mini 与 GPT-4o。
  - Hardware: 向量数据库服务使用 CPU 节点；embedding service 使用一张 NVIDIA A30 GPU 和 CPU 资源。
  - Dataset: LOCOMO 与 LongMemEval_s。
  - System: VikingDB-backed production VikingMem。
- **Procedure**:
  1. 每个 memory system 只导入一次 benchmark 数据。
  2. 每个 query 按各方法检索/构造 memory。
  3. 在相同 prompt setup 下生成答案并用 LLM-as-a-judge 评估。
  4. 多次重复答案生成和评估并取平均。
  5. 测量检索系统 p50/p95 search latency。
- **Metrics**: 分类与总体 LLM Judge Score；p50/p95 search latency。
- **Expected outcome**:
  - VikingMem 应获得高于所列基线的总体 LLM-judge 分数，同时保持低延迟。
- **Baselines**: Mem0、Mem0-graph、Zep、RAG、Full-Context、Claude Native Memory、OpenClaw、Mirix（按适用情况）。
- **Dependencies**: E02

## E04: One-pass extraction 与 EUA 效率实验
- **Verifies**: C04
- **Setup**:
  - Model: §5.3 的 LLM extraction setup。
  - Hardware: 同评测环境（抽取硬件未单独说明）。
  - Dataset: LOCOMO。
  - System: VikingMem 配置为 one event memory + two entity memories。
- **Procedure**:
  1. 配置包含多个 memory type 的 schema。
  2. 运行传统 Multiple Prompts 基线：每个 memory type 单独 LLM 调用。
  3. 运行 One-pass (w/o EUA)。
  4. 运行 One-pass (w/ EUA)。
  5. 对比 monetary extraction cost、wall-clock time 与 LLM Judge Score。
- **Metrics**: Extraction cost、extraction time、LLM Judge Score。
- **Expected outcome**:
  - One-pass 应相比 Multiple Prompts 降低成本；EUA 应相比无 EUA one-pass 降低时间与成本，并保持相近质量。
- **Baselines**: Multiple Prompts；One-pass (w/o EUA)。
- **Dependencies**: E02

## E05: 存储效率与保留分析
- **Verifies**: C05
- **Setup**:
  - Model: LongMemEval 存储分析使用的 GPT-4o 评测设置。
  - Hardware: 同 VikingMem/VikingDB 评测环境。
  - Dataset: LongMemEval_s。
  - System: VikingMem selective event/entity retention。
- **Procedure**:
  1. 用 Naive RAG raw-token retention 持久化 memory state。
  2. 用 VikingMem extracted events 与 entity snapshots 持久化 memory state。
  3. 测量相对 raw-token baseline 的 stored token count。
  4. 用 LLM-judge score 测量 retrieval accuracy。
- **Metrics**: Storage token percentage；LLM Judge Score。
- **Expected outcome**:
  - VikingMem 应比 Naive RAG 保留更少 token，同时保持或提高检索准确性。
- **Baselines**: Naive RAG。
- **Dependencies**: E03

## E06: 组件消融与 F1 鲁棒性评测
- **Verifies**: C06, C03
- **Setup**:
  - Model: LOCOMO 上 GPT-4o-mini；F1 的 answer generation/evaluation 也使用 gpt-4o-mini。
  - Hardware: 同评测环境。
  - Dataset: LOCOMO。
  - System: Full VikingMem 以及分别移除 multi-vector rerank、entity memory、IMSM、keyword graph 的变体。
- **Procedure**:
  1. 评测 full VikingMem。
  2. 每次移除一个目标组件。
  3. 重新运行 LOCOMO 评测并测量 LLM-judge score 与 p95 latency impact。
  4. 对多个方法独立计算相对 ground-truth answer 的 token-level F1。
- **Metrics**: LLM Judge Score；p95 search-latency delta；token-level F1。
- **Expected outcome**:
  - 移除每个组件都应降低质量；F1 应支持同样的有效性结论。
- **Baselines**: Full VikingMem；各组件移除变体；F1 对比中的 Mem0、Mem0-graph、Zep、Full-Context、Claude、OpenClaw。
- **Dependencies**: E03
