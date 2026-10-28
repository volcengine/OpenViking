# 路径锁与崩溃恢复

OpenViking 通过**路径锁**和**持久化队列恢复**两种机制保护核心写操作（`rm`、`mv`、`add_resource`、`session.commit`）的一致性，协调并发写入，并在进程重启后继续处理已入队的会话任务。路径锁和队列恢复不构成跨 VikingFS、VectorDB、QueueManager 的原子事务。

## 设计哲学

OpenViking 是上下文数据库，FS 是源数据，VectorDB 是派生索引。索引通常可以从保留的源数据重建；恢复丢失的源数据则依赖备份。因此：

> **宁可搜不到，不要搜到坏结果。**

## 设计原则

1. **写互斥**：参与锁协议的不同 owner 不能同时取得冲突路径的锁
2. **默认生效**：受保护的写操作默认加锁；普通读取和底层 mkdir 不自动加锁
3. **锁只保护并发**：运行时申请和释放 lease，不提供跨存储的 undo/journal/commit 语义
4. **持久化任务恢复**：`SessionCommit` 队列恢复会话 Phase 2；资源派生处理由相应的持久化队列恢复
5. **锁与队列配合**：业务路径可在持锁时入队并交接 lease；重试和去重取决于具体任务协议，不能把所有 enqueue 视为天然幂等

## 架构

<PathLockDiagram />

## 两个核心组件

### 组件 1：路径锁系统

运行时锁由 Rust RAGFS 的 `PathLockManager` 和 Provider 管理。Python 服务通过 binding 申请、续用、交接和释放 lease。后文的 `LockContext` 示例仅说明生命周期，不是当前 Python SDK 或运行时类接口。

- **范围**：EXACT 保护路径本身，TREE 保护路径及其子树。
- **Provider**：默认 filesystem 保存锁文件；cache 用 Redis 原子检查和写入 token；memory 只在当前进程内协调。
- **lease**：记录 owner、覆盖路径、token 位置和续期状态。后台任务可以接收显式交接的 lease。
- **失败处理**：释放前校验 owner，过期锁由后续获取时的 stale 检查清理；锁释放不会撤销已写入的数据。

### 组件 2：持久化 `SessionCommit` 队列（崩溃恢复）

队列名为 `SessionCommit`，任务类型为 `session_commit`。

`session.commit` 的 Phase 2 不再使用独立 RedoLog。Phase 1 会先把 archive 元数据持久化，再把
`SessionCommitMsg` 写入持久化队列；进程重启后，QueueManager 会继续消费遗留的 `session_commit`
任务并恢复 Phase 2。

恢复使用已保存的 archive 和处理状态。记忆提取包含模型调用，不能假设重新执行会生成逐字相同的内容。

## 一致性问题与解决方案

### rm(uri)

| 问题 | 方案 |
|------|------|
| 先删文件再删索引 -> 文件已删但索引残留 -> 搜索返回不存在的文件 | **调换顺序**：先删索引再删文件。索引删除失败 -> 源文件仍在，重试可完成可能只执行了一部分的索引清理 |

**加锁策略**（根据目标类型区分）：
- 删除**目录**：`lock_mode="tree"`，锁目录自身及其整棵子树
- 删除**文件**：`lock_mode="exact"`，锁文件路径本身

操作流程：

```
1. 检查目标是目录还是文件，选择锁模式
2. 获取锁
3. 删除 VectorDB 索引 -> 搜索立刻不可见
4. 删除 FS 文件
5. 释放锁
```

索引 URI 收集或 VectorDB 删除失败 -> 直接抛异常，锁自动释放，源文件仍在。多记录删除在部分
后端可能已经执行了一部分，但重试可以安全补完清理。FS 删除失败 -> VectorDB 已删但文件还在，
重试同样安全。

### mv(old_uri, new_uri)

| 问题 | 方案 |
|------|------|
| 文件移到新路径但索引指向旧路径 -> 搜索返回旧路径（不存在） | 先 copy 再更新索引，失败时清理副本 |

**加锁策略**（由公开的 `VikingFS.mv` 实现申请）：
- 移动**目录**：源路径加 TreeLock，目标路径加 TreeLock
- 移动**文件**：源路径和目标路径各加 EXACT 锁

操作流程：

```
1. 检查源是目录还是文件，确定 src_is_dir
2. 获取 mv 锁（内部根据 src_is_dir 选择 TreeLock 或 ExactPathLock）
3. Copy 到新位置（源还在，安全）
4. 如果是目录，删除副本中被 cp 带过去的锁文件
5. 更新 VectorDB 中的 URI
   - 失败 -> 尝试清理副本；索引或清理可能部分完成，需检查后再重试
6. 删除源
7. 释放锁
```

### add_resource

| 问题 | 方案 |
|------|------|
| 文件从临时目录移到正式目录后崩溃 -> 文件存在但永远搜不到 | 先提交内容计划，再由持久化队列完成派生索引 |
| 资源已落盘但语义处理/向量化还在跑时被 rm 删除 -> 处理白跑 | 生命周期 TreeLock，从落盘持续到处理完成 |

**首次添加和增量更新**使用同一条计划提交路径：

```
1. 获取 final_uri 的资源锁。
2. 持锁构建 R/N/F/V 快照：
   - R：归一化后的请求意图
   - N：解析产物清单
   - F：当前正式资源树
   - V：当前向量记录；build_index=false 时跳过
3. 编译 ContextUpdatePlan。
4. 同步把计划中的内容动作提交到 final_uri。
5. 清理 parser artifact。
6. 入队直接索引动作；需要语义处理时，再入队携带剩余 SemanticPlan 的 SemanticMsg。
7. 将资源锁交接给语义处理；没有语义任务时直接释放。
```

因此正式内容树会先于 semantic 和 embedding 工作更新。内容提交成功后，
派生摘要和向量可能短暂落后，并由队列任务补齐；队列不再负责把 parser
临时树复制到正式树。

此期间 `rm` 尝试获取同路径 TreeLock 会失败，抛出 `ResourceBusyError`。

自动命名由资源层处理，不属于锁服务：`ResourceProcessor` 先用 `exists(candidate_uri)`
判断候选目录是否已占用；已存在则尝试 `_1`、`_2` 后缀。候选目录不存在时才尝试
获取该目录的 `TreeLock`，且不等待；如果同名正在被并发请求处理，就直接尝试下一个后缀。

**服务重启恢复**：`SemanticMsg` 和 `SemanticPlan` 保存在持久化 QueueFS 后端中。语义处理通过 `lock_handoff` 尝试接管 lease；对可恢复的过期交接，按 `covered_paths` 重新申请覆盖范围后继续。锁冲突或不可恢复的交接错误仍会使处理失败，需检查任务状态。

### 派生语义文件（.abstract.md / .overview.md）

`.abstract.md` 和 `.overview.md` 是后台生成的派生文件，不作为普通用户源文件写入。它们的并发保护分两层：

| 问题 | 方案 |
|------|------|
| 多个后台任务同时刷新同一个目录摘要，旧结果覆盖新结果 | 相同 dirty key 使用 `coalesce_version`，只有最新版本允许写回 |
| 最新任务写回派生文件时与另一个写回交错 | 写 `.abstract.md`、`.overview.md` 前获取各自的 ExactPathLock |

例子：同一目录下并发写入 `a.md`、`b.md`、`c.md` 时，前台写入分别持有 `ExactPathLock(a.md)`、`ExactPathLock(b.md)`、`ExactPathLock(c.md)`，互不阻塞。后台可能产生多个 `docs/` 摘要刷新任务，但只有最新 version 能写回 `docs/.overview.md` 和 `docs/.abstract.md`；旧任务在写回前发现自己过期后直接丢弃结果。

memory 目录摘要使用同一规则。比如并发更新：

```text
viking://user/default/memories/preferences/theme.md
viking://user/default/memories/preferences/editor.md
```

两个文件写入各自持有 ExactPathLock；`preferences/.overview.md` 和 `preferences/.abstract.md` 的后台刷新不再持有长时间 TreeLock，而是通过 `coalesce_version` 淘汰旧任务，并在最终写派生文件时短暂获取 ExactPathLock。

### session.commit()

Phase 1 使用会话根目录的 EXACT 锁划定提交边界。模型调用耗时不可控，因此摘要生成和记忆提取放在后台，不在这个边界锁内等待：

```text
Phase 1：持锁准备并发布归档
  1. 读取消息与提交策略，分配归档编号，划分归档和保留消息
  2. 写归档消息及元数据
  3. 入队 SessionCommitMsg 并创建 task
  4. 写当前保留消息、会话元数据和 phase1 ready 标记
  5. 释放锁，返回 task_id（无可归档内容时 skipped）

Phase 2：后台处理已发布归档
  1. 读取归档消息并生成摘要
  2. 提取、更新记忆并安排派生处理
  3. 写 memory_diff.json 和完成标记
```

**崩溃恢复分析**：

| 时间点 | 应检查什么 |
| --- | --- |
| Phase 1 尚未发布 | 归档准备可能只完成一部分；检查归档状态和当前消息，不把目录存在当作提交成功 |
| 已入队但 ready 尚未写入 | 后台处理需要核对 Phase 1 状态；错误路径会尝试记录失败标记，不能仅凭 task 存在判断成功 |
| Phase 2 提取或写入中途 | 持久化队列可恢复处理，但模型重试不保证生成相同正文 |
| Phase 2 完成 | 核对 task 最终状态、归档完成标记和实际记忆变更 |

## LockContext

以下伪代码说明申请、使用、释放锁的顺序。当前运行时使用 RAGFS lease API，不能直接从 Python SDK 导入 `LockContext`：

```python
# Conceptual example: production path locks are acquired inside the Rust ragfs layer.

# Exact 锁（写操作、语义处理）
async with LockContext(lock_manager, [path], lock_mode="exact"):
    # 执行操作...
    pass

# Tree 锁（删除目录、目录生命周期保护）
async with LockContext(lock_manager, [path], lock_mode="tree"):
    # 执行操作...
    pass

# MV 锁（移动操作）
async with LockContext(lock_manager, [src], lock_mode="mv", mv_dst_path=dst):
    # 执行操作...
    pass
```

**锁模式**：

| lock_mode | 用途 | 行为 |
|-----------|------|------|
| `exact` | 文件写入、单文件删除、派生文件写回 | 锁定指定路径；与同路径锁和祖先目录 TreeLock 冲突 |
| `tree` | 删除目录、资源生命周期、目录级保护 | 锁定子树根节点；与同路径锁、后代锁和祖先 TreeLock 冲突 |
| `mv` | 移动操作 | 目录移动：源路径 TreeLock + 目标路径 TreeLock；文件移动：源路径和目标路径均 ExactPathLock（通过 `src_is_dir` 控制） |

**异常处理**：业务调用通过 `finally` 释放或交接 lease。锁冲突会返回相应 busy/锁获取错误；释放失败或进程退出后的 token 依赖过期清理。

## 锁类型（EXACT vs TREE）

锁机制使用两种锁类型来处理不同的冲突场景：

| | 同路径 EXACT | 同路径 TREE | 后代 EXACT | 祖先 TREE |
|---|---|---|---|---|
| **EXACT** | 冲突 | 冲突 | — | 冲突 |
| **TREE** | 冲突 | 冲突 | 冲突 | 冲突 |

- **EXACT (E)**：锁定一个具体路径本身。文件、目录名、尚未创建的目标路径都可以使用；若祖先目录持有 TreeLock 则阻塞。
- **TREE (T)**：用于删除目录、移动目录、资源生命周期保护等。逻辑上覆盖整棵子树，但只为根路径保存一个 Provider token。冲突检查覆盖 Provider scope 内的后代和持有 Tree 锁的祖先。Filesystem Provider 可能为了写锁文件而创建尚不存在的目标目录。

### 路径范围与目标类型

Exact 和 Tree 表达操作范围，文件、目录或缺失路径表达目标的当前状态，两者独立。锁保护路径名字，目标不存在也可以申请锁。

锁路径必须是绝对路径。Manager 在获取锁或检查租约覆盖范围前，对空路径、
相对路径、重复 `/`、`.` 或 `..` 路径段及 NUL 返回 `InvalidRequest`。
非根路径末尾的单个 `/` 会被移除。Redis 和 SQLite 的 token 操作要求路径
已完成这一规范化；Redis Lua 批量获取也会在修改 token 前校验全部请求。
应使用 `/local/account-a/file`，避免 `/local/account-a/./file`、
`/local/account-a//file` 等别名。

以下冲突关系限定为不同 owner、同一 Provider scope 内的请求：

| 已持有 | 新请求 | 冲突 |
| --- | --- | --- |
| Exact(`/docs/a.md`) | Exact 或 Tree(`/docs/a.md`) | 是 |
| Exact(`/docs`) | Exact(`/docs/a.md`) | 否 |
| Tree(`/docs`) | Exact 或 Tree(`/docs/a.md`) | 是 |
| Exact(`/docs/a.md`) | Tree(`/docs`) | 是 |
| Tree(`/docs/a.md`) | Exact(`/docs/b.md`) | 否 |

`Tree(/docs/a.md)` 不会扩大为 `Tree(/docs)`。反过来，目录自身的 Exact 也不能保护子树，递归删除需要 Tree。

锁只协调参与协议的操作。底层 `PathLockWrappedFS` 对 create、write、truncate、非递归 remove 使用 Exact，对 remove_all 使用 Tree；文件 rename 锁源和目标的 Exact，底层目录 rename 锁源 Tree 和目标 Exact；公开目录 `mv` 额外保护源、目标两棵子树。read、stat、列目录和 mkdir 直接转发，上层可另行持锁。绕过协议的 I/O 不会被操作系统自动阻断。

## 锁机制

### Filesystem Provider 锁协议

锁类型由调用者选择。Filesystem Provider 对所有目标统一使用父目录 sidecar：

| 目标状态 | Exact token | Tree token |
| --- | --- | --- |
| 现存文件 `/docs/a` | `/docs/.exact.ovlock.a.<hash>`，内容为 E | 同一 sidecar，内容为 T |
| 现存目录 `/docs/a` | `/docs/.exact.ovlock.a.<hash>`，内容为 E | 同一 sidecar，内容为 T |
| 缺失路径 `/docs/a` | `/docs/.exact.ovlock.a.<hash>`，内容为 E | 同一 sidecar，内容为 T |

sidecar 位于目标旁边，但只代表该目标，不会锁住整个父目录。递归删除目标时不会删除位于父目录中的 token，因此锁的互斥保护会持续到 lease 正式释放。`<hash>` 来自完整后端路径的 SHA-1 前缀，与业务文件内容无关。

`.exact.ovlock.*` 根据实际锁类型存储 E 或 T。为兼容升级前遗留 token，冲突检测仍读取目标内部的 `.path.ovlock`，但新锁不再写入该位置。文件名是存储协议的一部分，不能单凭名字判断逻辑锁类型。token 内容为：

```text
{owner_id}:{time_ns}:{lock_type}
```

`lock_type` 为 `E` 或 `T`。该归属 token 用于竞争检查、续期和条件释放，不代表所有业务写入都有存储端 fencing 校验。

lease 将逻辑范围 `covered_paths` 与 token 位置 `lock_paths` 分开记录。Owned lease 控制续期、释放和交接；Borrowed lease 仅提供已有锁的覆盖证明，不能释放外层锁。

#### 协议迁移与发布

新版本对新 sidecar 和旧 `.path.ovlock` 执行双读，但只向新 sidecar 写入 token。升级前已存在的 lease 不做原地迁移：续期、释放和交接继续使用 lease 中记录的旧 `lock_paths`，直到旧 token 正常释放或过期。

旧版本不会检查祖先路径的新 sidecar，因此 Filesystem Provider 不能让新旧版本在同一后端上同时处理写请求。当前版本采用协调切换：

1. 停止新的写请求，等待活跃 lease 和待交接任务排空，然后停止所有旧实例。
2. 全量启动新版本实例，确认旧 token 已释放或可按过期规则回收，再恢复写流量。

如果部署必须保持零停机滚动升级，需要拆成两个可独立发布的协议阶段：先发布“新旧双读、仍写旧 token”的兼容版本；全部实例升级后，再统一切换为“新旧双读、只写新 sidecar”。两个阶段之间不得存在旧实例读取新 sidecar 的窗口。

### Cache Provider 锁协议

Cache Provider 将相同 token 格式存入 Redis HASH field，并通过 Lua
原子完成整批冲突检查和写入：

```text
field = logical_path
value = owner_id:time_ns:lock_type
```

HASH key 按路径 scope 隔离：

```text
{service_name}:ov:pathlock:global:tokens
{service_name}:ov:pathlock:scope:_system:tokens
{service_name}:ov:pathlock:scope:account:{account}:tokens
```

所有 key 都使用 `{service_name}` 作为 Redis Cluster hash tag。Exact 获取使用
`HMGET` 读取目标和祖先；Tree 获取只对所属 scope 的 HASH 执行
`HGETALL`。`/` 和 `/local` 的 Tree 锁会返回 `InvalidRequest`，因为它们的
子树跨越 account 和 `_system` HASH。Tree 根路径应使用 `/local/<account>`、
`/local/_system` 或其后代。`/` 和 `/local` 的 Exact 锁仍然支持，只保护
对应路径本身。跨 scope batch 会被拒绝。

### Filesystem 获取锁流程（EXACT 模式）

```
循环直到超时（轮询间隔：200ms）：
    1. 检查目标路径是否被其他操作锁定
       - 陈旧锁？ -> 移除后重试
       - 活跃锁？ -> 等待
    2. 检查所有祖先目录是否有 TREE 锁
       - 陈旧锁？ -> 移除后重试
       - 活跃锁？ -> 等待
    3. 确保锁文件所在父目录存在；如果不存在则创建目录
    4. 写入 EXACT (E) 锁文件
    5. TOCTOU 双重检查：重新扫描目标路径和祖先目录的 TREE 锁
       - 发现冲突：比较 (timestamp, handle_id)
       - 后到者（更大的 timestamp/handle_id）主动让步（删除自己的锁），防止活锁
       - 等待后重试
    6. 验证锁文件归属（fencing token 匹配）
    7. 成功

超时（默认 0 = 不等待）抛出 LockAcquisitionError
```

### Filesystem 获取锁流程（TREE 模式）

```
循环直到超时（轮询间隔：200ms）：
    1. 检查目标路径是否被其他操作锁定
       - 陈旧锁？ -> 移除后重试
       - 活跃锁？ -> 等待
    2. 检查所有祖先目录是否有 TREE 锁
       - 陈旧锁？ -> 移除后重试
       - 活跃锁？ -> 等待
    3. 扫描所有后代目录，检查是否有其他操作持有的锁
       - 目标目录不存在？ -> 视为无后代锁
       - 陈旧锁？ -> 移除后重试
       - 活跃锁？ -> 等待
    4. 确保 Resolver 选定的 sidecar 父目录存在；不会创建缺失目标本身
    5. 在父目录 sidecar 写入 TREE (T) token
    6. TOCTOU 双重检查：重新扫描后代目录和祖先目录
       - 发现冲突：比较 (timestamp, handle_id)
       - 后到者（更大的 timestamp/handle_id）主动让步（删除自己的锁），防止活锁
       - 等待后重试
    7. 验证锁文件归属（fencing token 匹配）
    8. 成功

超时（默认 0 = 不等待）抛出 LockAcquisitionError
```

### 缺失目录创建规则

锁系统允许为了放置 sidecar 而创建缺失的父目录链，但不会创建被锁目标本身。创建前必须先检查冲突：

```
1. 发现祖先 TreeLock / 同路径锁 / 后代锁冲突 -> 不创建目录，直接失败或等待
2. 当前无冲突 -> 可以创建 sidecar 的父目录并写锁
3. 写锁后再次检查时发现新冲突 -> 删除自己的锁并失败或重试
4. 第 3 步不会回滚刚创建的空目录
```

例子：

```text
请求 A 正在删除 viking://resources/books
=> A 持有 TreeLock(/resources/books)

请求 B 想添加 viking://resources/books/java-guide
=> B 在创建 java-guide 目录前发现祖先 TreeLock
=> B 不创建目录，返回 busy
```

如果两个请求同时创建 `java-guide`，两边都可能先看到“当前无冲突”，但最终只有
fencing token 校验通过的一方成功持有 `TreeLock(java-guide)`；失败方会删除自己的锁。

获取失败的回滚和正常释放只清理 token，不保证删除为存放 sidecar 创建的父目录链。Snapshot 对缺失目标采用单独的策略，见 [快照的范围与并发](../guides/15-snapshot.md#提交范围与并发)。

### 锁过期清理

**自动续期**：Rust PathLockManager 每隔 `lock_expire / 3` 刷新活跃 lease；默认过期时间为 30 秒，不是业务操作的最长运行时间。进程退出后续期停止。

**陈旧锁检测**：PathLockEngine 检查归属 token 中的时间戳。超过 `lock_expire`（默认 30s）的锁被视为陈旧锁，在加锁过程中自动移除。

**失锁后的授权失效**：手动和后台续租使用相同规则。token 不存在或 owner 不匹配时，同 owner 下共享实际 token 路径（`lock_paths`）的所有本地 lease 立即整份失效，包括待交接的 lease。Tree 祖先和 Exact 后代使用不同 token，不会因逻辑覆盖关系连带失效。

网络等不确定错误允许短暂重试，但每个 token 距离最近一次成功确认达到 `0.8 × lock_expire`（默认 24 秒）时撤销授权。使用单调时钟分别记录各 token 的确认时间；部分续租成功、重入和本地交接/接管不会延长失败 token 的期限。写入授权检查也会检查期限，无需等待下次心跳。失效 lease 不能再授权后续受保护写入、续租、复用 owner 或交接/接管，且不会恢复有效。

**进程内清理**：失效 lease 保留释放能力和本地引用，后台重试带 owner 校验的清理。token 已不存在或已被其他 owner 接管均视为清理完成，不会删除新 owner 的 token；仍被其他有效本地 lease 引用的 token 也不会被删除。从首次失效起超过六个心跳间隔（`2 × lock_expire`，默认 60 秒），即使 Provider 持续不可用也记录剩余路径并移除本地 entry，遗留的远端 token 依赖过期清理。重试不会延长该窗口。

**失效诊断**：manager 在清理期间保留首次失效的时间与原因。`pathlock lease invalidated` 日志包含 `reason`、`lease_ref`、`owner_id` 和 `affected_paths`。`TokenLost` 表示必要 token 缺失或归属不符，应检查 token 过期、删除和接管情况；`ConfirmationDeadlineExceeded` 表示安全期限内未能确认归属，应检查 Provider 健康、网络延迟和续租调度，对应 token 日志还包含 `since_last_confirmation_ms` 和 `failures`；`ReleaseFailed` 表示释放或降级错误导致撤销授权并进入清理重试，释放日志会记录具体错误。后续响应和清理失败不会覆盖首次原因或重置时间，清理窗口耗尽的日志也会保留该原因。

上述检查在发现失锁或达到期限后拒绝后续操作；不会中断已经通过授权检查的写入，也不提供存储侧 fencing。

**孤儿锁**：进程崩溃后遗留的 Provider token，在后续 acquire 检查同一路径或 scope 时通过 stale lock 检测自动移除。

## 崩溃恢复

服务启动后，QueueManager 会继续消费持久化的 `session_commit` 任务：

| 场景 | 恢复方式 |
|------|---------|
| session_memory 提取中途崩溃 | 从 archive 恢复 Phase 2 并继续消费 `session_commit` 任务 |
| 锁持有期间崩溃 | Provider token 保留，后续匹配的 acquire 通过 stale 检测自动清理（默认 30s 过期）|
| enqueue 后 worker 处理前崩溃 | QueueFS SQLite 持久化，worker 重启后自动拉取 |
| 孤儿索引 | `rm` 对不存在的目标也会尝试清理相关向量记录 |

### 防线总结

| 异常场景 | 防线 | 恢复时机 |
|---------|------|---------|
| 操作中途崩溃 | 锁自动过期 + stale 检测 | 下次获取同路径锁时 |
| add_resource 语义处理中途崩溃 | 生命周期锁过期 + SemanticProcessor 重启时重新获取 | worker 重启后 |
| session.commit Phase 2 崩溃 | 持久化 `SessionCommit` 队列 + 重试消费 | 重启时 |
| enqueue 后 worker 处理前崩溃 | QueueFS SQLite 持久化 | worker 重启后 |
| 孤儿索引 | `rm` 对不存在的目标也会尝试清理相关向量记录 | 调用 `rm` 时 |

## 配置

路径锁默认启用，并使用 `filesystem` Provider。多进程通过 Redis 协调时，
设置 `storage.agfs.pathlock.provider=cache`。Cache PathLock 要求配置顶层
Redis Cache Provider，并使用顶层 `service_name` 作为 Redis namespace。
运行时等待超时固定为 `0.0` 秒。`storage.transaction` 仅保留为兼容旧配置：`lock_timeout`
已废弃且会被忽略，`lock_expire` 会在未显式配置新字段时自动映射，
`redo_recovery_enabled` 已废弃且会被忽略。

推荐写法：

```json
{
  "storage": {
    "agfs": {
      "pathlock": {
        "provider": "filesystem",
        "lock_expire_secs": 30.0
      }
    }
  }
}
```

Redis 配置：

```json
{
  "service_name": "production",
  "cache": {
    "provider": "redis",
    "params": {
      "mode": "standalone",
      "endpoints": ["redis://127.0.0.1:6379"]
    }
  },
  "storage": {
    "agfs": {
      "pathlock": {
        "provider": "cache",
        "lock_expire_secs": 30.0
      }
    }
  }
}
```

| 参数 | 类型 | 说明 | 默认值 |
|------|------|------|--------|
| `provider` | str | `filesystem`、`memory`、`sqlite` 或 `cache` | `filesystem` |
| `sqlite_path` | str / null | 使用 `sqlite` 时可覆盖本地数据库绝对路径；见 [SQLite 配置](../configuration/01-server.md#storage) | 使用 `sqlite` 时为 `{storage.workspace}/_system/pathlock/pathlock.db` |
| `lock_expire_secs` | float | 未刷新的锁进入 stale 状态前的秒数 | `30.0` |

跨容器或主机共享 EFS 的副本应使用 `provider="cache"`，连接共享 Redis。
SQLite 仅支持同机共享同一个本地磁盘数据库文件，不支持 EFS/NFS/SMB
或各容器独立的本地数据库。参见[部署限制与 Provider 选择](../configuration/01-server.md#storage)。

兼容旧写法：

```json
{
  "storage": {
    "transaction": {
      "lock_expire": 30.0
    }
  }
}
```

| 参数 | 类型 | 说明 | 默认值 |
|------|------|------|--------|
| `lock_timeout` | float | 已废弃且忽略。运行时等待超时固定为 `0.0`。 | `0.0` |
| `lock_expire` | float | 已废弃。改用 `storage.agfs.pathlock.lock_expire_secs`。 | `30.0` |

### QueueFS 持久化

重启恢复要求队列内容已持久化。QueueFS 默认使用 SQLite；使用 cache 后端时，持久性取决于所配置的 Provider。memory 后端不保留进程退出后的任务。

## 相关文档

- [架构概述](./01-architecture.md) - 系统整体架构
- [存储架构](./05-storage.md) - AGFS 和向量库
- [会话管理](./08-session.md) - 会话和记忆管理
- [配置](../guides/01-configuration.md) - 配置文件说明
