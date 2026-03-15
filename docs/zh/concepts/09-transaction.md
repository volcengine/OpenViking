# 事务机制

OpenViking 的事务机制保护核心写操作（`rm`、`mv`、`add_resource`、`session.commit`）的一致性，确保 VikingFS、VectorDB、QueueManager 三个子系统在故障时不会出现数据不一致。

## 设计哲学

OpenViking 是上下文数据库，FS 是源数据，VectorDB 是派生索引。索引丢了可从源数据重建，源数据丢失不可恢复。因此：

> **宁可搜不到，不要搜到坏结果。**

## 设计原则

1. **事务只覆盖同步部分**：FS + VectorDB 操作在事务内；SemanticQueue/EmbeddingQueue 的 enqueue 在事务提交后执行（post_actions），它们是幂等的，失败可重试
2. **默认生效**：所有数据操作命令自动开启事务机制，用户无需额外配置
3. **写互斥**：通过路径锁保证同一路径同一时间只有一个写事务
4. **Undo Log 模型**：变更前记录反向操作，失败时反序执行回滚
5. **事务日志持久化**：每个事务在 AGFS 中写入 journal 文件，支持崩溃恢复

## 架构

```
Service Layer (rm / mv / add_resource / session.commit)
    │
    ▼
┌──[TransactionContext 异步上下文管理器]──┐
│                                         │
│  1. 创建事务 + 写 journal               │
│  2. 获取路径锁（轮询 + 超时）           │
│  3. 执行操作（FS + VectorDB）           │
│  4. 记录 Undo Log（每步完成后标记）     │
│  5. Commit / Rollback                   │
│  6. 执行 post_actions（enqueue 等）     │
│  7. 释放锁 + 清理 journal               │
│                                         │
│  异常时：反序执行 Undo Log → 释放锁     │
└─────────────────────────────────────────┘
    │
    ▼
Storage Layer (VikingFS, VectorDB, QueueManager)
```

## 一致性问题与解决方案

### rm(uri)

| 问题 | 方案 |
|------|------|
| 先删文件再删索引 → 文件已删但索引残留 → 搜索返回不存在的文件 | **调换顺序**：先删索引再删文件。索引删除失败 → 文件和索引都在，搜索正常 |

事务流程：

```
1. 开始事务，加锁（lock_mode="subtree"）
2. 快照 VectorDB 中受影响的记录（用于回滚恢复）
3. 删除 VectorDB 索引 → 搜索立刻不可见
4. 删除 FS 文件
5. 提交 → 删锁 → 删 journal
```

回滚：第 4 步失败 → 从快照恢复 VectorDB 记录，文件和索引都在。

### mv(old_uri, new_uri)

| 问题 | 方案 |
|------|------|
| 文件移到新路径但索引指向旧路径 → 搜索返回旧路径（不存在） | 事务包装，移动失败则回滚 |

事务流程：

```
1. 开始事务，加锁（lock_mode="mv"，源路径 SUBTREE + 目标路径 POINT）
2. 移动 FS 文件
3. 更新 VectorDB 中的 URI
4. 提交 → 删锁 → 删 journal
```

回滚：第 3 步失败 → 把文件移回原位。

### add_resource (TreeBuilder.finalize_from_temp)

| 问题 | 方案 |
|------|------|
| 文件从临时目录移到正式目录后崩溃 → 文件存在但永远搜不到 | 事务包装 mv + post_action 保护 enqueue |

事务流程：

```
1. 开始事务，加锁（lock_mode="point"，锁 final_uri）
2. mv 临时目录 → 正式位置
3. 注册 post_action: enqueue SemanticQueue
4. 提交 → 执行 post_action → 删锁 → 删 journal
```

崩溃恢复：journal 中记录了 post_action，重启时自动重放 enqueue。

### session.commit()

| 问题 | 方案 |
|------|------|
| 消息已清空但 archive 未写入 → 对话数据丢失 | 拆为两段事务 + checkpoint |

LLM 调用耗时不可控（5s~60s+），放在事务内会长时间持锁。因此拆为：

```
第一段事务（归档）：
  1. 写 archive（history/archive_N/messages.jsonl + 摘要）
  2. 清空 messages.jsonl
  3. 写 checkpoint（status="archived"）
  4. 提交

LLM 调用（无事务）：
  从归档消息提取 memories

第二段事务（memory 写入）：
  1. 写 memory 文件
  2. 写 relations
  3. 更新 checkpoint（status="completed"）
  4. 注册 post_action: enqueue SemanticQueue
  5. 提交
```

崩溃恢复：读 checkpoint，根据 status 决定从哪一步继续。

## TransactionContext

`TransactionContext` 是**异步**上下文管理器，封装事务的完整生命周期：

```python
from openviking.storage.transaction import TransactionContext, get_transaction_manager

tx_manager = get_transaction_manager()

async with TransactionContext(tx_manager, "rm", [path], lock_mode="subtree") as tx:
    # 记录 undo（变更前调用）
    seq = tx.record_undo("vectordb_delete", {"record_ids": ids, "records_snapshot": snapshot})
    # 执行变更
    delete_from_vector_store(uris)
    # 标记完成
    tx.mark_completed(seq)

    # 注册提交后动作（可选）
    tx.add_post_action("enqueue_semantic", {"uri": uri, ...})

    # 提交
    await tx.commit()
# 未 commit 时自动回滚
```

**锁模式**：

| lock_mode | 用途 | 行为 |
|-----------|------|------|
| `point` | 写操作 | 锁定指定路径；与同路径的任何锁和祖先目录的 SUBTREE 锁冲突 |
| `subtree` | 删除操作 | 锁定子树根节点；与同路径的任何锁和后代目录的任何锁冲突 |
| `mv` | 移动操作 | 源路径加 SUBTREE 锁，目标路径加 POINT 锁 |

## 锁类型（POINT vs SUBTREE）

锁机制使用两种锁类型来处理不同的冲突场景：

| | 同路径 POINT | 同路径 SUBTREE | 后代 POINT | 祖先 SUBTREE |
|---|---|---|---|---|
| **POINT** | 冲突 | 冲突 | — | 冲突 |
| **SUBTREE** | 冲突 | 冲突 | 冲突 | — |

- **POINT (P)**：用于写操作和语义处理。只锁单个目录。若祖先目录持有 SUBTREE 锁则阻塞。
- **SUBTREE (S)**：用于删除和移动源操作。逻辑上覆盖整个子树，但只在根目录写**一个锁文件**。获取前扫描所有后代确认无冲突锁。

## Undo Log

每个事务维护一个 Undo Log，记录每步操作的反向动作：

| op_type | 正向操作 | 回滚动作 |
|---------|---------|---------|
| `fs_mv` | 移动文件 | 移回原位 |
| `fs_rm` | 删除文件 | 跳过（不可逆，设计上 rm 是最后一步） |
| `fs_write_new` | 创建新文件/目录 | 删除 |
| `fs_mkdir` | 创建目录 | 删除 |
| `vectordb_delete` | 删除索引记录 | 从快照恢复 |
| `vectordb_upsert` | 插入索引记录 | 删除 |
| `vectordb_update_uri` | 更新 URI | 恢复旧值 |

回滚规则：只回滚 `completed=True` 的条目，**反序执行**。每步独立 try-catch（best-effort）。崩溃恢复时使用 `recover_all=True`，也会回滚未完成的条目以清理部分操作残留。

## 锁机制

### 锁协议

锁文件路径：`{path}/.path.ovlock`

锁文件内容（Fencing Token）：
```
{transaction_id}:{time_ns}:{lock_type}
```

其中 `lock_type` 为 `P`（POINT）或 `S`（SUBTREE）。

### 获取锁流程（POINT 模式）

```
循环直到超时（轮询间隔：200ms）：
    1. 检查目标目录存在
    2. 检查目标路径是否被其他事务锁定
       - 陈旧锁？ → 移除后重试
       - 活跃锁？ → 等待
    3. 检查所有祖先目录是否有 SUBTREE 锁
       - 陈旧锁？ → 移除后重试
       - 活跃锁？ → 等待
    4. 写入 POINT (P) 锁文件
    5. TOCTOU 双重检查：重新扫描祖先目录的 SUBTREE 锁
       - 发现冲突：比较 (timestamp, tx_id)
       - 后到者（更大的 timestamp/tx_id）主动让步（删除自己的锁），防止活锁
       - 等待后重试
    6. 验证锁文件归属（fencing token 匹配）
    7. 成功

超时（默认 0 = 不等待）抛出 LockAcquisitionError
```

### 获取锁流程（SUBTREE 模式）

```
循环直到超时（轮询间隔：200ms）：
    1. 检查目标目录存在
    2. 检查目标路径是否被其他事务锁定
    3. 扫描所有后代目录，检查是否有其他事务持有的锁
    4. 写入 SUBTREE (S) 锁文件（只写一个文件，在根路径）
    5. TOCTOU 双重检查：重新扫描后代目录
       - 发现冲突：后到者主动让步（活锁防止）
    6. 验证锁文件归属
    7. 成功
```

### 锁过期清理

**陈旧锁检测**：PathLock 检查 fencing token 中的时间戳。超过 `lock_expire`（默认 300s）的锁被视为陈旧锁，在加锁过程中自动移除。

**事务超时**：TransactionManager 每 60 秒检查活跃事务，`updated_at` 超过事务超时时间（默认 3600s）的事务强制回滚。

## 事务日志（Journal）

每个事务在 AGFS 持久化一份 journal：

```
/local/_system/transactions/{tx_id}/journal.json
```

内容包含：事务 ID、状态、锁路径、init_info、undo_log、post_actions。

### 生命周期

```
创建事务 → 写 journal（INIT）
获取锁   → 更新 journal（AQUIRE → EXEC）
执行变更 → 每步更新 journal（标记 undo entry completed）
提交     → 更新 journal（COMMIT + post_actions）
         → 执行 post_actions → 删锁 → 删 journal
回滚     → 执行 undo log → 删锁 → 删 journal
```

## 崩溃恢复

`TransactionManager.start()` 启动时自动扫描残留 journal：

| 崩溃时 journal 状态 | 恢复方式 |
|---------------------|---------|
| `COMMIT` + post_actions 非空 | 重放 post_actions → 删锁 → 删 journal |
| `COMMIT` + post_actions 为空 / `RELEASED` | 删锁 → 删 journal |
| `EXEC` / `FAIL` / `RELEASING` | 执行 undo log 回滚（`recover_all=True`） → 删锁 → 删 journal |
| `INIT` / `AQUIRE` | 通过 init_info.lock_paths 清理孤儿锁 → 删 journal（变更未执行） |

### 防线总结

| 异常场景 | 防线 | 恢复时机 |
|---------|------|---------|
| 事务内崩溃 | journal + undo log 回滚 | 重启时 |
| 提交后 enqueue 前崩溃 | journal post_actions 重放 | 重启时 |
| enqueue 后 worker 处理前崩溃 | QueueFS SQLite 持久化 | worker 重启后自动拉取 |
| session.commit LLM 调用中崩溃 | checkpoint 文件恢复 | 重启时重新调用 LLM |
| 孤儿索引 | L2 按需加载时清理 | 用户访问时 |
| 加锁后 journal 更新前崩溃 | init_info 记录预期锁路径，恢复时检查并清理孤儿锁 | 重启时 |

## 事务状态机

```
INIT → AQUIRE → EXEC → COMMIT → RELEASING → RELEASED
                   ↓
                  FAIL → RELEASING → RELEASED
```

- `INIT`：事务已创建，等待锁获取
- `AQUIRE`：正在获取锁
- `EXEC`：事务操作执行中
- `COMMIT`：已提交，可能有 post_actions 待执行
- `FAIL`：执行失败，进入回滚
- `RELEASING`：正在释放锁
- `RELEASED`：锁已释放，事务结束

## 配置

事务机制默认启用，无需额外配置。**默认不等待**：若路径被锁定则立即抛出 `LockAcquisitionError`。如需允许等待重试，可通过 `storage.transaction` 段配置：

```json
{
  "storage": {
    "transaction": {
      "lock_timeout": 5.0,
      "lock_expire": 300.0,
      "max_parallel_locks": 8
    }
  }
}
```

| 参数 | 类型 | 说明 | 默认值 |
|------|------|------|--------|
| `lock_timeout` | float | 获取锁的等待超时（秒）。`0` = 立即失败（默认）；`> 0` = 最多等待此时间 | `0.0` |
| `lock_expire` | float | 锁过期时间（秒），超过此时间的事务锁将被视为陈旧锁并强制释放 | `300.0` |
| `max_parallel_locks` | int | rm/mv 操作的最大并行加锁数 | `8` |

### QueueFS 持久化

事务机制依赖 QueueFS 使用 SQLite 后端，确保 enqueue 的任务在进程重启后可恢复。这是默认配置，无需手动设置。

## 相关文档

- [架构概述](./01-architecture.md) - 系统整体架构
- [存储架构](./05-storage.md) - AGFS 和向量库
- [会话管理](./08-session.md) - 会话和记忆管理
- [配置](../guides/01-configuration.md) - 配置文件说明
