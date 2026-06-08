# 相关工作

## RW01: Gao et al., 2024 — RAG survey
- **DOI**: arXiv:2312.10997
- **Type**: baseline
- **Delta**:
  - What changed: VikingMem 在静态文档/chunk 检索之外，引入 Event-Entity schema 与算子来管理可演化记忆状态。
  - Why: 长期交互流低密度、主题交错且状态持续变化。
- **Claims affected**: C01, C03, C05
- **Adopted elements**: Dense/sparse retrieval 概念，以及 RAG 作为比较族。

## RW02: Chhikara et al., 2025 — Mem0
- **DOI**: arXiv:2504.19413
- **Type**: baseline
- **Delta**:
  - What changed: VikingMem 用 schema-driven event/entity extraction 与 operator-based state evolution 替代/扩展 fact extraction 和 ADD/UPDATE/DELETE 式 memory。
  - Why: 论文认为已有系统跨业务场景泛化不足，并且在 multi-hop/temporal retrieval 上可能表现较弱。
- **Claims affected**: C03, C06
- **Adopted elements**: 长期记忆基线与评测 framing。

## RW03: Rasmussen et al., 2025 — Zep
- **DOI**: arXiv:2501.13956
- **Type**: baseline
- **Delta**:
  - What changed: VikingMem 使用可配置 Event-Entity model、operator library 与低延迟 multi-vector rerank，而不只依赖 temporal knowledge graph architecture。
  - Why: 强调更低延迟与更广 schema 泛化能力。
- **Claims affected**: C03
- **Adopted elements**: Temporal memory baseline、LongMemEval_s 评测实践、hybrid retrieval comparison。

## RW04: Wang and Chen, 2025 — MIRIX
- **DOI**: arXiv:2507.07957
- **Type**: baseline
- **Delta**:
  - What changed: VikingMem 使用统一 event/entity schema 和算子，而不是六个 specialized memory modules。
  - Why: 降低架构碎片化与延迟，同时保持准确性。
- **Claims affected**: C03, C06
- **Adopted elements**: 模块化记忆基线与评测参考。

## RW05: Memobase, 2025
- **DOI**: 论文 [39] 的 GitHub repository reference
- **Type**: bounds
- **Delta**:
  - What changed: VikingMem 被定位为比主要围绕 conversational user profiles 的系统更可配置。
  - Why: 窄域垂直 schema 难以直接表达 procedural SOPs 或其他非聊天记忆结构。
- **Claims affected**: C01, C04
- **Adopted elements**: §3.1/§5.3 中将 multi-prompt extraction 作为代表性 prior paradigm。

## RW06: Packer et al., 2023 — MemGPT
- **DOI**: arXiv:2310.08560
- **Type**: imports
- **Delta**:
  - What changed: VikingMem 更关注数据库式 memory substrate、显式 event/entity persistence 与 retrieval，而不是 OS-like LLM memory framing。
  - Why: 提供 service-grade、schema-configurable memory management。
- **Claims affected**: C01
- **Adopted elements**: LLM agents 长期记忆动机。

## RW07: Peng et al., 2023 与 Fei et al., 2024 — context extension/compression
- **DOI**: arXiv:2309.00071；ACL Findings 2024 work [10]
- **Type**: bounds
- **Delta**:
  - What changed: VikingMem 认为上下文扩展与语义压缩本身不能提供结构化生命周期状态管理。
  - Why: 持久应用需要 consolidation、provenance、forgetting 与 retrieval。
- **Claims affected**: C01
- **Adopted elements**: 上下文窗口限制和压缩动机。

## RW08: Barbero et al., 2024 — information over-squashing
- **DOI**: NeurIPS 2024 reference [2]
- **Type**: imports
- **Delta**:
  - What changed: VikingMem 通过选择性分段减少低价值上下文，而不是依赖盲目总结/截断。
  - Why: 避免无关上下文干扰或 over-squashing LLM。
- **Claims affected**: C01, C05, C06
- **Adopted elements**: 过滤低信号流的动机。

## RW09: RoocodeInc., 2026 — RooCode
- **DOI**: GitHub repository reference [49]
- **Type**: extends
- **Delta**:
  - What changed: VikingMem 将 search/replace patch 思路改造成 EUA，用于无需额外 LLM 调用的 entity update。
  - Why: 降低在线实体记忆更新的延迟和 token cost。
- **Claims affected**: C04
- **Adopted elements**: Patch-based update 灵感。

## RW10: Deng et al., 2013 — edit-distance constrained search
- **DOI**: ICDE 2013 reference [9]
- **Type**: imports
- **Delta**:
  - What changed: VikingMem 在 EUA 内使用 edit-distance-based approximate span matching。
  - Why: 使 patch application 对 LLM 的轻微字符串误差更鲁棒。
- **Claims affected**: C04
- **Adopted elements**: Approximate string matching 方法族。

## RW11: ColBERT / Khattab and Zaharia, 2020；vector quantization/token merge works
- **DOI**: 论文 references [25], [15], [26], [36]
- **Type**: imports
- **Delta**:
  - What changed: VikingMem 将 ColBERT-style late interaction 与预计算压缩 memory vectors 用于 memory reranking。
  - Why: 在避免 cross-encoder 延迟的同时提升检索精度。
- **Claims affected**: C03, C06
- **Adopted elements**: Late interaction 与 vector compression 概念。

## RW12: Graph-RAG 与 keyword/graph retrieval works
- **DOI**: 论文 references [20], [22], [67]
- **Type**: imports
- **Delta**:
  - What changed: VikingMem 用 keyword graph 增强 hybrid dense/sparse retrieval，以处理低语义重叠 query。
  - Why: 直接语义匹配可能漏掉 nickname 等记忆。
- **Claims affected**: C06
- **Adopted elements**: Graph 与 hybrid retrieval 灵感。

## RW13: Cognitive memory references
- **DOI**: 论文 references [17], [32], [38], [40], [48]
- **Type**: imports
- **Delta**:
  - What changed: VikingMem 将 event-based memory、consolidation 与 retention 思路转化为数据管理原语（events、entities、TIME_COMPRESS）。
  - Why: 提供 lifecycle-aware memory substrate。
- **Claims affected**: C01, C02
- **Adopted elements**: Event-based memory 与 consolidation 动机。

## RW14: Agent workflow/tool memory works
- **DOI**: 论文 references [60], [62], [65]
- **Type**: extends
- **Delta**:
  - What changed: VikingMem 将 agent workflow/tool memories 泛化为统一 Event-Entity MBMS 中的一个场景。
  - Why: 避免 agent-only 记忆系统孤岛，并把 SOP/tool experience 作为 entity view 物化。
- **Claims affected**: C01, C02
- **Adopted elements**: Agent memory 场景与 SOP/tool-usage 动机。

## Additional citation footprint
论文还引用了 LLM item-description generation 与 recommendation [1]、enterprise/digital collaboration [5]、education agents 与 education RAG [8, 55]、entity resolution [13]、RAG evaluation surveys [14]、approximate vector search/quantization [15, 26]、prospective/human memory 与 cognitive decline [17, 40]、long-context vs RAG [21]、personalized agents [23]、QA 与 retrieval [24, 29, 35, 51-54, 58, 66, 70]、基础 LLM 与 prompting [3, 47, 56, 61, 68, 69]、OpenClaw [41]、KVFlow/prefix caching [43]、SeCom [44]、Yarn/context extension [45]，以及 VikingMem 作者提供的外部制品 [11, 12, 57]。这些引用主要作为背景、基线来源、实现灵感或应用动机，并非每个都在 VikingMem 内形成单独技术 delta。
