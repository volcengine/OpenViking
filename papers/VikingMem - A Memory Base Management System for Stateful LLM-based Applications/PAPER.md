---
title: "VikingMem: A Memory Base Management System for Stateful LLM-based Applications"
authors:
  - Jiajie Fu
  - Junwen Chen
  - Mengzhao Wang
  - Aoxiang He
  - Maojia Sheng
  - Xiangyu Ke
  - Yifan Zhu
  - Yunjun Gao
year: 2026
venue: "arXiv preprint"
doi: "arXiv:2605.29640"
ara_version: "1.0"
domain: "LLM 长期记忆系统；数据库系统；检索增强生成"
keywords:
  - Memory Base
  - VikingMem
  - 有状态 LLM 应用
  - Event-Entity 模型
  - 记忆抽取
  - 实体演化
  - 时间压缩
  - 混合检索
  - 多向量重排
claims_summary:
  - "Memory Base 应通过选择性抽取、内生状态演化和可泛化抽象来支撑长期有状态 LLM 应用。"
  - "VikingMem 用 Event/Entity 抽象、schema 驱动的一次性抽取、算子式实体更新、时间压缩、混合召回、关键词图召回和多向量重排落地该范式。"
  - "在 LOCOMO 与 LongMemEval 的报告结果中，VikingMem 在所有给定模型/基准设置的总体 LLM-as-a-judge 分数上均优于所列基线，同时保持亚秒级检索延迟。"
  - "一次性抽取与 EUA 相比多 prompt 或无 EUA 的变体降低了抽取成本/时间，同时保持相近质量。"
  - "选择性保留在 LongMemEval 上把存储降到原始 token 基线的 16.82%，同时提高报告的 LLM-judge 分数。"
  - "消融实验显示 IMSM、多向量重排、实体记忆和关键词图均对端到端性能有贡献。"
abstract: "大型语言模型推动了交互式应用，但有限上下文窗口给长期、有状态交互带来关键数据管理挑战。现有记忆方法常依赖简单抽取而产生不完整记忆，或使用针对单一场景（如聊天机器人）的刚性一次性记忆抽取 prompt，因而泛化性不足并在多样下游任务上表现不佳。论文提出 Memory Base，并给出基于 VikingDB 的端到端 Memory Base Management System：VikingMem。系统用事件/实体抽象、事件中心抽取、状态化实体演化、时间压缩、时间加权召回和多向量重排来管理长期交互状态。"
---

# VikingMem：面向有状态 LLM 应用的 Memory Base 管理系统

## 概览

本文把长期 LLM 交互中的“记忆”定义为一个数据管理问题，而不仅是提示词工程问题。作者提出 **Memory Base**：一种面向持久状态的记忆基座，核心原则是从低密度原始流中选择性抽取高价值记忆、让记忆内容持续演化并具备生命周期管理能力，以及通过可配置抽象跨场景复用。

**VikingMem** 是该范式在 VikingDB 上的系统化实现。它把原始会话转换为 schema 约束的 **Event**，再通过算子把事件持续物化为 **Entity** 状态；同时提供一次性抽取、EUA（无需额外 LLM 调用的补丁式实体更新）、TIME_COMPRESS、关键词图辅助召回、带时间/业务权重的混合检索，以及 ColBERT 风格的多向量重排。

## Layer Index

### Cognitive Layer (`/logic`)
| 文件 | 说明 |
|------|------|
| [problem.md](logic/problem.md) | Memory Base 与 VikingMem 的问题、观察、缺口、关键洞察和假设。 |
| [claims.md](logic/claims.md) | 6 个可证伪主张（C01-C06）及实验绑定。 |
| [concepts.md](logic/concepts.md) | Memory Base、Event、Entity、算子、EUA、IMSM、时间压缩、召回/重排等核心概念。 |
| [experiments.md](logic/experiments.md) | 6 个声明式实验/分析（E01-E06），精确数值放入 evidence。 |
| [related_work.md](logic/related_work.md) | 相关工作的类型化依赖图与完整引用足迹摘要。 |
| [solution/architecture.md](logic/solution/architecture.md) | VikingMem 抽取、管理、检索模块的组件图。 |
| [solution/method.md](logic/solution/method.md) | schema、一次性抽取、分段、算子、压缩、召回和重排方法。 |
| [solution/algorithm.md](logic/solution/algorithm.md) | 论文中明确给出的公式/伪代码：实体代数、EUA、召回打分和 F1。 |
| [solution/constraints.md](logic/solution/constraints.md) | 边界条件、假设、局限和未说明项。 |

### Physical Layer (`/src` 与 `/data`)
| 文件 | 说明 | 关联主张 |
|------|------|----------|
| [src/environment.md](src/environment.md) | 论文给出的运行时、硬件、数据集、基线、协议和复现信息。 | C02-C06 |
| [src/artifacts.md](src/artifacts.md) | 论文点名的真实制品：VikingMem 服务、OpenViking 子集、评测代码、用例与用户指南。 | C02-C06 |
| [src/configs/evaluation.md](src/configs/evaluation.md) | §5.1 的评测设置与实现细节。 | C03-C06 |
| [data/dataset.md](data/dataset.md) | LOCOMO 与 LongMemEval_s 数据集说明。 | C03, C05, C06 |

### Exploration Graph (`/trace`)
| 文件 | 说明 |
|------|------|
| [exploration_tree.yaml](trace/exploration_tree.yaml) | 12 节点、受来源约束的研究 DAG，重构问题、设计决策、实验和被揭示的失败路径。 |

### Evidence (`/evidence`)
| 文件 | 说明 |
|------|------|
| [README.md](evidence/README.md) | 6 个编号表格与 5 个编号图的索引；每个对象都有 markdown 转写与 PNG 截图。 |
| [tables/table1.md](evidence/tables/table1.md) | LLM-as-a-judge 与检索延迟基准结果。 |
| [tables/table2.md](evidence/tables/table2.md) | 一次性抽取和 EUA 的效率结果。 |
| [tables/table3.md](evidence/tables/table3.md) | LongMemEval 存储效率。 |
| [tables/table4.md](evidence/tables/table4.md) | 系统组件消融。 |
| [tables/table5.md](evidence/tables/table5.md) | 真实场景中的算子使用频率。 |
| [tables/table6.md](evidence/tables/table6.md) | LOCOMO F1-score 评测。 |
| [figures/figure1.md](evidence/figures/figure1.md) | Event/Entity schema 与内置算子。 |
| [figures/figure2.md](evidence/figures/figure2.md) | VikingMem 系统流水线。 |
| [figures/figure3.md](evidence/figures/figure3.md) | 传统多 prompt 抽取与 VikingMem 抽取范式对比。 |
| [figures/figure4.md](evidence/figures/figure4.md) | 无需 LLM 的快速实体更新。 |
| [figures/figure5.md](evidence/figures/figure5.md) | Agent Memory 事件/实体示例。 |
| [proofs/equations.md](evidence/proofs/equations.md) | 论文公式和伪代码：实体代数、EUA、召回打分、F1。 |
