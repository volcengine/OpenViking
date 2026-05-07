# OpenViking Working Memory v2 — 测试报告

> 数据集：locomo10 · MemoryArena group_travel_planner · 模型：doubao-seed-2-0-code-preview  
> 创建：2026-05-02

---

## 核心结论

1. **LoCoMo 长对话事实召回**：WM v2 在 152Q 上达 **79.61%**（旧版 23.68%，**+55.93pp**）；35Q 上达 **94.29%**（旧版 28.57%，**+65.7pp**）。
2. **Token 效率**：
   - **35Q**：WM v2 总 QA tokens **184,497**（旧版 144,797，**多 27.4%**）；但准确率涨 +65.7pp，单题成本（tok/correct）从 14,480 降到 **5,591**（约 **1/2.6**）。
   - **152Q**：WM v2 总 QA tokens **1,510,190**（旧版 2,280,098，**节省 33.8%**）；准确率涨 +55.93pp，单题成本从 63,336 降到 **12,481**（约 **1/5.1**）。
   - 整体趋势：35Q 是「以 token 换准确率」（总量略增 + 单题大幅降本），152Q 是**双向收益**（总量降 + 单题降）。
3. **纯工作记忆是主体收益**：关闭长期记忆向量召回仍能达 **73.03%**（152Q），autoRecall 在此基础上再补 **+6.58pp**。
4. **MemoryArena 跨会话规划任务**：跟 OV 主分支无 WM 严格 A/B 大体持平（**QA +3.06pp / Action −2.04pp**）；跟 MC 原生比 WM v2 显著领先（**QA +35.03pp / token −21.76%**）。该任务不直接验证工作记忆，结论不外推到 LoCoMo。

---

## 一、测试目标

验证 WM v2（结构化 7 段模板 + tool_call 增量更新 + 服务端 Guards + `keep_recent_count`）相比旧版（`compression.structured_summary` v1）的实际效果。重点回答 3 个问题：

1. WM v2 在小样本（35Q）和大样本（152Q）下相对 Main 的准确率提升
2. WM v2 结构化 overview 的独立贡献（关闭向量召回）
3. WM v2 + autoRecall 联合方案的最佳效果

---

## 二、测试环境

| 项 | 值 |
|---|---|
| LLM | doubao-seed-2-0-code-preview |
| Embedding | doubao-embedding-vision-251215 |
| Gateway | OpenClaw 2026.4.27 |
| Main 分支 | OpenViking @ `origin/main`，commit `4d6f5b65` |
| WM2 分支 | 工作记忆代码 |
| Judge | doubao-seed-2-0-code-preview-260215 |

---

## 三、LoCoMo 测试结果

### 3.1 测试组

| 测试组 | 说明 | autoRecall | 设计目的 |
|---|---|---|---|
| **WM2** | 工作记忆 + 长期记忆 + 工具回溯 | on | WM v2 全功能端到端测试 |
| WM2-NOREC | 工作记忆 + 工具回溯（关闭长期记忆） | off | 隔离工作记忆 + 工具回溯的独立贡献，看不靠向量召回时还能拿多少分 |
| MAIN | 原 overview + 长期记忆 + 老版工具回溯（仓库主分支代码） | on | 旧版基线 |
| MC | OpenClaw 原生记忆（关闭 OpenViking） | — | 横向对比 OpenClaw 原生记忆方案 |

### 3.2 数据集

| 数据集 | session 数 | QA 数 | 说明 |
|---|---|---|---|
| locomo-small | 19 | 35 | LoCoMo sample 0 前 35 题，小样本快速对照 |
| locomo10 case0 | 19 | 152 | LoCoMo sample 0 完整 |

> Ingest 和 QA 同会话（QA 会话连续，复用前题上下文）——目的是测试工作记忆。

### 3.3 locomo-small（35Q）

| 测试组 | 准确率 | QA tokens | tok/correct |
|---|---|---|---|
| MAIN | 28.57% (10/35) | 144,797 | 14,480 |
| **WM2** | **94.29% (33/35)** | **184,497** | **5,591** |
| WM2-NOREC | 88.57% (31/35) | 124,246 | **4,008** |
| MC | 42.86% (15/35) | 2,352,395 | 156,826 |

**对比**：

- **纯工作记忆（WM2-NOREC 测试组，关闭长期记忆向量召回）相比旧版 MAIN**：准确率从 28.57% 提升到 88.57%，**+60.0pp**；QA tokens 反而下降 14.2%（144,797 → 124,246），单题成本从 14,480 降到 **4,008**（**3.6× 效率**）。仅靠结构化 7 段 overview + 工具回溯，无需任何向量召回，已经能拿到大部分提升，且 token 同时节省。
- **叠加长期记忆向量召回（WM2 测试组）相比纯工作记忆**：准确率再升 **+5.7pp**（88.57% → 94.29%）；代价是 QA tokens 增加 48.5%（124,246 → 184,497），单题成本从 4,008 升到 **5,591**。长期记忆是用 token 换最后一段准确率的细节召回，边际收益递减但仍正向。
- **OpenClaw 原生记忆（MC 测试组，关闭 OpenViking 改由 LLM 主动 memorySearch）横向对照**：仅 42.86%，单题成本 156,826（是 WM2 的 **28 倍**）——准确率比 WM2 低 **51.4pp**，无竞争力。

### 3.4 locomo10 case0（152Q）

将 §3.3 对照扩展到 locomo10 case0 全量 152 道 QA：

| 测试组 | 准确率 | QA tokens | tok/correct |
|---|---|---|---|
| MAIN | 23.68% (36/152) | 2,280,098 | 63,336 |
| **WM2** | **79.61% (121/152)** | **1,510,190** | **12,481** |
| WM2-NOREC | 73.03% (111/152) | 1,622,319 | 14,615 |

**对比**：

- **纯工作记忆（WM2-NOREC 测试组，关闭长期记忆向量召回）相比旧版 MAIN**：准确率从 23.68% 提升到 73.03%，**+49.35pp**；QA tokens 同时下降 28.8%（2,280,098 → 1,622,319），单题成本从 63,336 降到 **14,615**（**4.3× 效率**）。在大样本上，纯结构化工作记忆已贡献整体提升的约 88%（49.35 / 55.93），且 token 大幅节省。
- **叠加长期记忆向量召回（WM2 测试组）相比纯工作记忆**：准确率再升 **+6.58pp**（73.03% → 79.61%）；QA tokens **再节省 6.9%**（1,622,319 → 1,510,190），单题成本从 14,615 降到 **12,481**。与 35Q 的「以 token 换准确率」不同，长样本上长期记忆是**双向收益**——既提升准确率，又因更高效答题而节省 token。

---

## 四、MemoryArena Group Travel 对比测试

MemoryArena `group_travel_planner` 是跨会话的旅行规划任务（slot-filling + 后续 QA），**不直接验证工作记忆能力**——每个 task 是独立 session，没有跨题上下文累积。本节把它作为另一个测试场景，从两个角度看 WM v2 的表现：

1. **跟 MC 原生比**（§4.2）：WM v2 相对「OpenClaw 自带 memorySearch」的整体能力差距
2. **严格 A/B**（§4.3）：跟 OV 主分支无 WM（OV-noWM）做同源对比，单变量评估 WM 改造对此类任务有无副作用

### 4.1 数据集与方法

| 维度 | 说明 |
|---|---|
| 数据集 | MemoryArena `group_travel_planner`（270 task / 1869 subtask 的多日多人旅行规划） |
| 子样本 | sample0 / sample1 / sample2 共 **294 道 slot-level QA** |
| 任务结构 | slot-filling 旅行规划 + 后续 QA（跨 task 独立 session） |

**指标说明**：

- **Action**：slot-filling 规划阶段的执行准确率——agent 在多步规划过程（订机票、订酒店、选餐厅等）中正确填充 expected slot（如 flight number / departure time / arrival time）的比例。衡量 agent 在规划**执行阶段**的动作准确性。
- **QA**：slot-filling 完成后问答阶段的答题准确率——agent 基于 task 上下文回答 slot-level 问题的正确率。衡量 agent 在**记忆/检索阶段**的能力。
- **Combined Tokens**：规划阶段（run）+ 问答阶段（QA）两阶段消耗的总 token。

### 4.2 WM v2 vs MC 原生

把 WM v2 跟 MC 原生（关闭 OpenViking、由 LLM 主动调 `memorySearch`）放在同一数据集上对比：

| 方案 | sample0 Action | sample1 Action | sample2 Action | **Agg Action** | **Agg QA** | Combined Tokens |
|---|---|---|---|---|---|---|
| MC memorySearch | 8/104 | 24/70 | 26/120 | 58/294 (19.73%) | 74/294 (25.17%) | 7,158,830 |
| **WM v2** | **66/104** | **38/70** | **79/120** | **183/294 (62.24%)** | **177/294 (60.20%)** | 5,601,097 |

WM v2 相比 MC 原生：**Action +42.52pp**，**QA +35.03pp**，Combined Tokens **−21.76%**。

### 4.3 严格 A/B（WM v2 vs OV-noWM）

| Sample | OV-noWM Action | OV-noWM QA | WM v2 Action | WM v2 QA |
|---|---|---|---|---|
| sample0 | 71/104 (**68.27%**) | 65/104 (**62.50%**) | 66/104 (63.46%) | 60/104 (57.69%) |
| sample1 | 52/70 (**74.29%**) | 45/70 (**64.29%**) | 38/70 (54.29%) | 38/70 (54.29%) |
| sample2 | 66/120 (55.00%) | 58/120 (48.33%) | **79/120 (65.83%)** | **79/120 (65.83%)** |
| **Aggregate** | **189/294 (64.29%)** | 168/294 (57.14%) | 183/294 (62.24%) | **177/294 (60.20%)** |

WM v2 vs OV-noWM：**Action −2.04pp**（OV-noWM 略胜），**QA +3.06pp**（WM 略胜），token +2.75%。per-sample 异质性较高（sample1 OV-noWM 大幅领先、sample2 WM v2 大幅领先），aggregate 大体持平。

### 4.4 观察

- **跟 MC 原生比**：WM v2 在 QA 准确率上 **+35.03pp**（25.17% → 60.20%），同时 token 节省 **21.76%**——在 slot-filling 任务上 OpenViking + 工作记忆的整体表现远胜 MC 自检索方案。
- **跟 OV-noWM 严格 A/B**：QA 略胜（**+3.06pp**）/ Action 略输（**−2.04pp**）/ token 略增（**+2.75%**）——没有显著退化也没有显著提升，符合预期（slot-filling 不直接受工作记忆改造影响）。
- **任务定位**：MemoryArena 是跨会话规划任务，每个 task 独立 session，**不直接测工作记忆**；以上两组对比说明 WM v2 在此类任务上**没有副作用**，且仍显著优于 MC 自检索方案。

---

> **创建**：2026-05-02  
