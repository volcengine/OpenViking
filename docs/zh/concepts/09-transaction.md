# 路径锁与崩溃恢复

OpenViking 通过**路径锁**和**Redo Log** 两个简单原语保护核心写操作（`rm`、`mv`、`add_resource`、`session.commit`）的一致性，确保 VikingFS、VectorDB、QueueManager 三个子系统在故障时不会出现数据不一致。

## 设计哲学

OpenViking 是上下文数据库，FS 是源数据，VectorDB 是派生索引。索引丢了可从源数据重建，源数据丢失不可恢复。因此：

> **宁可搜不到，不要搜到坏结果。**

## 设计原则

1. **写互斥**：通过路径锁保证同一路径同一时间只有一个写操作
2. **默认生效**：所有数据操作命令自动加锁，用户无需额外配置
3. **锁即保护**：进入 LockContext 时加锁，退出时释放，没有 undo/journal/commit 语义
4. **仅 session_memory 需要崩溃恢复**：通过 RedoLog 在进程崩溃后重做记忆提取
5. **Queue 操作在锁外执行**：SemanticQueue/EmbeddingQueue 的 enqueue 是幂等的，失败可重试

## 架构

```
Service Layer (rm / mv / add_resource / session.commit)
    |
    v
+--[LockContext 异步上下文管理器]-------+
|                                       |
|  1. 创建 LockHandle                  |
|  2. 获取路径锁（轮询 + 超时）        |
|  3. 执行操作（FS + VectorDB）        |
|  4. 释放锁                           |
|                                       |
|  异常时：自动释放锁，异常原样传播    |
+---------------------------------------+
    |
    v
Storage Layer (VikingFS, VectorDB, QueueManager)
```

## 两个核心组件

### 组件 1：PathLock + LockManager + LockContext（路径锁系统）

**PathLock** 实现基于文件的分布式锁，支持 POINT 和 SUBTREE 两种锁类型，使用 fencing token 防止 TOCTOU 竞争，自动检测并清理过期锁。

**LockHandle** 是轻量的锁持有者令牌：

```python
@dataclass
class LockHandle:
    id: str          # 唯一标识，用于生成 fencing token
    locks: list[str] # 已获取的锁文件路径
    created_at: float # 创建时间
```

**LockManager** 是全局单例，管理锁生命周期：
- 创建/释放 LockHandle
- 后台清理泄漏的锁（进程内安全网）
- 启动时执行 RedoLog 恢复

**LockContext** 是异步上下文管理器，封装加锁/解锁生命周期：

```python
from openviking.storage.transaction import LockContext, get_lock_manager

async with LockContext(get_lock_manager(), [path], lock_mode="point") as handle:
    # 在锁保护下执行操作
    ...
# 退出时自动释放锁（包括异常情况）
```

### 组件 2：RedoLog（崩溃恢复）

仅用于 `session.commit` 的记忆提取阶段。操作前写标记，成功后删标记，启动时扫描遗留标记并重做。

```
/local/_system/redo/{task_id}/redo.json
```

Memory 提取是幂等的 — 从同一个 archive 重新提取会得到相同结果。

## 一致性问题与解决方案

### rm(uri)

| 问题 | 方案 |
|------|------|
| 先删文件再删索引 -> 文件已删但索引残留 -> 搜索返回不存在的文件 | **调换顺序**：先删索引再删文件。索引删除失败 -> 文件和索引都在，搜索正常 |

**加锁策略**（根据目标类型区分）：
- 删除**目录**：`lock_mode="subtree"`，锁目录自身
- 删除**文件**：`lock_mode="point"`，锁文件的父目录

操作流程：

```
1. 检查目标是目录还是文件，选择锁模式
2. 获取锁
3. 删除 VectorDB 索引 -> 搜索立刻不可见
4. 删除 FS 文件
5. 释放锁
```

VectorDB 删除失败 -> 直接抛异常，锁自动释放，文件和索引都在。FS 删除失败 -> VectorDB 已删但文件还在，重试即可。

### mv(old_uri, new_uri)

| 问题 | 方案 |
|------|------|
| 文件移到新路径但索引指向旧路径 -> 搜索返回旧路径（不存在） | 先 copy 再更新索引，失败时清理副本 |

**加锁策略**（通过 `lock_mode="mv"` 自动处理）：
- 移动**目录**：源路径和目标父目录各加 SUBTREE 锁
- 移动**文件**：源的父目录和目标父目录各加 POINT 锁

操作流程：

```
1. 检查源是目录还是文件，确定 src_is_dir
2. 获取 mv 锁（内部根据 src_is_dir 选择 SUBTREE 或 POINT）
3. Copy 到新位置（源还在，安全）
4. 如果是目录，删除副本中被 cp 带过去的锁文件
5. 更新 VectorDB 中的 URI
   - 失败 -> 清理副本，源和旧索引都在，一致状态
6. 删除源
7. 释放锁
```

### add_resource

| 问题 | 方案 |
|------|------|
| 文件从临时目录移到正式目录后崩溃 -> 文件存在但永远搜不到 | 首次添加与增量更新分离为两条独立路径 |
| 资源已落盘但语义处理/向量化还在跑时被 rm 删除 -> 处理白跑 | 生命周期 SUBTREE 锁，从落盘持续到处理完成 |

**首次添加**（target 不存在）— 在 `ResourceProcessor.process_resource` Phase 3.5 中处理：

```
1. 获取 POINT 锁，锁 final_uri 的父目录
2. agfs.mv 临时目录 -> 正式位置
3. 获取 SUBTREE 锁，锁 final_uri（在 POINT 锁内，消除竞态窗口）
4. 释放 POINT 锁
5. 清理临时目录
6. 入队 SemanticMsg(lifecycle_lock_handle_id=...) -> DAG 在 final 上跑
7. DAG 启动锁刷新循环（每 lock_expire/2 秒刷新时间戳）
8. DAG 完成 + 所有 embedding 完成 -> 释放 SUBTREE 锁
```

此期间 `rm` 尝试获取同路径 SUBTREE 锁会失败，抛出 `ResourceBusyError`。

**增量更新**（target 已存在）— temp 保持不动：

```
1. 获取 SUBTREE 锁，锁 target_uri（保护已有资源）
2. 入队 SemanticMsg(uri=temp, target_uri=final, lifecycle_lock_handle_id=...)
3. DAG 在 temp 上跑，启动锁刷新循环
4. DAG 完成后触发 sync_diff_callback 或 move_temp_to_target_callback
5. callback 执行完毕 -> 释放 SUBTREE 锁
```

注意：DAG callback 不在外层加锁。每个 `VikingFS.rm` 和 `VikingFS.mv` 内部各自有独立锁保护。外层锁会与内部锁冲突导致死锁。

**服务重启恢复**：SemanticMsg 持久化在 QueueFS 中。重启后 `SemanticProcessor` 发现 `lifecycle_lock_handle_id` 对应的 handle 不在内存中，会重新获取 SUBTREE 锁。

### session.commit()

| 问题 | 方案 |
|------|------|
| 消息已清空但 archive 未写入 -> 对话数据丢失 | Phase 1 无锁（archive 不完整无副作用）+ Phase 2 RedoLog |

LLM 调用耗时不可控（5s~60s+），不能放在持锁操作内。设计拆为两个阶段：

```
Phase 1 — 归档（无锁）：
  1. 生成归档摘要（LLM）
  2. 写 archive（history/archive_N/messages.jsonl + 摘要）
  3. 清空 messages.jsonl
  4. 清空内存中的消息列表

Phase 2 — 记忆提取 + 写入（RedoLog）：
  1. 写 redo 标记（archive_uri、session_uri、用户身份信息）
  2. 从归档消息提取 memories（LLM）
  3. 写当前消息状态
  4. 写 relations
  5. 直接 enqueue SemanticQueue
  6. 删除 redo 标记
```

**崩溃恢复分析**：

| 崩溃时间点 | 状态 | 恢复动作 |
|-----------|------|---------|
| Phase 1 写 archive 中途 | 无标记 | archive 不完整，下次 commit 从 history/ 扫描 index，不受影响 |
| Phase 1 archive 完成但 messages 未清空 | 无标记 | archive 完整 + messages 仍在 = 数据冗余但安全 |
| Phase 2 记忆提取/写入中途 | redo 标记存在 | 启动恢复：从 archive 重做提取+写入+入队 |
| Phase 2 完成 | redo 标记已删 | 无需恢复 |

## LockContext

`LockContext` 是**异步**上下文管理器，封装锁的获取和释放：

```python
from openviking.storage.transaction import LockContext, get_lock_manager

lock_manager = get_lock_manager()

# Point 锁（写操作、语义处理）
async with LockContext(lock_manager, [path], lock_mode="point"):
    # 执行操作...
    pass

# Subtree 锁（删除操作）
async with LockContext(lock_manager, [path], lock_mode="subtree"):
    # 执行操作...
    pass

# MV 锁（移动操作）
async with LockContext(lock_manager, [src], lock_mode="mv", mv_dst_parent_path=dst):
    # 执行操作...
    pass
```

**锁模式**：

| lock_mode | 用途 | 行为 |
|-----------|------|------|
| `point` | 写操作、语义处理 | 锁定指定路径；与同路径的任何锁和祖先目录的 SUBTREE 锁冲突 |
| `subtree` | 删除操作 | 锁定子树根节点；与同路径的任何锁、后代目录的任何锁和祖先目录的 SUBTREE 锁冲突 |
| `mv` | 移动操作 | 目录移动：源和目标均加 SUBTREE 锁；文件移动：源父目录和目标均加 POINT 锁（通过 `src_is_dir` 控制） |

**异常处理**：`__aexit__` 总是释放锁，不吞异常。获取锁失败时抛出 `LockAcquisitionError`。

## 锁类型（POINT vs SUBTREE）

锁机制使用两种锁类型来处理不同的冲突场景：

| | 同路径 POINT | 同路径 SUBTREE | 后代 POINT | 祖先 SUBTREE |
|---|---|---|---|---|
| **POINT** | 冲突 | 冲突 | — | 冲突 |
| **SUBTREE** | 冲突 | 冲突 | 冲突 | 冲突 |

- **POINT (P)**：用于写操作和语义处理。只锁单个目录。若祖先目录持有 SUBTREE 锁则阻塞。
- **SUBTREE (S)**：用于删除和移动操作。逻辑上覆盖整个子树，但只在根目录写**一个锁文件**。获取前扫描所有后代和祖先目录确认无冲突锁。

## 锁机制

### 锁协议

锁文件路径：`{path}/.path.ovlock`

锁文件内容（Fencing Token）：
```
{handle_id}:{time_ns}:{lock_type}
```

其中 `lock_type` 为 `P`（POINT）或 `S`（SUBTREE）。

### 获取锁流程（POINT 模式）

```
循环直到超时（轮询间隔：200ms）：
    1. 检查目标目录存在
    2. 检查目标路径是否被其他操作锁定
       - 陈旧锁？ -> 移除后重试
       - 活跃锁？ -> 等待
    3. 检查所有祖先目录是否有 SUBTREE 锁
       - 陈旧锁？ -> 移除后重试
       - 活跃锁？ -> 等待
    4. 写入 POINT (P) 锁文件
    5. TOCTOU 双重检查：重新扫描祖先目录的 SUBTREE 锁
       - 发现冲突：比较 (timestamp, handle_id)
       - 后到者（更大的 timestamp/handle_id）主动让步（删除自己的锁），防止活锁
       - 等待后重试
    6. 验证锁文件归属（fencing token 匹配）
    7. 成功

超时（默认 0 = 不等待）抛出 LockAcquisitionError
```

### 获取锁流程（SUBTREE 模式）

```
循环直到超时（轮询间隔：200ms）：
    1. 检查目标目录存在
    2. 检查目标路径是否被其他操作锁定
       - 陈旧锁？ -> 移除后重试
       - 活跃锁？ -> 等待
    3. 检查所有祖先目录是否有 SUBTREE 锁
       - 陈旧锁？ -> 移除后重试
       - 活跃锁？ -> 等待
    4. 扫描所有后代目录，检查是否有其他操作持有的锁
       - 陈旧锁？ -> 移除后重试
       - 活跃锁？ -> 等待
    5. 写入 SUBTREE (S) 锁文件（只写一个文件，在根路径）
    6. TOCTOU 双重检查：重新扫描后代目录和祖先目录
       - 发现冲突：比较 (timestamp, handle_id)
       - 后到者（更大的 timestamp/handle_id）主动让步（删除自己的锁），防止活锁
       - 等待后重试
    7. 验证锁文件归属（fencing token 匹配）
    8. 成功

超时（默认 0 = 不等待）抛出 LockAcquisitionError
```

### 锁过期清理

**陈旧锁检测**：PathLock 检查 fencing token 中的时间戳。超过 `lock_expire`（默认 300s）的锁被视为陈旧锁，在加锁过程中自动移除。

**进程内清理**：LockManager 每 60 秒检查活跃的 LockHandle，创建超过 3600 秒的 handle 强制释放。

**孤儿锁**：进程崩溃后遗留的锁文件，在下次任何操作尝试获取同一路径锁时，通过 stale lock 检测自动移除。

## 崩溃恢复

`LockManager.start()` 启动时自动扫描 `/local/_system/redo/` 目录中的遗留标记：

| 场景 | 恢复方式 |
|------|---------|
| session_memory 提取中途崩溃 | 从 archive 重做记忆提取 + 写入 + enqueue |
| 锁持有期间崩溃 | 锁文件留在 AGFS，下次获取时 stale 检测自动清理（默认 300s 过期）|
| enqueue 后 worker 处理前崩溃 | QueueFS SQLite 持久化，worker 重启后自动拉取 |
| 孤儿索引 | L2 按需加载时清理 |

### 防线总结

| 异常场景 | 防线 | 恢复时机 |
|---------|------|---------|
| 操作中途崩溃 | 锁自动过期 + stale 检测 | 下次获取同路径锁时 |
| add_resource 语义处理中途崩溃 | 生命周期锁过期 + SemanticProcessor 重启时重新获取 | worker 重启后 |
| session.commit Phase 2 崩溃 | RedoLog 标记 + 重做 | 重启时 |
| enqueue 后 worker 处理前崩溃 | QueueFS SQLite 持久化 | worker 重启后 |
| 孤儿索引 | L2 按需加载时清理 | 用户访问时 |

## 配置

路径锁默认启用，无需额外配置。**默认不等待**：若路径被锁定则立即抛出 `LockAcquisitionError`。如需允许等待重试，可通过 `storage.transaction` 段配置：

```json
{
  "storage": {
    "transaction": {
      "lock_timeout": 5.0,
      "lock_expire": 300.0
    }
  }
}
```

| 参数 | 类型 | 说明 | 默认值 |
|------|------|------|--------|
| `lock_timeout` | float | 获取锁的等待超时（秒）。`0` = 立即失败（默认）；`> 0` = 最多等待此时间 | `0.0` |
| `lock_expire` | float | 锁过期时间（秒），超过此时间的锁将被视为陈旧锁并强制释放 | `300.0` |

### QueueFS 持久化

路径锁机制依赖 QueueFS 使用 SQLite 后端，确保 enqueue 的任务在进程重启后可恢复。这是默认配置，无需手动设置。

## 相关文档

- [架构概述](./01-architecture.md) - 系统整体架构
- [存储架构](./05-storage.md) - AGFS 和向量库
- [会话管理](./08-session.md) - 会话和记忆管理
- [配置](../guides/01-configuration.md) - 配置文件说明
