# Reindex 与索引管线复用二期设计

## 背景

当前一期只收敛了 reindex 中最容易重复的部分：资源目录和资源文件的向量重建直接复用 `vectorize_directory_meta` 与 `vectorize_file`，并把 `.overview.md` 解析、memory chunk 规则、skill 语义重建抽成可复用逻辑。

仍然保留在 `ReindexExecutor` 内部的主要是编排逻辑：URI 分类、namespace 遍历、任务计数、错误隔离，以及 memory/skill 在已有数据上的降级重建策略。这些逻辑短期内不应该为了“抽公共”而拆出薄 wrapper，但可以继续演进成更明确的索引管线接口。

## 二期目标

1. 建立统一的“索引提交”接口，同时支持异步入队和同步执行。
2. 让 resource、memory、skill 的 L0/L1/L2 Context 构造都通过领域公共入口完成。
3. 让 reindex 只负责：
   - 选择目标范围；
   - 调用对应领域的 semantic rebuild / vector rebuild；
   - 汇总 counters 和 warnings。
4. 避免新增“函数 A 只调用函数 B”的过渡层；公共函数必须封装真实策略或跨模块契约。

## 建议设计

### 1. 提交器抽象

保留当前一期在 `vectorize_file` / `vectorize_directory_meta` 中引入的 `submit_embedding_msg` 参数，并扩展为稳定约定：

- 默认提交器：enqueue 到 embedding queue。
- reindex 提交器：直接调用 `TextEmbeddingHandler.on_dequeue`。
- 测试提交器：捕获 `EmbeddingMsg`，不依赖真实队列或向量库。

这个接口可以继续作为二期 memory/skill 公共入口的提交方式，不需要重新设计。

### 2. Resource 索引入口

把 `index_resource` 从“单目录扫描并入队”扩展为更通用的 resource indexing service：

- 支持传入已收集的目录/文件列表，避免 reindex 再实现一次遍历后的向量构造。
- 支持 `semantic_only`、`vectors_only`、`semantic_and_vectors`。
- 支持同步提交器。

`ReindexExecutor._reindex_resource_vectors_from_entries` 最终应退化为调用这个公共入口，并只负责 counters 映射。

### 3. Memory 索引入口

把 `SessionCompressor._index_memory` 中的 memory Context 入队逻辑整理为公共 memory indexing helper：

- 输入：memory file uri、body、abstract/overview fallback、ctx。
- 统一处理：
  - `parse_memory_file_with_fields`；
  - detail abstract 选择；
  - chunk 生成；
  - base record 与 chunk record 的 Context 构造；
  - 提交方式。

这样 reindex 可以删除 memory L2 与 chunk 相关私有逻辑。

### 4. Skill 索引入口

把 `SkillProcessor` 中 skill L0/L1/L2 向量构造补齐为公共方法：

- `regenerate_existing_skill_semantics` 负责重新生成 `.abstract.md` / `.overview.md` / `SKILL.md`。
- 新增公共 skill vector rebuild 方法，复用 `build_skill_abstract`、skill meta 和 SKILL.md 内容读取规则。

这样 reindex 可以删除 `_skill_meta` 以及 skill L0/L1/L2 私有向量构造。

## 分阶段实施

1. Resource：把目录/文件列表输入能力补到 resource indexing service，删除 reindex 中 resource 向量循环。
2. Memory：抽公共 memory vector rebuild helper，删除 reindex 中 memory file/chunk Context 构造。
3. Skill：抽公共 skill vector rebuild helper，删除 reindex 中 skill Context 构造和 meta 拼装。
4. 最后清理 `ReindexExecutor`，只保留目标发现、模式校验、锁、任务状态和 counters。

## 验收标准

- `ReindexExecutor` 不再直接判断文件类型、embedding text source、memory chunk 细节或 skill abstract 结构。
- 新增公共入口不是薄 wrapper，每个入口都拥有明确领域策略。
- async enqueue 和 sync reindex 共享同一套 Context / EmbeddingMsg 构造路径。
- 现有 admin reindex、resource vectorize、memory compressor、skill add 流程测试全部通过。
