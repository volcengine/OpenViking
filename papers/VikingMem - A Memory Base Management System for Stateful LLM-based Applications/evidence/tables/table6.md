# Table 6: F1-score on LOCOMO

**Source**: Table 6, §5.7
**Caption**: "F1-score (%) on LOCOMO. SH: Single-Hop, MH: Multi-Hop, OD: Open-Domain, Temp: Temporal."
**Screenshot**: table6.png
**Extraction type**: raw_table

| Methods | SH | MH | OD | Temp | Overall |
|---|---:|---:|---:|---:|---:|
| Mem0 | 47.65 | 38.72 | 28.64 | 48.93 | 45.09 |
| Mem0-graph | 49.27 | 38.09 | 24.32 | 51.55 | 46.14 |
| Zep | 49.56 | 35.74 | 41.37 | 52.04 | 47.03 |
| Full-Context | 55.64 | 43.52 | 40.43 | 58.32 | 53.03 |
| Claude | 41.23 | 34.23 | 28.06 | 46.32 | 40.19 |
| Openclaw | 20.12 | 11.36 | 10.04 | 21.32 | 18.14 |
| VikingMem | 59.59 | 44.52 | 43.13 | 55.62 | 54.98 |

## 中文说明
该表补充支撑 C03：在 token-level F1 上，VikingMem Overall 54.98，高于所列基线；Temporal 维度低于 Full-Context，但整体最高。
