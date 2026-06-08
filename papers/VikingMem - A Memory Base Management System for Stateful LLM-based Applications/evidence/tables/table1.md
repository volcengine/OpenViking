# Table 1: LLM-as-a-Judge evaluation scores and Search Latency

**Source**: Table 1, §5.2
**Caption**: "LLM-as-a-Judge evaluation scores (%, with higher values denoting superior performance) and Search Latency (second) for each question category in the LOCOMO and LongMemEval dataset. Scores for baselines annotated with a superscript † are sourced from published evaluation results, while the search latencies for Memobase in LOCOMO and Zep in LongMemEval were measured from our own experiments. Best results are in bold; second-best results are with underline. In LongMemEval, \"SSU\" stands for \"single-session-user\", \"MS\" denotes \"multi-session\", \"SSP\" represents \"single-session-preference\", \"TR\" indicates \"temporal-reasoning\", \"KU\" refers to \"knowledge-update\", and \"SSA\" means \"single-session-assistant\"."
**Screenshot**: table1.png
**Extraction type**: raw_table

## LOCOMO

| LLM Model | Method | Single Hop | Multi-Hop | Open Domain | Temporal | Overall | p50 | p95 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| GPT-4o-mini | Mem0† | 72.93 | 67.13 | 51.15 | 55.51 | 66.88 | 0.15 | 0.20 |
| GPT-4o-mini | Mem0-graph† | 75.71 | 65.71 | 47.19 | 58.13 | 68.44 | 0.48 | 0.66 |
| GPT-4o-mini | Zep† | 74.11 | 66.04 | 67.71 | 79.79 | 75.14 | 0.42 | 0.63 |
| GPT-4o-mini | RAG | 65.16 | 50.35 | 48.96 | 59.50 | 60.26 | 0.22 | 0.43 |
| GPT-4o-mini | Full-Context | 78.00 | 74.82 | 59.38 | 83.80 | 77.47 | / | / |
| GPT-4o-mini | Claude | 63.50 | 55.67 | 50.00 | 47.35 | 57.86 | 18.90 | 25.42 |
| GPT-4o-mini | Openclaw | 17.60 | 13.48 | 30.21 | 13.40 | 16.75 | 17.19 | 26.26 |
| GPT-4o-mini | Mirix | 79.08 | 76.01 | 66.67 | 80.86 | 78.66 | 10.90 | 25.76 |
| GPT-4o-mini | VikingMem | 94.89 | 81.91 | 78.12 | 82.24 | 88.83 | 0.20 | 0.39 |
| GPT-4.1-mini | Mem0† | 62.41 | 57.32 | 44.79 | 66.47 | 62.47 | 0.15 | 0.20 |
| GPT-4.1-mini | Mem0-graph | 74.44 | 68.44 | 51.04 | 54.52 | 67.73 | 0.48 | 0.66 |
| GPT-4.1-mini | Zep† | 79.43 | 69.16 | 73.96 | 83.33 | 79.09 | 0.42 | 0.63 |
| GPT-4.1-mini | RAG | 69.56 | 56.74 | 57.29 | 64.17 | 65.32 | 0.27 | 0.91 |
| GPT-4.1-mini | Full-Context | 88.70 | 77.30 | 72.92 | 92.21 | 86.36 | / | / |
| GPT-4.1-mini | Claude | 72.29 | 68.44 | 56.25 | 55.45 | 67.08 | 10.81 | 18.62 |
| GPT-4.1-mini | Openclaw | 26.52 | 18.09 | 26.04 | 21.81 | 23.96 | 13.73 | 19.26 |
| GPT-4.1-mini | Mirix† | 85.11 | 83.70 | 65.62 | 88.39 | 85.38 | 10.90 | 25.76 |
| GPT-4.1-mini | VikingMem | 93.46 | 85.89 | 79.79 | 88.16 | 90.12 | 0.20 | 0.39 |

## LongMemEval

| LLM Model | Method | SSU | MS | SSP | TR | KU | SSA | Overall | p50 | p95 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| GPT-4o-mini | Mem0 | 68.57 | 41.35 | 20.00 | 37.59 | 66.67 | 5.36 | 42.80 | 0.40 | 1.00 |
| GPT-4o-mini | Mem0-graph | 72.86 | 38.35 | 16.67 | 34.59 | 60.26 | 35.71 | 44.00 | 1.10 | 2.09 |
| GPT-4o-mini | Zep† | 92.90 | 47.40 | 53.30 | 54.10 | 74.40 | 75.00 | 63.21 | 3.80 | 5.45 |
| GPT-4o-mini | Full-Context | 81.43 | 40.60 | 30.00 | 36.84 | 76.92 | 82.14 | 55.00 | / | / |
| GPT-4o-mini | VikingMem | 90.00 | 53.31 | 46.67 | 55.89 | 71.23 | 96.43 | 66.36 | 0.25 | 0.89 |
| GPT-4o | Mem0 | 80.00 | 50.38 | 26.67 | 42.11 | 74.36 | 10.71 | 50.20 | 0.40 | 1.00 |
| GPT-4o | Mem0-graph | 84.29 | 48.87 | 30.00 | 41.35 | 75.64 | 48.21 | 54.80 | 1.10 | 2.09 |
| GPT-4o | Zep† | 92.90 | 57.90 | 56.70 | 62.40 | 83.30 | 80.40 | 70.40 | 3.80 | 5.45 |
| GPT-4o | Full-Context | 81.43 | 44.36 | 20.00 | 45.11 | 78.21 | 94.64 | 59.20 | / | / |
| GPT-4o | VikingMem | 92.90 | 69.92 | 56.70 | 66.92 | 76.92 | 98.21 | 75.80 | 0.25 | 0.89 |

## 中文说明
该表是 C03 的核心证据：在四个报告设置中，VikingMem 的 Overall 分数分别为 88.83、90.12、66.36、75.80，均为对应设置的最高 Overall。延迟列同时显示 VikingMem 保持低延迟量级。
