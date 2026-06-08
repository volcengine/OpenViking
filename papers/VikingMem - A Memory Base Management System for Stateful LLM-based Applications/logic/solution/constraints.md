# 约束、假设与局限

## Boundary Conditions

- VikingMem 面向长期、有状态 LLM 应用，而不是纯静态文档 QA。
- 系统假设目标领域可以定义有效 memory schemas。
- 论文 benchmark 结果仅限 LOCOMO 与 LongMemEval_s，以及给定 LLM 和基线实现。
- 由于评测数据多为事实类问题，time weighting 在实验中默认关闭，因此 benchmark 证据未充分检验 time-decay 收益。
- 论文报告的是 VikingDB-backed production implementation；OpenViking 被描述为开源核心能力子集。

## Assumptions

- LLM extraction 能较可靠地遵循 event/entity schemas。
- 高价值记忆可表示为 event records 与 materialized entity state。
- Approximate patch matching 足以处理 SEARCH/REPLACE 中的小型 LLM 字符串误差。
- Hybrid dense/sparse retrieval + keyword graph + rerank 足以覆盖评测中的 memory queries。
- LLM-as-a-judge 与 token-level F1 可作为长期记忆 QA 的有效性指标。

## Known Limitations Stated or Implied by the Paper

- PDF 未完整给出所有应用场景的 prompt templates 与精确 schema examples。
- Segmentation prompts、prefix-cache 配置、quantization/token-merge 参数、time-decay curve 参数和 TTL schedules 的精确实现细节未说明。
- 论文没有形式化证明 Event-Entity 抽象覆盖所有有状态 LLM 应用。
- 由于 ingestion cost 高，评测中每个系统只导入一次数据；这可能无法衡量重复摄取方差。
- 24-hour limit 下 timeout 的基线存在缺失结果，限制了部分单元格的直接比较。
- 论文承认 LLM-as-a-judge score 会受 judge model 和 evaluation prompt 影响。
- Storage efficiency 以 token percentage 报告，但未完全展开存储核算流程。
- 一些生产部署与商业可用性主张在 PDF 中描述，但未在 PDF 内独立审计。

## Not Specified in Paper

- Random seeds。
- 精确 Python/package versions。
- 完整 extraction prompts。
- 生产 VikingMem 内部源码路径。
- Time-decay weights、business weights、freshness window、TTL、quantization、token-merge 的精确默认值。
- 除动机描述外的完整 privacy、audit、access-control 机制。
