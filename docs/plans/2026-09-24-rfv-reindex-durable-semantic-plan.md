# RFV Reindex 接入持久化语义队列实施方案

> **For Claude:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task.

**目标：** 将 resource/skill 的 RFV reindex 产出的 `SemanticPlan` 直接持久化到既有 Semantic Queue，并沿用既有 Embedding Queue；不持久化原文，不引入 `ReembedAction`、`force_embed`、`ParentPropagation` 等重复抽象，同时保持与基线 reindex 一致的 freshness 行为。

**架构：** reindex 仍同步完成一次 R/F/V 快照、MD5/标量比较和 `ContextUpdatePlan` 编译；只要 plan 含语义 DAG，就将其作为 `SemanticMsg.plan` 写入 Semantic Queue，并把当前 reindex 路径锁交接给该消息。语义 worker 从 S3 读取 `GENERATE` 节点所需原文，复用 plan 中允许持久化的 abstract/overview/索引动作，继续通过既有 Embedding Queue 产出向量。`SemanticMsg.propagate_to_parent` 是 freshness 的唯一开关；它由 reindex 的 `recursive` 映射，不进入 `SemanticPlan`。

**技术栈：** Python、asyncio、VikingFS/S3、AGFS pathlock、QueueFS、SemanticTreeExecutor、Embedding Queue、VikingDB、pytest。

---

## 1. 已确认的设计边界

### 1.1 范围

- 第一阶段只覆盖 `resource` 与 `skill`；`memory` 保持 legacy reindex 路径。
- 对外 API 不变：`recursive=true` 默认保留，`force=true` 表示跳过 MD5 比较并重新处理所有目标；不恢复 `dry_run` 或 `prune_orphans`。
- `vectors_only` 与 `semantic_and_vectors` 都使用 RFV 和同一份 `ContextUpdatePlan`；前者只消费 `DirectIndexAction`，后者可产生 `SemanticPlan`。
- 模型迁移、旧消息与新消息并存时的 generation/epoch fencing 暂不纳入本次改造；本方案的前提是 reindex target 已被覆盖范围匹配的 pathlock 独占。

### 1.2 RFV 语义

RFV 表示 **R**equest、当前 **F**ormal tree、当前 **V**ector state。reindex 不应伪造 parser 生成的 N：它做的是“让 F 与 V 收敛”，而不是“再次导入一份变更内容”。

- F snapshot：文件树、目录 sidecar、L0/L1/L2 内容 MD5、完整性。目录 L0/L1 的 MD5 是 sidecar 可见正文指纹；文件 L2 的 MD5 是原始 bytes 指纹。
- V snapshot：目标 URI 范围内的现有向量记录与受 planner 比较所需字段。
- `force=false`：当内容 MD5 与向量状态都完整、且没有标量更新时，节点为 no-op，不做文件读取之外的模型/向量工作。
- `force=true`：比较阶段将目标视为 stale，不能被“MD5 相同”短路；所有目标重新走相应的 semantic/vector 动作。
- orphan 删除只在 formal snapshot 完整时发生；当前 RFV snapshot 已对拒绝访问等不完整树 fail-closed。

### 1.3 不增加额外文件 I/O

RFV producer 会为本次规划读取每个必需 source 一次，并把 bytes/sidecar body 暂存于进程内 `RFVSnapshot.source_contents`：

- 当前进程内直跑实现可用它避免 semantic executor 再读一次。
- **持久化边界不得保存它**。持久化的 `SemanticMsg.plan` 只带 `SemanticPlan`，不带 `source_contents`、`source_raw_contents`、文件 bytes、原文、parser artifact 或本地路径。
- 计划执行发生在后续 worker，`SemanticAction.GENERATE` 文件从 S3 读取当前正文；`REUSE`/目录聚合只使用 plan 中允许保存的 abstract、overview、record id、MD5、字段 patch 等派生数据。
- 因此“单次读取”只承诺 producer 的 RFV 规划阶段；跨 durable queue 的消费者读取是必要成本，不能通过持久化原文规避。

### 1.4 semantic closure 的正确性

`semantic_and_vectors` 的 planner 必须先形成完整 closure。若 retained 文件的 L2 MD5 完整，但某个父目录聚合需要其 abstract、而又不存在可复用 abstract，则必须将该文件提升为 `SemanticAction.GENERATE`，并把祖先提升为 `AGGREGATE`。

这条规则同时适用于 `add_resources` 和 reindex；否则“先 `vectors_only` 导入，再首次 `semantic_and_vectors` reindex”会因缺少 abstract 而失败。当前 shared planner 已具备该 promotion 逻辑，持久化改造不能绕开它。

## 2. 目标数据流

```text
HTTP / CLI reindex(uri, mode, recursive, force)
  │
  ├─ 获取 exact/tree pathlock
  ├─ 并发读取 F inventory 与 V inventory
  ├─ 有界并发读取本次比较必需的 source，编译 ContextUpdatePlan
  │
  ├─ DirectIndexAction
  │    └─ 直接写入既有 Embedding Queue（vectors_only 或无需 VLM 的动作）
  │
  └─ SemanticPlan（仅 semantic_and_vectors）
       ├─ 转成 SemanticMsg(plan=..., lock_handoff=...)
       ├─ register_semantic_root(telemetry_id, msg.id)
       ├─ pathlock_handoff(lease)，再 enqueue Semantic Queue
       └─ producer 不再 release 已交接的 lease

Semantic Queue worker
  ├─ adopt lock_handoff
  ├─ 从 S3 读取 GENERATE 文件；执行 REUSE / GENERATE / AGGREGATE DAG
  ├─ 通过既有 Embedding Queue 发送 L0/L1/L2 向量动作
  ├─ 根据 msg.propagate_to_parent 走既有 freshness
  ├─ wait_for_embeddings(telemetry_id)
  └─ ACK 后释放 lease
```

这里没有“reindex root queue”或新的持久化队列类型：持久化单位就是已经存在的 `SemanticMsg`，其 payload 是已经确定的 plan。

## 3. 消息与状态的归属

### 3.1 `SemanticPlan` 只描述 DAG

`SemanticPlan` 应只回答“本次处理哪些节点、每个节点的 semantic action 与 index slot 是什么”。它可以持久化：

- `root_uri`、`context_type`、树节点与相对路径；
- `REUSE` / `GENERATE` / `AGGREGATE`；
- 已有 abstract/overview、MD5、record id、`IndexSlot`、`FieldPatch`、`IngestOptions`、skill source metadata。

它不能持久化：

- 文件正文、原始 bytes、`source_contents`、`source_raw_contents`；
- parser artifact、临时文件、本地绝对路径；
- pathlock lease、请求 telemetry 生命周期等队列执行状态。

### 3.2 `SemanticMsg` 承担执行上下文

以下字段已经存在，应复用而不是新建 plan 内字段：

| 语义 | 归属 | 来源 |
| --- | --- | --- |
| 是否递归扫描/规划 | reindex request / RFV builder | `recursive` |
| 是否强制重新处理 | reindex request / `RequestIntent` | `force` |
| plan 执行后的 freshness 开关 | `SemanticMsg.propagate_to_parent` | `recursive` |
| 锁所有权转移 | `SemanticMsg.lock_handoff` | 当前 reindex lease |
| wait/统计关联 | `SemanticMsg.telemetry_id` | 当前 request telemetry |
| worker 的身份与 ACL | `SemanticMsg` | owner `RequestContext` |

因此删除 `ParentPropagation`：它与现有 `SemanticMsg.propagate_to_parent` 表达相同含义，会造成两处状态不一致。持久化 plan 分支也不能再读取 `msg.plan.propagation.enabled`；统一由 `_enqueue_parent_refresh()` 内已有的 `msg.propagate_to_parent` 判断。

## 4. Freshness 语义

### 4.1 与基线保持一致

legacy reindex 已经以：

```python
SemanticMsg(
    generation_trigger="reindex",
    propagate_to_parent=recursive,
)
```

进入 `SemanticProcessor`。持久化 plan 应保持同样的设置：

```python
SemanticMsg(
    uri=plan.root_uri,
    context_type=context_type,
    recursive=recursive,
    generation_trigger="reindex",
    propagate_to_parent=recursive,
    plan=plan,
    ...
)
```

plan DAG 完成后允许走 `_enqueue_parent_refresh()`；由 freshness counter、`freshness_refresh_ratio`、L0 body 是否实际变化决定是否仅标记 pending 或入队刷新。`force` 不改变 freshness 规则。

### 4.2 需要澄清并用测试锁定的现状

`_enqueue_parent_refresh()` 本身只从当前完成 URI 计算 **直接父目录**。但它创建的 `parent_refresh` 消息目前没有显式传 `propagate_to_parent=False`，而该字段默认是 `True`。所以当父级刷新真的入队并完成时，代码层面仍可能继续尝试它自己的父级 freshness。

这与“reindex 后只判断 root 的父目录一次”是不同的策略：

- **基线兼容策略（本方案默认）**：保留现状；reindex root 和普通 parent refresh 都按既有消息默认值运行。这样 RFV durable path 与 legacy 一致。
- **单跳策略（如果产品要求只检查 root 的父级）**：在 `_enqueue_parent_refresh()` 构造 `parent_msg` 时明确写 `propagate_to_parent=False`。这会同时改变 legacy、add_resources 与 RFV plan 的上行行为，不能作为 reindex-only 隐式改动。

本次以“基线一致”为准，不改变该默认传播链；但必须补测试，以免后续有人误以为现状天然是单跳。

## 5. 锁、可靠性与等待语义

### 5.1 锁交接

reindex 当前在同步执行期间持有 root 的 exact/tree lease，并在 finally 释放。改为 durable plan 后，不能让 producer 入队后立即释放锁：这样后续写入可能在 plan 的 VLM/embedding 未完成时进入，同一记录可能被旧任务覆盖。

正确顺序：

1. producer 从 live lease 调 `pathlock_to_handoff()` 得到可序列化 handoff；
2. 成功 `pathlock_handoff(lease)` 后，将 handoff 写入 `SemanticMsg.lock_handoff` 并 enqueue；
3. enqueue 失败时 adopt 回 lease，再由 producer finally 正常 release；
4. enqueue 成功时把 `run.lock`/`lease` 标记为已交接，外层 finally 不可二次释放；
5. worker 通过 `SemanticLockScope.resolve()` adopt 或按既有 fallback 覆盖范围重获锁；
6. worker 在本 plan 派生的 Embedding Queue 工作完成后才 ACK/释放锁。失败重试必须把同一 owned lease 再 handoff 给 retry；取消/死信必须释放 handoff。

`SemanticLockScope`、`SemanticMsg.lock_handoff`、skill retry 的 handoff 模式已经存在，可复用；不能创建第二套 reindex lock 协议。

### 5.2 等待与统计

reindex 的 request telemetry 在入队前先 `register_request()`，在 SemanticMsg 成功入队前 `register_semantic_root()`；Embedding 生产者继续注册 embedding root。同步 `wait=true` 仍使用 `wait_for_request(telemetry_id)`，因此等待包含 Semantic Queue 和其派生的 Embedding Queue。

`SemanticMessageWork` 的 resource 分支当前完成时只 close lock，skill 分支才等待 embeddings。为了兑现上面的锁边界，需将“plan message 且持有 handoff”也纳入等待 embeddings 的条件；不要粗暴改变所有 resource semantic 消息的完成时序。

## 6. 实施任务

### Task 1：删除重复的 propagation plan 状态

**文件：**

- 修改：`openviking/storage/context_update_plan.py`
- 修改：`openviking/storage/queuefs/semantic_processor.py`
- 修改：`tests/storage/test_context_update_plan.py`
- 修改或新增：`tests/storage/test_semantic_processor_*`

**步骤：**

1. 为 `SemanticPlan.to_dict()/from_dict()` 添加兼容性测试：旧持久化 payload 即使含 `propagation` 也能反序列化，新序列化不再写该字段。
2. 删除 `ParentPropagation`、`SemanticPlan.propagation`、其导出与新的读写逻辑；`from_dict()` 忽略未知 legacy `propagation` 字段。
3. 把 plan 消费路径的 freshness 条件改为 `not executor.stale`，后续统一交给 `_enqueue_parent_refresh()` 使用 `msg.propagate_to_parent` 判断。
4. 添加 plan 消息测试：`propagate_to_parent=False` 不调用 freshness，`True` 调用一次 planning helper。
5. 运行：`pytest tests/storage/test_context_update_plan.py tests/storage/test_semantic_processor_*.py -q`。

### Task 2：提供 reindex plan 的 durable enqueue helper

**文件：**

- 修改：`openviking/service/reindex_executor.py`
- 必要时修改：`openviking/utils/summarizer.py`（仅抽取通用 enqueue/handoff helper；不要让 reindex 伪装为 import）
- 测试：`tests/server/test_admin_rebuild_api.py`
- 新增或修改：`tests/service/test_reindex_*`

**步骤：**

1. 先写失败测试：semantic plan 存在时，reindex enqueue 的 `SemanticMsg` 包含 root URI、owner identity、`generation_trigger="reindex"`、`recursive`、`propagate_to_parent=recursive`、plan 和 telemetry id。
2. 实现私有 helper（例如 `_enqueue_reindex_semantic_plan`）：构建 `SemanticMsg`、预注册 semantic root、获取 handoff、原子地 handoff + enqueue，并在 enqueue 失败时恢复计数/锁所有权。
3. 在 `_reindex_rfv()` 中替换同步 `SemanticTreeExecutor` 分支；保留 `vectors_only` 的 direct index 分支和 file refresh fallback。
4. 将 run context 扩展为显式的“lease 已交接”状态，确保最外层 finally 只释放 producer 仍拥有的 lease。不要依赖 `lock is None` 推断，因为 borrowed lock 与 handoff 的所有权不同。
5. 运行 focused reindex tests。

### Task 3：让计划队列消费者在正确时机释放锁

**文件：**

- 修改：`openviking/storage/queuefs/semantic_work.py`
- 必要时修改：`openviking/storage/queuefs/semantic_processor.py`
- 测试：`tests/storage/test_semantic_processor_lock_ownership.py`
- 新增：`tests/storage/test_reindex_semantic_plan_queue.py`

**步骤：**

1. 写失败测试：资源 reindex plan worker adopt handoff 后，embedding 未完成时锁不释放；embedding 完成后才释放。
2. 将此等待限定为“持久化 plan 且本消息拥有 handoff lock”或等价的最小条件，调用现有 `RequestWaitTracker.wait_for_embeddings()`；普通 resource semantic 消息保持原行为。
3. 覆盖成功、VLM/DAG 失败重试、取消、embedding enqueue 失败、worker 被取消五个路径；每条路径都验证 lease 最终恰好释放一次或转移给 retry。
4. 验证 worker fallback reacquire 仍使用 handoff 记录的 exact/tree 覆盖范围，不能把 file target 错误升级为 tree lock。
5. 运行相关 storage tests。

### Task 4：保证消费者不依赖 producer 原文

**文件：**

- 修改：`openviking/service/reindex_executor.py`
- 修改：`openviking/storage/queuefs/semantic_executor.py`（只补确有缺口的 S3 读取逻辑）
- 测试：`tests/storage/test_resource_rfv.py`
- 测试：`tests/storage/test_semantic_executor_incremental.py`

**步骤：**

1. 写失败测试：队列持久化 JSON 中不出现文件正文、`source_contents`、`source_raw_contents`、原始字节或临时路径。
2. 用只含 `SemanticPlan` 的 `SemanticMsg` 驱动 worker；对 `GENERATE` 文件断言通过 VikingFS/S3 读取正文，对 `REUSE` 节点断言不读文件正文。
3. 测试 closure promotion：无 abstract 的 retained file 被提升为 `GENERATE` 后，worker 能从 S3 成功生成并让目录聚合完成。
4. 测试 skill `SKILL.md` 以及 L0/L1 复用路径，确保保留必要 metadata、但不泄漏正文到消息。
5. 运行 RFV、semantic executor focused tests。

### Task 5：freshness 与 recursive 的回归测试

**文件：**

- 修改：`openviking/storage/queuefs/semantic_processor.py`（只在测试暴露的缺口确有必要时）
- 测试：`tests/storage/test_semantic_processor_*`
- 测试：`tests/server/test_admin_rebuild_api.py`

**步骤：**

1. 对 legacy reindex 与 RFV plan reindex 分别断言：`recursive=true` 均允许 root 完成后的 freshness decision，`recursive=false` 均跳过。
2. 使用已有 freshness metadata 覆盖 `MARK_PENDING` 和 `REFRESH_NOW`；断言 `l0_body_changed=False` 时不入队。
3. 记录现状测试：parent refresh 默认是否继续传播，避免把“直接父级判定”误认为“整个链条只一跳”。
4. 若产品后续确认单跳策略，再单独提交：`parent_msg.propagate_to_parent=False`，并同时更新 legacy/add_resources/RFV 所有行为测试和文档；本提交不混入该策略变化。

### Task 6：端到端验证、指标和文档

**文件：**

- 修改：`docs/zh-cn/...` 中现有 reindex API 文档（按仓库实际路径定位）
- 修改：相关开发者设计文档或本方案的状态章节
- 测试：HTTP E2E 脚本/现有 integration tests

**步骤：**

1. 以 S3 文件系统、本地向量库、现有 `ov.conf` 模型，按 HTTP 跑：
   - `add_resources(processing_mode=vectors_only)`；
   - `reindex(semantic_and_vectors, force=false)`；
   - 相同输入再跑一次普通 reindex；
   - `force=true` 完整重建。
2. 验证每次 HTTP 返回的 `wait=true` 在 semantic 与 embedding 都 settled 后才完成；检查 Semantic/Embedding Queue 的持久化 payload 不含原文。
3. 比较 legacy baseline 与新路径：树节点数、L0/L1/L2 vector 数、核心检索命中、freshness 事件、重复 reindex 的 rebuilt/enqueued 数。
4. 记录拆分耗时：RFV scan/plan、Semantic Queue wait/execute、Embedding Queue wait/execute；不得只比较 HTTP 总耗时。
5. 运行：
   - `pytest tests/storage/test_resource_rfv.py tests/storage/test_context_update_plan.py tests/storage/test_semantic_executor_incremental.py -q`
   - `pytest tests/server/test_admin_rebuild_api.py tests/storage/test_semantic_processor_lock_ownership.py -q`
   - 受影响的 lint/format/type checks。

## 7. 验收标准

1. resource/skill 的 `semantic_and_vectors` reindex 产出的 `SemanticPlan` 可在重启后由既有 Semantic Queue 恢复、执行并完成。
2. QueueFS 的 plan payload 不包含任何原文或 source bytes；`GENERATE` 消费时从 S3 读取。
3. `force=false` 的 MD5/标量 no-op 不入 VLM 或 Embedding Queue；`force=true` 不受 MD5 skip 影响。
4. `vectors_only` 不进入 Semantic Queue；`semantic_and_vectors` 的 direct index actions 和 semantic plan 都沿用既有 Embedding Queue。
5. `recursive` 同时决定扫描范围与 `SemanticMsg.propagate_to_parent`，持久化路径与 legacy reindex 的 freshness 开关一致。
6. `ParentPropagation` 不再存在；freshness 开关只有 `SemanticMsg.propagate_to_parent` 一个来源。
7. reindex 交接后 pathlock 覆盖从 plan enqueue 延续到该 plan 派生 embeddings settled；成功、失败、重试、取消路径都没有泄漏或双重释放。
8. 首次从 `vectors_only` 转入 `semantic_and_vectors` 不会因缺 abstract 失败，closure promotion 能生成必需输入。

## 8. 不做的事情

- 不持久化 snapshot 原文来换取跨队列零读取。
- 不引入 reindex 专属队列、`ReembedAction`、`force_embed`、`RebuildPolicy` 或 plan 内 propagation 对象。
- 不把 memory reindex 迁移到 RFV。
- 不在本方案中实现模型迁移 epoch/fencing；如未来允许同一范围并发、旧 embedding 消息与 force reindex 竞争，再独立设计 record generation。
- 不借此次持久化改造修改 parent freshness 的全局传播策略；是否单跳是独立产品语义决策。
