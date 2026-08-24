# OpenViking 任务机制分层重构设计

| 项目 | 信息 |
| --- | --- |
| 状态 | 本地形态已实现，分布式 Store / Queue Backend 待实现 |
| 目标版本 | v1 |
| 更新日期 | 2026-08-24 |
| 关联模块 | `TaskTracker`、`TaskWorkStore`、`QueueMiddleware`、`QueueManager` / `NamedQueue` |

## 1. 结论先行

**核心不是"上分布式",而是先做合理的业务分层与抽象；分层做对之后，分布式改造收敛为替换最底层的 Task/Work Store、Queue Backend 与缓存/一致性策略，上层业务逻辑和 Middleware 契约不动。** 当前代码难以改造的根因是分层混乱：完成度门控、请求响应聚合、内存缓存、持久化、asyncio 句柄被糊在几个单例里。

**系统切成四层，依赖单向向下，把"脏逻辑"关进特定领域。** L1 领域模型（纯数据）、L2 TaskTracker（唯一业务入口）、L2.5 Middleware+ACL（防腐层）、L3 TaskWorkStore（持久化 + 并发 + 缓存）、L4 队列（通用传输 + middleware）。本地 JSON 序列化、mkdir、路径布局这类逻辑只允许出现在 L3 的 `PersistentTaskStore` 里。

**允许中间态不可用，方向单调收敛。** 不被"每一步都能跑"绑架——补丁式改造正是代码走到今天这么乱的原因。

**两条硬约束不可破：数据兼容 + API 返回兼容。** 已持久化的 TaskRecord JSON、队列消息线协议、QueueFS 磁盘消息必须继续可读（§10）；对前端的 `/tasks` 等接口返回**逐字段不变**，内部新增字段（`version`、`works` 等）绝不能外泄（§9）。

## 2. 背景与问题

### 2.1 两套并行机制，职责混叠

`TaskTracker`（task 生命周期，落 `PersistentTaskStore`）与 `RequestWaitTracker`（`telemetry_id` 维度、内存统计 Semantic/Embedding 完成度）并行。后者内部混了**完成度门控**（pending roots → wait）与**按队列聚合计数**（queue_status 响应体）两件事，前者与 `TaskTracker.wait_for_descendants` 同构，是"难以直接改"的根源。

### 2.2 work 状态未持久化，所有权错位

`TaskWorkIndex._work` / `_active` / `_failures` 全是进程内存态，启动时从 QueueFS 未 ACK 消息 rebuild。`QueueManager` 自己 `new TaskWorkIndex()` 注入 `NamedQueue`，再由 tracker 反向 `attach`——work 状态逻辑上属 task 域却被队列层持有。**work 状态无历史持久化数据要兼容**，重构布局有完全自由。

### 2.3 enqueue 契约不统一

`NamedQueue.enqueue(dict)` 硬编码 `prepare_task_payload`；`SemanticQueue.enqueue(SemanticMsg)` 覆盖签名做 coalesce 再 `to_dict()` 下沉，导致 semantic/embedding 因 `to_dict()` 不含 `task_id`/`_task_work_id` 而漏出 task 跟踪。问题在 enqueue 契约本身——coalesce、序列化、work 盖章都塞进了队列。

## 3. 目标分层

四层，依赖严格单向向下。

### 3.1 L1 领域模型（纯数据，零 IO）— 已落地

`openviking/service/task_domain.py`：

- `TaskRecord`：状态 + `version`（乐观并发 token，只承载不自增）。
- `WorkRecord`：work_id / task_id / queue_name / state / requeue_count / error。状态机为 `pending / in_progress / requeued / done / failed`，不含独立取消态（取消在 task 层表达，见 §4.2）。
- `TaskAggregate`：task + works，**纯函数**状态迁移；`queue_status()` 把 Semantic/Embedding 的 processed/requeue/error **投影**出来（RequestWaitTracker 的 stats 被吸收为只读投影）。work 完成不删、标 `done` 保留到终态/TTL；`has_open_work` 判"存在非终态 work"。

### 3.2 L2 TaskTracker（唯一业务入口）

所有调用方只跟它打交道：生命周期、work 操作、投影、启动重建、取消拉循环。持有 `_active`（asyncio 句柄内存 side-table，**永不持久化、不进 TaskRecord**）。持久化、缓存与 `_active` 统一以 `(account_id, user_id, task_id)` 三元组为键，相同 task_id 在不同 owner 下互不干扰；`_active` 每个句柄额外记录“是否已发过用户取消信号”，保证多条取消路径（API cancel、拉循环、迟到注册）对同一 coroutine 只投递一次 `cancel()`。只依赖 L3 的 `TaskWorkStore` 接口，不知道存储介质。

### 3.3 L2.5 Middleware + ACL（防腐层）

task 域实现 `TaskWorkQueueMiddleware` 注册进队列：enqueue 阶段盖章并登记 Work，process 阶段负责终态去重、active handle、取消分类和 Work 结算，ACK 阶段只透传 transport 删除。Semantic 的 coalesce/dedupe 保留在 `SemanticQueue` 外层，避免 deduplicated 消息产生虚假 Work。Queue 消息使用 `task_id + _task_work_id + _task_account_id + _task_user_id`，内部 owner 与消息自身业务 owner 严格分离。

### 3.4 L3 TaskWorkStore（仓储：持久化 + 并发 + 缓存）— 接口已定

`openviking/service/task_store.py`。接口用领域语言，**所有脏逻辑关在实现里**。当前实现：`PersistentTaskStore`（JSON blob + version + 路径 + 读旧格式）、`CachingTaskWorkStore`（仅本地形态使用的缓存装饰器）；后续增加 `MySQLTaskWorkStore`（行级 CAS + 权威读）。TaskTracker 只调用 Store 的正式 `cleanup()` 和 `snapshot_task_stats()`，不读取缓存聚合、不调用 `invalidate()`，也不通过 `getattr` 探测缓存能力。

### 3.5 L4 队列（通用传输 + middleware 注册）

`NamedQueue` 独占消息的 enqueue、dequeue、handler、retry、计数和 ACK 生命周期；`QueueManager` 只负责 worker 线程与 `consume_one()` 并发调度。`QueueMiddleware` 用同一个接口包裹 enqueue/process/ack 三个操作，L4 不 import TaskTracker，也不知道 task 字段名。

## 4. 关键机制

### 4.1 并发控制：version 藏在对象里，CAS 封在底层

接口沿用 `update(task) -> bool`，不暴露 `expected_version` 参数。version 作为 `TaskRecord` 不变量随对象携带。FileStore 无法提供真实 CAS，因此无条件覆盖并返回 True，本地并发由 L2 owner-loop/task lock 保证；MySQLStore 使用 `WHERE version=?`，冲突返回 False。L2 对 task 状态迁移统一执行“重新加载 aggregate → 重放纯迁移 → 有界 CAS 重试”；finalize 冲突后若看到新增 work，会放弃终态化。

**work 不需要 version。** `mark_work_done` / `mark_work_failed` / `mark_work_requeued` 均可压成单条原子语句（条件更新 / 原子自增 / upsert），不依赖读整行；requeue 的终态迁移与计数合并进 `mark_work_requeued(delta)` 一步完成。

### 4.2 取消：只在 task 层表达，work 靠拉 + 本地推

**取消不推给 work，也不需要 work 有取消态。** 发起节点只通过 `update(task)` 把状态改为 `CANCELLING`，不碰任何 work、不管谁在跑。执行 work 的节点靠读 task 状态响应：

- `is_cancelling(task_id, owner)`：热路径查本地取消视图，process middleware 在跑 handler 前用它短路；enqueue middleware 通过原子的 `add_work` 拒绝终态/cancelling task。
- `list_cancelling_tasks()`：返回 owner-scoped 三元键全集，L2 的**本节点取消拉循环**周期性拉取，对 `cancelling ∩ 本机 _active` 中尚未收到取消信号的句柄主动 `cancel()`，用"拉 + 本地 cancel"实现推的效果。

**存储只回答"哪些 task 在 cancelling"，不持有句柄、不认识 asyncio。** 取消即时性 = 同节点即时（本机 `_active` 命中直接 cancel）+ 跨节点检查点级延迟（拉循环周期 + work 主动查点）。跨节点即时抢占本质做不到，这是协作式取消的固有权衡。

### 4.3 add_work 拒绝不变量

终态 **或 cancelling** 的 task 必须拒绝 `add_work`，且"检查与插入原子"（单机整体写；分布式条件插入）。配合 finalize 的 version CAS，两条合起来堵死"边取消/终态边加 work"的竞态。复用/扩展现有 `TaskWorkRejected`。

## 5. QueueManager ↔ TaskTracker 交互

队列是通用传输设施，提供 middleware 能力；TaskTracker 是注册方。依赖方向：队列依赖 `QueueMiddleware` 接口，task 域实现 middleware 并注册进队列。

**process 时序不变量**：Handler 结果与 retry replacement 先完成，再由 TaskWork middleware 持久化旧 Work 终态，最后 Queue 执行 ACK。ACK 失败不回滚 Work；消息重投时通过终态 work_id 跳过 Handler，只补做 ACK。

启动重建由 L2.5 适配器完成：`snapshots = qm.snapshot_all()` → 仅恢复 missing/open Work → `qm.register_middleware(TaskWorkQueueMiddleware)`。Bootstrap 在这段流程完成前不启动 worker；终态 Work 不因 stale 消息重开。

## 6. enqueue 契约收敛

```text
NamedQueue.enqueue(payload: dict | str) -> msg_id
    ensure_dir
    middleware.enqueue(ctx, next)  # 洋葱进入/退出，可改写、拒绝和补偿
        QueueFS.write(payload)
```

Semantic coalesce/dedupe → `SemanticQueue.enqueue()`；work 盖章 + 拒绝 → `TaskWorkQueueMiddleware.enqueue()`；typed msg 序列化 → 进 enqueue 前完成。retry enqueue 强制生成新的 work_id。

## 7. 命名约定

store 读方法用**领域词**，不用架构词（不叫 `load_aggregate`）：

- `get(task_id, account_id?, user_id?)` → 返回 `TaskAggregate`（task 连同 works）。完整 owner 时允许读取持久化；owner 不完整时，只有本地缓存装饰器可返回进程内已知任务，底层持久化不得做无作用域查询。
- `list(account_id?, user_id?)` → 完整 owner 时读取该 owner；owner 不完整时仅返回本地缓存匹配项。
- `list_all()` → 权威全租户读取，仅供 ROOT 管理接口使用；FileStore 扫描全部 owner，分布式 Store 由数据库查询实现。
- 写：沿用 `create / update / delete`，新增 `add_work / start_work / mark_work_done / mark_work_failed / mark_work_requeued / restore_work` 等 work 动词。

`TaskAggregate` 类型名保留（内部数据结构），但方法名不带 aggregate。

## 8. TaskWorkStore 接口（已定）

```text
# 读
get(task_id, account_id?, user_id?) -> TaskAggregate?
list(account_id?, user_id?) -> [TaskAggregate]
list_all() -> [TaskAggregate]

# task 写(乐观并发, version 藏对象内)
create(task)
create_if_no_active(task) -> bool       # 同 owner/type/resource 的 active 业务唯一键
update(task) -> bool
delete(task_id, account_id, user_id)

# task 取消状态读
list_cancelling_tasks() -> set[(account_id, user_id, task_id)]  # L2 拉循环用
is_cancelling(task_id, account_id, user_id) -> bool             # worker 热路径本地视图

# work 写(意图化动词, 单条原子, 无 version)
add_work(work) -> bool                  # task 不存在返回 False；终态/cancelling 拒绝；检查与插入原子
restore_work(work)                      # 仅恢复 missing/open Work；终态 Work 保留不重开
get_work_state(task_id, work_id) -> WorkState?   # process middleware 去重用
mark_work_done(task_id, work_id)
mark_work_failed(task_id, work_id, error?)
mark_work_requeued(task_id, work_id, delta=1)    # 终态迁移 + requeue 计数合一
list_open_works(task_id) -> [WorkRecord]
clear_works(task_id)
```

## 9. API 返回兼容红线（不可破坏）

**现状**：`/tasks` 等接口直接把 `TaskRecord.to_dict()` 返回前端，而 `to_dict()` 用 `asdict(self)` **全字段导出**再 pop 掉 account_id/user_id。这意味着**只要 `TaskRecord` 新增字段（如 `version`），`asdict` 会自动把它带进 API 返回**——这是本次重构最大的隐性破坏点。

**红线规则：**

1. **对外序列化走白名单视图，禁止 `asdict(内部模型)` 直出。** 新增一个显式的"API 视图"函数，只列白名单字段；内部模型加多少字段都不外泄。
2. **`version` / `works` / `WorkRecord` 等内部字段禁止出现在任何对外返回。**
3. **对齐老 `to_dict()` 的字段集合**（逐字段）：`task_id`、`task_type`、`status`、`created_at`、`updated_at`、`created_at_iso`、`updated_at_iso`、`resource_id`、`meta`、`stage`、`result`、`error`；且不输出 `account_id`、`user_id`；`result`/`meta` 递归过滤 `user_key`。
4. **`queue_status` 响应结构不变**：由 `TaskAggregate.queue_status()` 投影出的 `{Semantic/Embedding: {processed, requeue_count, error_count, errors}}` 必须与老 `RequestWaitTracker.build_queue_status` 逐字段一致。

**守线手段**：接线阶段加回归测试，对比新旧 `/tasks` 与 `queue_status` 返回 JSON 完全一致。

**ROOT 与普通用户的查询边界**：普通用户始终使用鉴权 owner。ROOT 列表通过 `list_all()` 读取所有租户的权威任务集合；ROOT 单项查询保留历史兼容语义，先用 ownerless `get(task_id)` 查询当前进程缓存，再查询当前请求 owner 与保留的 system owner。ownerless 查询不得穿透到底层持久化。

## 10. 数据兼容守点

- **TaskRecord JSON**：`PersistentTaskStore` 读旧格式，缺 `works` / `version` 字段时分别读成空 works / version=0，由启动 rebuild 从 QueueFS 回填 work。无需数据迁移。
- **线协议**：新消息写 `task_id + _task_work_id + _task_account_id + _task_user_id`；内部 task owner 不覆盖 payload 的业务 owner。旧消息缺 work_id 时回退 `queuefs:<envelope-id>`，缺内部 owner 时回退旧 `account_id/user_id/user/context_data`。
- **QueueFS 磁盘消息**：队列后端不变，`snapshot_all()` + rebuild 保证升级期未 ACK 存量消息可重建。

## 11. 三条不可违反的边界

- **L4 队列不认识 task**：只有队列触发 `QueueMiddleware`，没有队列 → TaskTracker。
- **L2 TaskTracker 不认识存储介质 / JSON**：只依赖 `TaskWorkStore`；缓存是装饰器不是内部分支。
- **缓存生命周期属于 L3**：TTL、容量上限、缓存失效和缓存统计由 `CachingTaskWorkStore` 自己维护；L2 只触发通用 `cleanup()`，不拿缓存快照。
- **L1 谁都不依赖**：纯数据 + 纯函数；`PersistentTaskStore` 负责 ↔ JSON；`_active` 永不进 `TaskRecord`。

## 12. 推进顺序（允许中间态不可用）

1. **L1 领域模型 + `TaskWorkStore` 接口**（纯定义，不接线）— ✅ 已完成。
2. **L3 `PersistentTaskStore` + `CachingTaskWorkStore`**：✅ 已完成。File 仍为无 CAS 覆盖写，本地串行由 L2 保证；缓存只用于本地形态，TTL/容量清理和 stats snapshot 均由 Store 实现。
3. **L2 TaskTracker 重写**：✅ 已完成。只依赖 Store，work/stats/取消拉循环统一管理。
4. **L4 队列 middleware 化 + L2.5 middleware/ACL**：✅ 已完成。typed message 在调用方序列化，队列唯一入口为 payload。
5. **接线 + 移除 RequestWaitTracker**：✅ 已完成。同步写入口建立内部 task scope，对外返回不新增 task 字段；SessionCommit worker 显式传递并排除承载当前 handler 的 Work，见 §15。
6. **分布式 `MySQLTaskWorkStore` + 分布式 Queue Backend**：待实现。

第 3、4 步之间系统是碎的，不回头缝，直到第 5 步一次性接通。

## 13. 分布式收敛点

分层做对后，分布式落地 = **新增 `MySQLTaskWorkStore`(L3) + 替换 Queue Backend + 换 Bootstrap 装配**，L1/L2、QueueMiddleware 契约及业务调用方不动。L3 需解决：`update(task)` 行级 CAS、`add_work` 条件插入、取消视图/通知、work 状态幂等迁移；跨 DB/Queue 的一致性需使用 outbox/inbox 或可靠对账，不能把两个独立提交伪装成一个事务。ACK 仍遵守"先持久化 work/task 终态，再删除队列消息"。

### 13.1 Handler 结果与 ACK

Handler 不操作 Queue 状态，也不调用 success/error/requeue callback，而是返回显式 `ProcessResult`：

- `SUCCESS`：process middleware 落 Work `done`，Queue 结算 processed 并 ACK。
- `FAILED`：process middleware 先落 Work `failed`，再由 Queue ACK。ACK 表示消息已明确处置，不表示业务成功。
- `REQUEUE`：Queue 先持久化 replacement message（强制新 work_id），再把旧 Work 标记为 requeued 并 ACK 旧消息；`max_attempts=None` 为默认值，表示保留原有持续重投语义，只有 handler 显式设置有限 `max_attempts` 时，超过上限才转为 `FAILED`。
- `CANCELLED`：process middleware 确认持久化取消意图，Queue 经 `ctx.discard()` 调用 handler `on_discard` 清理成功后结算并 ACK。
- `DUPLICATE`：stale recovery 重投时若 Work 缺失且 task 已终态、或 Work 已处于终态，则跳过 Handler，仅补做 settle/ACK。

Handler 抛出的普通 `Exception` 由 Queue 转成 `FAILED`，避免消息停在 processing 等待重启。只有无法完成明确处置的 Queue/持久化异常、retry replacement 写入失败、ACK 失败、进程退出，以及未被业务取消确认的 `CancelledError` 才保持未 ACK，交给 QueueFS stale recovery。

用户取消必须先持久化 Task `CANCELLING`，再取消 active coroutine。TaskWork middleware 捕获 `CancelledError` 并确认取消意图；Queue 通过 context 提供的通用 discard 操作调用 handler `on_discard`。shutdown、来源未知、状态查询失败或 discard 失败时不 ACK。

## 14. Queue Middleware 洋葱模型

`QueueMiddleware` 的同一个协议支持 `enqueue/process/ack` 三个操作。每个操作都以 `context + call_next` 表达，不拆分 `before/after/on_error` 回调。

**统一规则：注册顺序就是从外到内；进入按注册顺序，退出与异常传播按逆序。**

```text
TracingMiddleware.before
  TaskWorkMiddleware.before
    RetryMiddleware.before
      QueueFS write / handler / ACK
    RetryMiddleware.after
  TaskWorkMiddleware.after
TracingMiddleware.after
```

三个操作：

- `enqueue(ctx, call_next)`：TaskWork 在外层登记 Work，内层完成队列持久化；未提交异常时逆序回滚 Work，retry 强制新 work_id。
- `process(ctx, call_next)`：TaskWork 处理终态重投、执行上下文、active handle、取消分类和 Work 终态持久化。
- `ack(ctx, call_next)`：transport 删除。TaskWork 默认透传；ACK 失败不回滚已终结 Work，重投后按 work_id 去重。

Middleware 与 Observer 分开：Task/Work 一致性、取消、上下文、重试和补偿属于 Middleware；日志、metrics、trace 等不改变控制流的通知属于 Observer。Semantic coalesce/dedupe 继续留在 `SemanticQueue` 的 middleware 链外，只有确认需要真实入队的消息才进入通用链，避免产生没有 QueueFS 消息的虚假 Work。Queue 启动后应冻结 middleware 顺序，避免同一运行期内不同消息使用不同语义。

## 15. RequestWaitTracker `register_request` 迁移审计

旧代码共有 8 个生产 `register_request()` 调用点。`register_request()` 本身不创建业务 Task，只在 enqueue 前建立一个 `telemetry_id` 等待槽；新机制必须分别由“上层创建 Task + 绑定 TaskContext”和“QueueMiddleware 在入队前登记 Work”替代，不能在每个内部执行器中重复创建 Task。

| 旧调用点 | 当前承接方式 | 结论 / 影响 |
| --- | --- | --- |
| `FSService.rm` | `content_remove` Task + `bind_task_context` + `wait_for_work` | 完整替代；`wait=true` 保持旧响应，Task 仍可列举。 |
| `ReindexExecutor._run` | `execute` 统一创建 `admin_reindex` Task，`_run_tracked` 绑定 context | 完整替代；QueueMiddleware 登记派生 embedding Work。 |
| `ResourceService.add_skill` | `add_skill` Task + task context；同步等 Work，异步后台监控 | 完整替代。 |
| `ContentWriteCoordinator.batch_write` | 上层 `FSService.batch_write` 创建 Task；coordinator 继承 context | 生产入口完整替代；coordinator 无 context 的直接调用仅保留全队列等待兼容。 |
| `ContentWriteCoordinator._write_direct_with_refresh` | 上层 `FSService.write` 或 session commit TaskContext | 完整替代；内部方法不应创建第二个 Task。 |
| `ContentWriteCoordinator._write_memory_with_refresh` | 上层 `FSService.write` 或父 TaskContext；`_wait_for_task_work` 按 context 等待 | 完整替代；等待不再由 telemetry_id 决定。 |
| `AddResourceProcessor._process` | enqueue 前创建 `add_resource` Task；dequeue 后 QueueMiddleware/processor 重建 context；等待 descendants 时排除当前 Work | 完整替代，避免 handler 等待自身 ACK。 |
| `Session._run_memory_extraction` | Phase 1 已创建 `session_commit` Task，worker 绑定同一 context，并显式传入 QueueFS envelope 的 current work_id | 已修复：调用 `wait_for_descendants(task_id, current_work_id)` 排除承载 handler 的 Work；旧消息或直接内部调用缺少 work_id 时兼容回退 `wait_for_work`。 |

旧的 `register_semantic_root/register_embedding_root` 均由 `TaskWorkQueueMiddleware.enqueue -> TaskTracker.register_work` 接管。Semantic typed message 经 `SemanticQueue.enqueue -> NamedQueue.enqueue`，Embedding typed message经 `VikingDBManager -> EmbeddingQueue/NamedQueue.enqueue`，没有绕过 Middleware。Semantic dedupe 在进入通用 Middleware 前完成，因此 deduplicated 消息不会留下虚假 Work。

## 16. 待拍板 / 开放问题

- **无 task 的写路径**：已在 FSService write/batch_write/rm、ResourceService add_skill、同步 reindex 等入口建立内部 task scope；task_id 不额外暴露到原有同步 API 返回。
- **done work 保留时长**（到终态 vs 额外 TTL）。
- **高频 settle 写放大**（File blob 整体重写 vs 分布式行 CAS）。
