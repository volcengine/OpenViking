# 证据索引

## Tables
| File | Source | Claims | Description |
|------|--------|--------|-------------|
| [tables/table1.md](tables/table1.md) | Table 1, §5.2 | C03 | LOCOMO 与 LongMemEval 的 LLM Judge Score 和 Search Latency。 |
| [tables/table2.md](tables/table2.md) | Table 2, §5.3 | C04 | Multiple Prompts、One-pass w/ EUA、One-pass w/o EUA 的 Cost/Time/Score 对比。 |
| [tables/table3.md](tables/table3.md) | Table 3, §5.4 | C05 | LongMemEval 上 Naive RAG 与 VikingMem 的 storage tokens 与 score。 |
| [tables/table4.md](tables/table4.md) | Table 4, §5.5 | C06 | 移除各核心组件后的 LLM Judge Score 与 latency impact。 |
| [tables/table5.md](tables/table5.md) | Table 5, §5.6 | C01 | Education、Agent Memory、Social Companionship 场景中的算子使用频率。 |
| [tables/table6.md](tables/table6.md) | Table 6, §5.7 | C03 | LOCOMO 上多方法 token-level F1-score。 |

## Figures
| File | Source | Claims | Description |
|------|--------|--------|-------------|
| [figures/figure1.md](figures/figure1.md) | Figure 1, §2.2.1 | C01, C02 | Event/Entity 定义 schema 与 built-in operators。 |
| [figures/figure2.md](figures/figure2.md) | Figure 2, §3 | C02 | VikingMem 从数据流到抽取、存储管理、检索重排和回复的 pipeline。 |
| [figures/figure3.md](figures/figure3.md) | Figure 3, §3.1 | C04 | 传统多 prompt 抽取与 VikingMem schema-driven 抽取范式对比。 |
| [figures/figure4.md](figures/figure4.md) | Figure 4, §3.1 | C04 | 无需额外 LLM 的 patch-based entity update。 |
| [figures/figure5.md](figures/figure5.md) | Figure 5, §4.2 | C01, C02 | Agent Memory 中 tool event 演化为 tool entity 的实例。 |

## Proofs / Equations
| File | Source | Claims | Description |
|------|--------|--------|-------------|
| [proofs/equations.md](proofs/equations.md) | §2.2.2, Algorithm 1, §3.3, Eq. (1) | C02, C04 | 论文明确给出的实体代数、EUA 伪代码、召回打分公式与 F1。 |

## 完整性说明
- 本 ARA 对 PDF 中所有编号对象进行了完整 sweep：6 个 Table 与 5 个 Figure 均已归档。
- 每个编号 table/figure 均包含一个 markdown 转写/描述文件和同名 PNG 截图。
- 未发现 appendix；参考文献页没有额外编号图表。
