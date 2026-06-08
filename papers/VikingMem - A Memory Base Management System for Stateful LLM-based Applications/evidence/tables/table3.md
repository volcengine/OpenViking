# Table 3: Storage Efficiency on LongMemEval

**Source**: Table 3, §5.4
**Caption**: "Storage Efficiency on LongMemEval"
**Screenshot**: table3.png
**Extraction type**: raw_table

| Method | Storage (Tokens) | Score |
|---|---:|---:|
| Naive RAG [16] | 100% (Baseline) | 63.81 |
| VikingMem | 16.82% (83.18% ↓) | 75.80 |

## 中文说明
该表支撑 C05：VikingMem 在 LongMemEval 上只保留 16.82% token（相对 100% baseline 下降 83.18%），同时 Score 为 75.80，高于 Naive RAG 的 63.81。
