# Table 4: Ablation study on VikingMem components

**Source**: Table 4, §5.5
**Caption**: "Ablation study on the contribution of each system component to end-to-end performance and its associated impact on p95 search latency, evaluated on the LOCOMO dataset using GPT-4o-mini. 'IMSM' stands for 'intelligent memory segmentation method for event-intertwined sessions'."
**Screenshot**: table4.png
**Extraction type**: raw_table

| Removed Component | LLM Judge Score | Search Latency |
|---|---:|---:|
| / | 88.83 | - |
| Multi-Vector Rerank | 85.19 | +6.8ms |
| Entity Memory | 86.93 | ≈0 |
| IMSM | 83.51 | ≈0 |
| Keyword Graph | 86.92 | +25.8ms |

## 中文说明
该表支撑 C06：所有移除变体的分数低于 full system；移除 IMSM 后分数最低（83.51），对应最大质量下降。
