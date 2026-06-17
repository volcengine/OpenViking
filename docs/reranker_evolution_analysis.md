# Reranker 模型演进方向分析

> 分析对象：OpenViking 记忆检索系统中的 Reranker 组件
> 目标：从"调用第三方 API"向"自研轻量模型"演进的路径规划

## 一、现状与背景

### 1.1 当前架构

OpenViking 采用 **"向量检索 + Reranker 精排"** 的两阶段检索范式，Reranker 在以下三个环节被调用：

| 环节 | 位置 | 作用 |
|------|------|------|
| 起始点排序 | `_merge_starting_points()` | 对全局搜索结果重排，确定递归检索的起始目录 |
| 全局候选精排 | `_prepare_initial_candidates()` | 对 L2 全局命中结果精排后加入候选池 |
| 子目录递归精排 | `_recursive_search()` | 每层目录下对子节点结果重排 |

当前 Reranker 以 **API 调用** 形式接入，支持多家供应商：
- **VikingDB / 豆包**（默认）：`doubao-seed-rerank`
- Cohere
- OpenAI 兼容接口
- LiteLLM

### 1.2 为什么要自研

原需求文档明确指向 **"更轻量的 reranker 模型（80M）"**，核心驱动因素推测为：

1. **成本**：Reranker 在层次检索的每一层都被调用，token 消耗大，API 成本随调用量线性增长
2. **延迟**：网络调用 + 第三方排队，单次 rerank 延迟不可控，影响整体检索响应时间
3. **数据安全**：记忆内容可能包含敏感信息，外放第三方 API 有数据合规风险
4. **场景定制**：通用 reranker 对 agent memory 场景（时间约束、因果推理、指代消解）优化不足
5. **私有化部署**：支持离线 / 本地部署场景，不依赖外部服务

---

## 二、候选方案对比分析

### 2.1 方案总览

| 维度 | Bocha Reranker | Jina Reranker V3 | MemReranker |
|------|----------------|------------------|-------------|
| **参数量** | 80M | 0.6B | 0.6B / 4B |
| **底座** | 自研 | Qwen3-0.6B | Qwen3-Reranker |
| **核心技术** | 未知（小模型蒸馏路线） | Last-but-not-Late Interaction 架构 | 多阶段 LLM 知识蒸馏 + 对比学习 |
| **擅长场景** | 中文搜索/文档检索 | 多语言 listwise 排序 | Agent Memory / 对话记忆检索 |
| **中文支持** | 优（主打中文） | 中（多语言） | 中（中英文） |
| **许可证** | 商业 API | CC BY-NC 4.0（非商用免费） | Apache 2.0（完全开源） |
| **推理延迟** | 极快（80M） | 快（0.6B） | 较快（0.6B 版本） |
| **可直接商用** | 付费 API | 需授权 | ✅ 可 |

### 2.2 Bocha Semantic Reranker（博查）

**核心信息：**
- 80M 参数实现接近 280M / 560M 模型的排序效果
- 提供中文 / 英文两个模型版本
- 以 API 形式提供，已开放 `gte-rerank` 模型，`bocha-semantic-reranker-cn/en` 在邀测
- 评分范围 0~1，有明确的分数含义分级（0.75+ 高度相关，0.5~0.75 相关但不完整等）

**借鉴价值：**
- **80M 参数量级**是轻量部署的标杆，验证了小模型 rerank 的可行性
- **中文优化**是我们需要重点关注的方向（OpenViking 面向中文用户）
- API 接口范式与我们现有的 Rerank 接口兼容，可作为过渡方案先接入

**局限性：**
- 不开源，无法基于自身数据继续微调
- 仍为 API 调用模式，不能从根本上解决成本和数据安全问题
- 通用搜索场景优化，非 agent memory 场景定制

### 2.3 Jina Reranker V3

**核心信息：**
- 0.6B 参数，基于 Qwen3-0.6B 底座（28 层 Transformer）
- 创新的 **Last-but-not-Late Interaction** 架构
  - 与 ColBERT 的分离编码 + 多向量匹配不同
  - 在同一个上下文窗口内进行 query-documents 的因果自注意力
  - 从每个 document 的最后一个 token 提取上下文嵌入
- **Listwise 排序**：可同时处理最多 64 篇文档，上下文窗口 131K tokens
- BEIR nDCG@10 达到 61.94，比同量级 BGE-Reranker 高 5+ 个点
- 多语言支持（中、英、法等）

**借鉴价值：**
- **listwise 范式**：一次前向同时排 N 个文档，相比 pointwise / pairwise 效率更高，排序质量更好
- **Late Interaction 架构**：在计算效率和排序质量之间取得了很好的平衡
- 0.6B 参数量级在效果上已非常有竞争力，可作为中等规格的选型参考
- 轻量级 MLP projector（1024→512→256）的设计思路可参考

**局限性：**
- CC BY-NC 4.0 许可证，**商业使用需授权**
- 0.6B 对于"80M 轻量"目标还是偏大
- 通用检索场景优化，非记忆检索定制

### 2.4 MemReranker（重点推荐）

**核心信息：**
- **专为 Agent Memory 场景设计**的 reranker 模型家族（0.6B / 4B）
- 基于 Qwen3-Reranker 微调，采用两阶段训练范式：
  - **阶段 1：BCE 逐点蒸馏** — 多教师两两比较 + Elo/Bradley-Terry 五级评分体系生成校准软标签
  - **阶段 2：InfoNCE 对比微调** — 增强难例区分能力
- 训练数据结合通用语料 + **记忆场景专用多轮对话数据**（时间约束、因果推理、指代消解）

**关键指标（LOCOMO Memory Retrieval Benchmark）：**

| 模型 | MAP | MRR | NDCG@10 | 推理延迟 |
|------|-----|-----|---------|----------|
| BGE-v2-m3 | 0.671 | 0.699 | 0.714 | - |
| Qwen3-Reranker-0.6B | 0.643 | 0.673 | 0.689 | - |
| Qwen3-Reranker-4B | 0.689 | 0.716 | 0.732 | - |
| GPT-4o-mini | 0.715 | 0.742 | 0.753 | ~1s+ |
| **MemReranker-0.6B** | **0.715** | **0.738** | **0.754** | **~200ms** |
| **MemReranker-4B** | **0.737** | **0.760** | **0.773** | 稍高 |
| Gemini-3-Flash | 0.777 | 0.797 | 0.807 | - |

**针对性解决记忆检索的三大痛点：**
1. **分数校准差（Score Miscalibration）** — 通用模型的相关性分数分布不均，难以用阈值过滤
2. **复杂查询退化（Complex Query Degradation）** — 面对时间约束、因果推理等复杂查询时排序质量下降
3. **上下文消歧困难（Context Disambiguation）** — 无法利用对话上下文进行语义消歧

**借鉴价值：**
- **场景高度匹配**：Agent memory 正是 OpenViking 的核心场景，LOCOMO benchmark 与我们的场景高度一致
- **训练方法论可直接复用**：两阶段蒸馏 + 对比学习的训练范式是已验证的有效路径
- **Apache 2.0 许可证**：无商用限制，可基于此模型继续做领域微调
- **效果超越同量级模型**：0.6B 版本打平 GPT-4o-mini，4B 版本接近 Gemini-3-Flash
- **推理延迟低**：0.6B 版本约 200ms，仅为大模型的 10%~20%

**局限性：**
- 0.6B 对于"80M 超轻量"目标还是偏大，但 0.6B 是已经验证的"效果-效率"甜点
- 中文能力未明确说明（基于 Qwen3 底座，应有基础中文能力，但需验证）
- 模型较新（2026 年 5 月发布），社区验证还不够充分

---

## 三、OpenViking Reranker 演进路线

### 3.1 演进三阶段

```
阶段一：快速接入    阶段二：领域微调      阶段三：自研蒸馏
  (1-2 月)          (3-6 月)            (6-12 月)
     │                  │                    │
     ▼                  ▼                    ▼
  接入现有 API    基于开源底座微调     自研 80M 蒸馏模型
  验证场景价值      提升场景效果        极致轻量私有化
```

### 3.2 阶段一：快速接入与价值验证

**目标**：快速引入轻量 reranker 能力，验证在 OpenViking 记忆检索场景下的实际收益

**动作**：
1. **接入 Bocha API** 作为轻量选项
   - 新增 `BochaRerankClient`，遵循现有 `RerankBase` 接口
   - 在 `RerankConfig` 中增加 bocha 配置项
   - 提供 `gte-rerank` 和 `bocha-semantic-reranker-cn` 两个模型选择
   
2. **建立评测基线**
   - 使用 LOCOMO 或自建记忆检索评测集
   - 对比当前 doubao reranker、Bocha reranker 的效果差异
   - 建立 MAP / MRR / NDCG@10 等核心指标的基线

3. **成本与延迟测算**
   - 统计单次会话的 rerank 调用次数、总 token 数
   - 对比不同方案的单次请求成本和端到端延迟

**产出**：明确轻量 reranker 的效果-成本收益比，为后续投入提供数据支撑

### 3.3 阶段二：基于开源底座的领域微调

**目标**：基于开源 reranker 底座，用 OpenViking 的真实记忆数据做领域微调，打造更贴合 agent memory 场景的模型

**选型建议：MemReranker-0.6B 作为底座**

选择理由：
- Apache 2.0 许可，无商用风险
- 原生面向 agent memory 场景优化，起点更高
- 0.6B 参数在效果和效率间取得良好平衡
- 训练方法论（两阶段蒸馏 + 对比学习）已被验证有效

**微调方向**：

| 方向 | 说明 | 预期收益 |
|------|------|----------|
| **中文增强** | 补充中文对话记忆数据，提升中文场景效果 | 中文 NDCG@10 提升 3~5% |
| **记忆类型适配** | 针对 events / entities / preferences / experiences 等记忆类型构建专用微调数据 | 类型相关查询的排序质量提升 |
| **多轮上下文感知** | 利用对话历史进行查询消歧，支持 context-aware reranking | 指代消解类查询准确率提升 |
| **时间推理增强** | 强化时间约束、时序推理能力 | 时间相关查询准确率提升 |
| **分数校准** | 用真实标注数据优化分数分布，提升阈值过滤的可靠性 | 降低误召回 / 漏召回率 |

**工程落地**：
- 部署形态：vLLM 推理服务，提供 OpenAI 兼容 API
- 与现有 Reranker 接口无缝切换
- 支持本地 CPU 推理（量化后）作为 fallback

### 3.4 阶段三：自研 80M 级蒸馏模型

**目标**：将 0.6B 模型的能力蒸馏到 80M 级小模型，实现极致轻量化和私有化部署

**技术路线（参考 MemReranker + Bocha 的思路）**：

1. **教师模型选择**
   - 主教师：阶段二产出的 0.6B 领域微调模型
   - 辅助教师：GPT-4o-mini / 豆包等大模型（用于难例增强）

2. **蒸馏策略**
   - **Logit 蒸馏**：学习教师模型的 yes/no 概率分布
   - **层级蒸馏**：从中间层隐藏状态蒸馏（可选，视效果而定）
   - **多级评分体系**：借鉴 MemReranker 的五级评分 + Elo/Bradley-Terry 校准

3. **数据策略**
   - 通用检索数据 + 记忆场景专用数据混合
   - 难例挖掘：用向量检索的 hard negative 增强训练
   - 数据增强：同义词替换、改写、噪声注入

4. **架构选型**
   - 方案 A：Cross-Encoder 小模型（类似 BGE-Reranker 的结构）
   - 方案 B：参考 Jina 的 Late Interaction 架构，做 listwise 排序
   - 方案 C：更极致的 Bi-Encoder + 浅层交互（速度最快，效果略低）

5. **部署形态**
   - 支持 ONNX / TensorRT 量化部署
   - CPU 实时推理（目标 < 50ms / 次）
   - 可嵌入 SDK 离线运行

**预期效果**：
- 效果达到 0.6B 模型的 90% 以上
- 推理速度提升 5~10 倍
- 模型体积压缩到 200MB 以内（量化后）

---

## 四、关键技术决策点

### 4.1 Pointwise vs Pairwise vs Listwise

| 范式 | 原理 | 优点 | 缺点 | 代表模型 |
|------|------|------|------|----------|
| **Pointwise** | 对每个文档独立打分 | 简单、易实现、推理快 | 不考虑文档间关系 | BGE-Reranker |
| **Pairwise** | 比较文档对的相对顺序 | 排序效果优于 pointwise | 训练复杂度高 | — |
| **Listwise** | 一次对整组文档排序 | 效果最好、效率最高 | 架构更复杂 | Jina Reranker V3 |

**建议**：阶段二先从 pointwise 入手（兼容现有接口，改动最小）；阶段三考虑 listwise 架构以追求极致效率。

### 4.2 参数量选择

| 规格 | 参数量 | 适用场景 | 推理延迟（估） |
|------|--------|----------|----------------|
| **超轻量** | 80M | 端侧部署、极低延迟要求 | < 50ms |
| **轻量** | 0.3B~0.6B | 服务端部署、性价比最优 | 100~300ms |
| **标准** | 2B~4B | 追求极致效果、不计成本 | 500ms~1s |

**建议**：
- 短期（阶段一~二）以 **0.6B** 为目标，效果和效率平衡最佳
- 长期（阶段三）探索 **80M** 蒸馏，满足私有化和端侧需求
- 80M 与 0.6B 并行存在，分别服务不同部署场景

### 4.3 训练数据来源

1. **公开数据集**：MS MARCO、BEIR、LOCOMO 等
2. **业务数据**：OpenViking 真实记忆检索日志（需脱敏）
3. **合成数据**：用 LLM 生成 query-document 配对，特别是复杂推理类
4. **难例挖掘**：从线上 badcase 中挖掘 hard negative

---

## 五、风险与挑战

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| **小模型效果天花板** | 80M 可能达不到预期效果 | 先验证 0.6B 再蒸馏，有 fallback 方案 |
| **中文效果不确定** | MemReranker 中文能力未验证 | 第一时间做中文评测，必要时补充中文数据 |
| **训练数据质量** | 记忆场景缺乏标注数据 | 用 LLM 自动标注 + 人工抽检，控制数据质量 |
| **工程复杂度** | 自研模型需要 ML 工程能力 | 可先基于 vLLM 部署开源模型，逐步迭代 |
| **维护成本** | 自研模型需要持续训练和优化 | 与业务迭代绑定，用业务效果驱动模型迭代 |

---

## 六、下一步行动建议

1. **本周**：接入 Bocha Reranker API，跑通通路上线
2. **两周内**：建立记忆检索评测集，完成现有方案与 Bocha 的效果对比
3. **一个月内**：部署 MemReranker-0.6B 开源模型，验证在 OpenViking 场景下的表现
4. **Q3 启动**：基于 MemReranker 做中文 + 记忆场景领域微调
5. **Q4 启动**：80M 蒸馏模型预研

---

## 参考资料

1. [OpenViking 的 Rerank 需求](https://bytedance.larkoffice.com/wiki/VhWEwYUSViA9LvknDztcVYj7nue) — 需求来源
2. [Semantic Reranker API（Bocha）](https://bocha-ai.feishu.cn/wiki/LHwfwDUGeihkJ2kOlj2cccuNndh) — 80M 中文 reranker
3. [jinaai/jina-reranker-v3](https://huggingface.co/jinaai/jina-reranker-v3) — 0.6B listwise reranker
4. [IAAR-Shanghai/MemReranker-4B](https://huggingface.co/IAAR-Shanghai/MemReranker-4B) — Agent memory 专用 reranker
5. [MemReranker: The AI Model That Outplays Heavyweights in Memory Retrieval](https://www.machinebrief.com/news/memreranker-the-ai-model-that-outplays-heavyweights-in-memor-dhrd) — 技术解读
