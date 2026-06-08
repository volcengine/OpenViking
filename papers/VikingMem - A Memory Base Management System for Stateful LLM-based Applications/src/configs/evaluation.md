# 评测配置

## LOCOMO judge/generator models
- **Value**: GPT-4o-mini 与 GPT-4.1-mini。
- **Rationale**: 在多个 judge/generator 设置下评估 LOCOMO，并观察相对排名稳定性。
- **Search range**: Not specified in paper。
- **Sensitivity**: Medium；论文指出 LLM-as-a-judge score 会受 evaluation setup 影响。
- **Source**: §5.1.2, §5.2, Table 1。

## LongMemEval judge/generator models
- **Value**: GPT-4o-mini 与 GPT-4o。
- **Rationale**: 用于 LongMemEval_s 有效性评测。
- **Search range**: Not specified in paper。
- **Sensitivity**: Medium。
- **Source**: §5.1.2, Table 1。

## Answer generation and evaluation repetitions
- **Value**: 每个 query 重复三次；报告平均值。
- **Rationale**: 缓解随机性。
- **Search range**: Not specified in paper。
- **Sensitivity**: Not specified in paper。
- **Source**: §5.1.5。

## Memory ingestion
- **Value**: 每个系统只导入一次。
- **Rationale**: Memory ingestion cost 高。
- **Search range**: Not specified in paper。
- **Sensitivity**: Not specified in paper。
- **Source**: §5.1.5。

## End-to-end time limit
- **Value**: 完整 memory extraction + answering 流程 24-hour limit。
- **Rationale**: 评估实际吞吐；timeout baselines omitted。
- **Search range**: Not specified in paper。
- **Sensitivity**: Medium for large benchmark comparability。
- **Source**: §5.1.5。

## Time weighting during benchmark experiments
- **Value**: 默认关闭。
- **Rationale**: 论文称数据集主要由 fact-based queries 构成，time weighting 收益有限。
- **Search range**: Not specified in paper。
- **Sensitivity**: Medium for temporal or recency-heavy workloads。
- **Source**: §5.1.5。

## RAG chunking baseline
- **Value**: 将同一 session 的 8 messages 组合成一个 text chunk。
- **Rationale**: 使每个 memory unit 的 token count 与其他方法管理的 granular memories 可比。
- **Search range**: Not specified in paper。
- **Sensitivity**: High；chunking strategy 会影响 RAG retrieval quality。
- **Source**: §5.1.3。

## Extraction-efficiency memory types
- **Value**: One event memory + two entity memories（user profile 与 topic-based compressed memory）。
- **Rationale**: 在多个 memory types 下测试 one-pass extraction 与 EUA。
- **Search range**: Not specified in paper。
- **Sensitivity**: Medium；成本优势会随 memory type 数变化。
- **Source**: §5.3。

## Candidate entities for EUA patch generation
- **Value**: 部署中 top-5 relevant existing entities。
- **Rationale**: 限制 prompt length，同时保留足够 entity context。
- **Search range**: Not specified in paper。
- **Sensitivity**: Medium。
- **Source**: §3.1 Faster Entity Update。

## Session accumulation threshold
- **Value**: 至少 20 messages 往往产生稳定、高质量 memories。
- **Rationale**: 平衡 short-term active context 与 persistent long-term memory extraction。
- **Search range**: Not specified in paper。
- **Sensitivity**: Medium。
- **Source**: §3.1 Memory Extract。
