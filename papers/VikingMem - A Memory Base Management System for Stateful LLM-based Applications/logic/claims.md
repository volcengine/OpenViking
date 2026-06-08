# 主张

## C01: Memory Base 原则刻画了持久 LLM 状态的关键需求
- **Statement**: 生产级 LLM 记忆基座应同时具备选择性抽取、内生状态/演化能力和可泛化设计范式。
- **Status**: supported
- **Falsification criteria**: 如果多样化有状态 LLM 应用能在生产规模下仅靠 raw-context prompting 或静态 insert-and-retrieve 存储满足需求，而无需选择性抽取、状态演化或 schema 泛化，则该主张被削弱。
- **Proof**: [E01, E05]
- **Evidence basis**: §2.1 将三条行业观察映射为三条设计原则；Figure 1 定义可配置 Event/Entity schema 与内置算子；Table 5 显示三个真实场景的算子组合不同。
- **Interpretation**: 该主张偏概念和设计论证；证据表明论文提出的需求与部署多样性，而非形式化证明其他方法不可能成立。
- **Dependencies**: none
- **Tags**: memory-base, design-principles, generalization

## C02: VikingMem 将 Memory Base 实现为 event/entity MBMS
- **Statement**: VikingMem 通过 Event/Entity 抽象、schema 驱动抽取、event/entity 存储、算子式管理和检索/重排模块落地 Memory Base。
- **Status**: supported
- **Falsification criteria**: 如果系统描述缺失 event、entity、抽取、管理或检索模块，或实体并非通过算子与事件关联，则该主张为假。
- **Proof**: [E01, E02]
- **Evidence basis**: Figure 1 展示 Event Schema、Entity Schema 和 operators；Figure 2 展示抽取、存储/管理、关键词图、混合召回、重排和带记忆回复流程；§2.2 与 §3 解释组件。
- **Interpretation**: 论文提供系统架构与生产系统描述；PDF 内没有给出可逐行验证的生产源码。
- **Dependencies**: C01
- **Tags**: architecture, event-entity, MBMS

## C03: VikingMem 在报告的 LLM-judge 评测中总体分最高
- **Statement**: 在 Table 1 的 LOCOMO 与 LongMemEval LLM-as-a-judge 评测中，VikingMem 在每个报告的模型/基准设置下总体分均高于所列基线。
- **Status**: supported
- **Falsification criteria**: 如果 Table 1 中任一同设置基线的 Overall 分数高于 VikingMem，则该主张为假。
- **Proof**: [E03]
- **Evidence basis**: Table 1 报告 VikingMem 在 LOCOMO（GPT-4o-mini、GPT-4.1-mini）和 LongMemEval（GPT-4o-mini、GPT-4o）的 Overall 分均高于所列替代方法。
- **Interpretation**: 该结论仅限论文评测协议、数据子集和 judge 模型；不能直接泛化到所有长期记忆任务。
- **Dependencies**: C02
- **Tags**: effectiveness, llm-judge, LOCOMO, LongMemEval

## C04: 一次性抽取与 EUA 提升抽取效率且保持相近质量
- **Statement**: 在论文的 LOCOMO 抽取效率实验中，schema 驱动 one-pass extraction 相比 Multiple Prompts 降低成本；加入 EUA 又相比无 EUA one-pass 降低时间和成本，同时 LLM-judge 分数相近。
- **Status**: supported
- **Falsification criteria**: 如果 Table 2 显示 one-pass 变体成本/时间不低于对应基线，或质量显著崩塌，则该主张为假。
- **Proof**: [E04]
- **Evidence basis**: Table 2 报告 Multiple Prompts、One-pass (w/ EUA)、One-pass (w/o EUA) 的 Cost、Time 和 Score；§5.3 将其解释为成本/时间下降且质量相近。
- **Interpretation**: “相近质量”基于 Table 2 中较小的分数差；结论受限于 LOCOMO 上 one event memory + two entity memories 设置。
- **Dependencies**: C02
- **Tags**: one-pass-extraction, EUA, efficiency

## C05: 选择性保留在降低存储的同时保持/提升检索准确性
- **Statement**: 在 LongMemEval 上，VikingMem 相比 Naive RAG 使用显著更少 token 存储，同时报告更高 LLM-judge 分数。
- **Status**: supported
- **Falsification criteria**: 如果 VikingMem 存储占比与 Naive RAG 接近或更高，或压缩导致分数显著低于基线，则该主张为假。
- **Proof**: [E05]
- **Evidence basis**: Table 3 报告 Naive RAG 存储 100%、Score 63.81；VikingMem 存储 16.82%（83.18% ↓）、Score 75.80。
- **Interpretation**: 结果支持 LongMemEval 上的选择性抽取；论文未详述完整存储核算流程。
- **Dependencies**: C01, C02
- **Tags**: storage-efficiency, selective-retention, LongMemEval

## C06: VikingMem 核心组件均贡献端到端性能
- **Statement**: 消融实验显示移除 multi-vector rerank、entity memory、IMSM 或 keyword graph 都会降低分数，其中移除 IMSM 的质量下降最大。
- **Status**: supported
- **Falsification criteria**: 如果移除这些组件不降低 LLM-judge 分数，或 IMSM 不是 Table 4 中最大质量贡献项，则该主张为假。
- **Proof**: [E06]
- **Evidence basis**: Table 4 报告 full system 与各移除组件变体的分数；§5.5 指出 IMSM 带来最严重下降。
- **Interpretation**: 消融基于 LOCOMO + GPT-4o-mini；其他数据集或部署中组件重要性可能变化。
- **Dependencies**: C02
- **Tags**: ablation, IMSM, rerank, entity-memory, keyword-graph
