# 架构

## 系统上下文
VikingMem 是构建在 VikingDB 向量引擎上的云原生 Memory Base Management System。它处理原始交互流，为有状态 LLM 应用提供长期记忆：把 session history 转换为持久 event/entity memory，并在后续 query 中检索相关记忆上下文。

## 组件：Input Data Stream 与 Session Buffer
- **Purpose**: 将历史消息聚合成逻辑 session，用于长期记忆抽取。
- **Inputs**: 可选 user profile、historical messages、active session messages、query stream。
- **Outputs**: 送入抽取模块的 batched input messages；保留在当前上下文的 short-term memory。
- **Interactions**: 向 Extract Module 供给数据；short-term memory 也参与最终回复。
- **Key design choices**: 论文称累计至少 20 messages 的 threshold 往往能产生更稳定和高质量的 memories。

## 组件：Extract Module
- **Purpose**: 将低密度原始流转换为结构化 long-term memories。
- **Inputs**: Input messages、system instruction、memory schema、fixed prompt prefix、可选 user profile。
- **Outputs**: Event memories、entity-related events/patches、other events。
- **Interactions**: 输出到 Storage and Management；对固定 prompt prefix 使用 prefix-cache。
- **Key design choices**: schema-driven one-pass extraction 取代 multi-prompt extraction，并利用 ICL 在一次 LLM pass 中抽取全部 memory types。

## 组件：Memory Schema Compiler
- **Purpose**: 将用户定义的 event/entity schema 编译为抽取 prompt。
- **Inputs**: Event schemas、entity schemas、system instruction。
- **Outputs**: 嵌入固定抽取前缀的 event prompt 与 entity prompt。
- **Interactions**: 约束 LLM 抽取行为。
- **Key design choices**: 应用特定 prompt 被放在 pipeline 边缘；整体转换模式保持 schema/operator 驱动。

## 组件：Intelligent Memory Segmentation
- **Purpose**: 在 event-intertwined sessions 中识别高价值语义片段，并合并同一主题的非连续片段。
- **Inputs**: Raw dialogue/session data。
- **Outputs**: coherent events 的 coordinate-like start/end tuples 与过滤后的高价值片段。
- **Interactions**: 在 memory extraction 内运行，再进入 event memory 存储。
- **Key design choices**: 两阶段：semantic saliency filtering 与 event-centric partitioning。

## 组件：Storage and Management
- **Purpose**: 持久化并更新 event/entity memories。
- **Inputs**: Extracted event memories、entity updates/patches、existing events/entities。
- **Outputs**: 更新后的 event store、entity store、old-event compressed summaries、keyword graph。
- **Interactions**: 由 VikingDB 支撑；向 retrieval 提供候选 memories，也为 entity update 提供候选 entities。
- **Key design choices**: deduplication、operator-based entity updates、TIME_COMPRESS timeline compression、TTL pruning、keyword graph updates。

## 组件：Entity Memory Update
- **Purpose**: 维护持久状态表示。
- **Inputs**: Old entity、相关 event attributes、operator 或 field-wise patch。
- **Outputs**: Updated entity。
- **Interactions**: Entity property 通过 AggregateExpression 指定 event type/property 与更新 operator。
- **Key design choices**: 统计算子避免 LLM 算术错误；LLM_MERGE 处理文本合成；EUA 对可 patch 的字符串更新避免额外 LLM 调用。

## 组件：Keyword Graph
- **Purpose**: 对直接语义相似度很低的 query 提供辅助召回。
- **Inputs**: keywords 与包含这些 keywords 的 memory segments。
- **Outputs**: keyword-linked memory retrieval candidates。
- **Interactions**: 供给辅助检索路径，并与主 hybrid search 结果合并。
- **Key design choices**: keyword embedding 由包含该 keyword 的 memory segment embeddings 平均得到。

## 组件：Retrieve Module
- **Purpose**: 为 query 检索并排序 long-term memory。
- **Inputs**: Query、long-term memory store、keyword graph、time/business weights。
- **Outputs**: Multi-path retrieved memory。
- **Interactions**: 候选送入 multi-vector rerank；最终 memory context 进入回复生成。
- **Key design choices**: 主路径是 dense/sparse hybrid vector search，并叠加 time-decay 与 business weighting；辅助路径是 keyword graph recall；各路径独立排序、分配 quota 后再合并。

## 组件：Multi-vector Rerank
- **Purpose**: 在交互延迟约束内提升 memory search 精度。
- **Inputs**: Candidate memories 与预计算的 ColBERT-style memory vectors。
- **Outputs**: Reranked memory list。
- **Interactions**: 接收 multi-path recall 输出并返回最终 long-term memory context。
- **Key design choices**: 受 ColBERT 启发的 late interaction；用 quantization 与 token merge 压缩预计算向量。

## 组件：Reply with Memory
- **Purpose**: 用 query、short-term memory、可选 updated profile 和 retrieved long-term memory 生成下游 LLM response。
- **Inputs**: Query、short-term memory、reranked long-term memory、可选 updated profile。
- **Outputs**: 面向用户/应用的 response。
- **Interactions**: 同时消费 active session context 与 retrieved persistent state。
- **Key design choices**: 将 active-session short-term memory 与抽取出的 persistent long-term memory 分离。
