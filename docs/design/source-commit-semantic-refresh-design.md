# Source Commit 与 Semantic Refresh 解耦设计

| 项目 | 信息 |
|------|------|
| 状态 | `实施中` |
| 创建日期 | 2026-05-12 |
| 相关模块 | `add_resource`, `VikingFS`, `PathLockEngine`, `LockManager`, `SemanticQueue`, `VectorDB`, `SessionCompressorV2` |

## 背景

本设计来自本地 OpenViking Server 压测。压测使用 `127.0.0.1:1935` 并发执行：

- SDK 并发 `add_resource`
- CLI 并发 `add`
- 并发检索
- 并发创建不同 `session_id` 的会话消息
- 并发 `commit`
- 上述请求的混合压测

其中最明确的问题是：

```text
同一个目录下并发添加多个不同资源时，部分 add_resource 返回 500。
```

客户端看到的是：

```text
InternalError: Internal server error
Error: API error: [INTERNAL] Internal server error
```

Server 侧真实错误是：

```text
[EXACT] Timeout waiting for lock on: /local/default/resources/bench/load_test/sdk
LockAcquisitionError: Failed to acquire exact lock for ['/local/default/resources/bench/load_test/sdk']
```

例子：

```text
add viking://resources/bench/load_test/sdk/resource-0000.md
add viking://resources/bench/load_test/sdk/resource-0001.md
add viking://resources/bench/load_test/sdk/resource-0002.md
```

这些请求写入的是不同文件，理论上不应该因为共享父目录而互斥。单纯调大等待时间不解决根因，只会把失败换成长尾延迟。

## 第一性原理

OpenViking 的数据分两类。

源数据是用户真正提交的内容：

```text
docs/a.md
docs/b.md
docs/images/logo.png
```

源数据必须强一致：成功就是已经落盘，不能被并发 `rm`、`mv`、同路径写入破坏。

派生数据是系统根据源数据生成的内容：

```text
docs/a.md 的摘要
docs/.abstract.md
docs/.overview.md
向量索引
relations
```

派生数据可以异步、可重试、可短暂落后，但不能反过来阻塞或回滚已经成功提交的源数据。

因此核心原则是：

```text
前台请求只负责把源数据正确提交。
后台任务负责把语义状态最终追到最新。
```

## 总体方案

把 `add_resource` 拆成两个阶段：

```text
Source Commit      源数据提交，短事务，强一致
Semantic Refresh   语义刷新，异步，可合并，带版本，可重试
```

### Source Commit

Source Commit 只做最终源路径提交：

```text
1. 文件先写入 temp 区。
2. 解析、切分、校验都在 temp 区完成。
3. 根据目标 URI 进入很短的提交阶段。
4. 获取目标路径对应的短锁。
5. temp 内容移动到最终路径，或作为已有目标的增量输入。
6. 获取资源生命周期锁，交给后续语义任务释放。
7. 返回 add_resource 成功或明确冲突错误。
```

关键点：

- 解析、摘要、向量化不能持有目录锁。
- 同目录不同文件不互斥。
- 同路径、目录删除、目录移动必须互斥。
- 顺序重复写同一个显式 `to` 目标，继续兼容现有增量更新语义。
- 并发写同一个显式目标时，返回结构化 `409 CONFLICT`，而不是 `500`。

### Semantic Refresh

Semantic Refresh 处理 `.abstract.md`、`.overview.md`、向量索引等派生状态：

```text
1. Source Commit 成功后 enqueue 语义刷新消息。
2. 相同 dirty key 的消息递增 coalesce version。
3. 后台 worker 仍然可以并发生成，但写回前必须检查自己是不是最新 version。
4. 旧 version 丢弃结果，不写 `.overview.md`、`.abstract.md`，也不继续向量化。
5. 最新 version 使用派生文件的 ExactPathLock 做最终写回。
```

## 锁模型

不再维护 “当前目录锁 / NamespaceLock” 作为 `add_resource` 主路径。目录级长锁粒度太粗，会把不同文件的并发写入错误串行化。

最终只保留两类锁语义和一个派生写回规则。

| 锁 | 保护对象 | 典型场景 | 是否跨解析/摘要/向量化 | 说明 |
|----|----------|----------|------------------------|------|
| `ExactPathLock(path)` | 单个路径 | `add docs/a.md`、`rm docs/a.md`、`mv docs/a.md docs/b.md`、写 `.overview.md` | 否 | 不同文件不同锁，可并发；同一路径冲突；派生文件最终写回也用它 |
| `TreeLock(path)` | 一棵目录树 | `rm docs/ --recursive`、目录移动、资源生命周期、memory 变量文件名 schema | 只覆盖必要结构变更或生命周期保护 | 与子路径 exact commit 互斥 |
| `SemanticCoalesceVersion(key)` | 语义刷新任务的新旧关系 | 父目录摘要、memory 目录摘要、直接 write 后刷新 | 不持锁 | 不是文件锁；只判断旧任务能不能写回 |

不再维护父目录点锁作为业务层语义。新路径只围绕 `ExactPathLock` 和 `TreeLock` 建模。

### 为什么不保留目录命名空间锁

自动命名看起来像需要锁整个目录，比如两个请求同时写 `report.md`，都可能想选 `report_1.md`。

更简单的做法是逐个候选名尝试 `ExactPathLock`：

```text
请求 A: 尝试 report.md，拿到 ExactPathLock(report.md)，提交成功
请求 B: 尝试 report.md，发现已存在或被占用，继续尝试 report_1.md
请求 B: 拿到 ExactPathLock(report_1.md)，提交成功
```

这个方案不保证编号连续，也不要求大家先抢目录锁。它只保证最终不会两个请求写到同一个路径。对用户来说，这是更重要的正确性。

### Memory extraction 的 schema 作用域

Memory extraction 和资源写入不同。资源写入一开始就知道最终路径，memory extraction 则需要先根据 schema 读取、搜索已有 memory，再让模型决定 operations。因此 memory 的锁保护的是：

```text
模型看到的 schema 作用域上下文
到 apply operations 写回
这段期间的一致性
```

预期规则：

| schema 类型 | 例子 | 锁 |
|-------------|------|----|
| 固定文件名 | `profile.md`、`soul.md`、`identity.md` | `ExactPathLock(具体文件路径)` |
| 变量文件名 | `preferences/{{ user }}/{{ topic }}.md`、`events/.../{{ event_name }}.md` | `TreeLock(schema 目录)` |

例子：

```text
profile schema -> ExactPathLock(viking://user/default/memories/profile.md)
preferences schema -> TreeLock(viking://user/default/memories/preferences)
events schema -> TreeLock(viking://user/default/memories/events)
```

这样 fixed schema 不会再锁住整个 `/memories` 父目录；变量文件名 schema 仍然锁 schema 目录，避免两个模型基于同一个旧目录视图做重复决策。

Agent memory 也使用同一规则。`experiences/*` 这类变量文件名 schema 使用 `TreeLock(experiences/)`。系统维护的 `source_trajectories` 元数据回填属于 experience 更新的一部分，必须在 experience schema 锁释放前完成，避免另一个 extraction 基于“内容已更新但来源记录还没补上”的中间状态继续推理。

## 具体场景

### 1. 同目录并发添加不同文件

输入：

```text
add docs/a.md
add docs/b.md
add docs/c.md
```

预期：

```text
a.md、b.md、c.md 都可以并发 exact commit。
后台合并刷新 docs/ 的语义状态。
```

原因：

```text
ExactPathLock(docs/a.md)
ExactPathLock(docs/b.md)
ExactPathLock(docs/c.md)
```

三把锁不同，不需要互斥。

### 2. 同路径并发添加同一个文件

输入：

```text
add docs/a.md
add docs/a.md
```

预期：

```text
一个请求获得 ExactPathLock(docs/a.md)。
另一个请求返回 409 CONFLICT，details.conflict_type = path_busy。
```

顺序执行时，如果是显式 `to=viking://resources/docs/a.md`，仍按现有逻辑做增量更新，避免破坏兼容性。

### 3. 自动重名

输入：

```text
两个请求都上传 report.md，目标是 viking://resources/docs/
```

预期：

```text
第一个可能落到 docs/report.md。
第二个可能落到 docs/report_1.md。
```

也允许在极端竞争下第二个跳到 `report_2.md`。编号不是一致性的核心，核心是不能两个请求都返回同一个最终路径。

### 4. 删除文件与添加同文件并发

输入：

```text
rm docs/a.md
add docs/a.md
```

预期：

```text
两者竞争 ExactPathLock(docs/a.md)。
先拿到锁的操作先完成，另一个返回 409 或后续重试。
```

不能出现：

```text
add 返回成功，但文件马上被同一轮未串行化的 rm 删除，且用户看不到原因。
```

### 5. 删除目录与添加子文件并发

输入：

```text
rm docs/ --recursive
add docs/a.md
```

预期：

```text
rm docs/ 获取 TreeLock(docs/)。
add docs/a.md 获取 ExactPathLock(docs/a.md) 时会被 TreeLock 阻塞或返回 409。
```

如果 add 先完成，rm 可以随后删除整棵树；这是可解释的顺序结果。

### 6. 重命名文件

输入：

```text
mv docs/a.md docs/b.md
```

预期：

```text
同时获取 ExactPathLock(docs/a.md) 和 ExactPathLock(docs/b.md)。
```

这样能避免：

- 源文件被另一个请求删除
- 目标文件被另一个请求创建
- 同一路径被两个 mv 请求同时占用

### 7. 重命名目录

输入：

```text
mv docs/ archived/docs/
```

预期：

```text
目录移动使用 TreeLock / mv lock。
它必须和 docs/ 下所有 ExactPathLock 冲突。
```

不能出现：

```text
文件写在旧路径，但索引或语义任务认为它在新路径。
```

### 8. `.abstract.md` 和 `.overview.md`

`.abstract.md`、`.overview.md` 是派生语义文件，不应该当普通源文件处理。

推荐语义：

```text
用户直接写 docs/.overview.md -> 拒绝
后台生成 docs/.overview.md -> 先确认任务 version 仍然最新，再用 ExactPathLock(docs/.overview.md) 写回
```

例子：

```text
任务 A 开始刷新 docs/，coalesce version = 1
期间 b.md 上传成功，又 enqueue docs/ 刷新，coalesce version = 2
任务 A 写回前发现自己不是最新 version
任务 A 丢弃 overview/abstract，不覆盖新状态
任务 B 基于当前 docs/ 重新生成并写回
```

这样可以避免旧 overview 覆盖新 overview。

### 9. 批量上传避免重复语义生成

输入：

```text
add docs/a.md
add docs/b.md
add docs/c.md
add docs/d.md
```

预期：

```text
四个 Source Commit 可以并发完成。
相同 dirty key 的父目录刷新只允许最新 version 写回。
后台可能已经启动了旧任务，但旧任务在写回前会自我淘汰。
```

允许为了时效性生成多于一次，但不应该和上传文件数线性绑定。

## 错误语义

锁冲突不是内部错误。

| 场景 | HTTP / SDK 错误码 | details | 说明 |
|------|-------------------|---------|------|
| 同路径并发写 | `CONFLICT` | `conflict_type=path_busy`, `retryable=true` | 客户端可退避重试 |
| 子路径被目录操作占用 | `CONFLICT` | `conflict_type=path_busy`, `retryable=true` | 目录 rm/mv 与 add 互斥 |
| 父目录不存在 | `NOT_FOUND` | `resource=<uri>` | 非锁问题 |
| if-match 版本不一致 | `PRECONDITION_FAILED` | `expected/current revision` | 后续持久 revision 能力 |
| 存储损坏或未知异常 | `INTERNAL` | 原始诊断信息 | 真正的服务端异常 |

当前实现先统一映射到 `409 CONFLICT`，保留 `retryable` 和 `conflict_type`，避免继续暴露泛化 `500`。

## 当前完成情况

- 已完成：`ExactPathLock(path)` 底层实现，锁文件放在父目录，能保护文件和未创建路径。
- 已完成：`ExactPathLock` 与祖先/子孙 `TreeLock` 的冲突检测。
- 已完成：`add_resource` 首次提交路径改为 `ExactPathLock(final_path)`，不再锁父目录。
- 已完成：自动重名逐个候选路径尝试 `ExactPathLock`。
- 已完成：单文件 `rm/mv` 使用 exact path 语义；目录 `rm/mv` 继续使用 tree 语义。
- 已完成：锁冲突映射为结构化 `409 CONFLICT`。
- 已完成：压测脚本和中文压测说明。
- 已完成：memory v2 锁收敛为 `ExactPathLock(固定文件)` + `TreeLock(变量 schema 目录)`，不再用父目录锁保护 fixed schema。
- 已完成：agent experience 的 `source_trajectories` 回填纳入 experience schema 锁作用域；异常 fallback 路径使用短 `ExactPathLock` 保护 read-modify-write。
- 已完成：资源父目录摘要、直接 write 刷新、memory 目录摘要使用 coalesce version，旧任务写回前自我淘汰。
- 已完成：`.abstract.md`、`.overview.md` 的后台写回使用派生文件 `ExactPathLock`。
- 未完成：持久化 `dir_revision/exact_revision`。当前先使用运行时 coalesce version，解决同进程并发刷新覆盖问题。

## 当前实施计划

### 阶段 1：ExactPathLock（已完成）

落地内容：

- 在 `PathLockEngine` 中增加 `ExactPathLock(path)`。
- 锁文件放在父目录下，形如 `.exact.ovlock.<name>.<hash>`，因此可以保护文件、目录名和未创建路径。
- 同一路径 exact lock 互斥。
- exact lock 与祖先 `TreeLock` 互斥。
- `TreeLock` 扫描子树时识别 descendant exact lock。

验收例子：

```text
add docs/a.md 和 add docs/b.md 不互斥。
add docs/a.md 和 add docs/a.md 互斥。
rm docs/ 和 add docs/a.md 互斥。
```

### 阶段 2：add_resource 提交路径（已完成）

落地内容：

- 删除 `add_resource` 主路径里的父目录点锁。
- 首次提交最终路径时只获取 `ExactPathLock(final_path)`。
- 自动重名逐个候选名尝试 exact lock，不抢目录锁。
- 源数据提交后获取资源 `TreeLock` 生命周期锁，并交给语义任务释放。
- 显式 `to` 目标已存在时，保留现有增量更新行为。

验收例子：

```text
并发 add docs/a.md、docs/b.md、docs/c.md -> 都成功或只因真实同路径冲突失败。
显式 to=docs/a.md 顺序重复执行 -> 仍能增量更新。
显式 to=docs/a.md 并发执行 -> 一个处理，另一个 409。
```

### 阶段 3：VikingFS rm/mv（已完成）

落地内容：

- 删除单文件：使用 `ExactPathLock(file)`。
- 移动单文件：同时使用 `ExactPathLock(src)` 和 `ExactPathLock(dst)`。
- 删除目录、移动目录：继续使用树级锁，并与子路径 exact lock 冲突。

验收例子：

```text
rm docs/a.md 和 add docs/b.md 可并发。
rm docs/a.md 和 add docs/a.md 互斥。
mv docs/a.md docs/b.md 与 add docs/b.md 互斥。
mv docs/ archived/docs/ 与 add docs/a.md 互斥。
```

### 阶段 4：错误映射（已完成）

落地内容：

- `ResourceBusyError` 带 `uri`、`conflict_type`、`retryable`。
- `LockAcquisitionError` 映射为结构化 `CONFLICT`。
- filesystem `mv` 路由也走统一异常映射。

验收例子：

```json
{
  "code": "CONFLICT",
  "details": {
    "uri": "viking://resources/docs/a.md",
    "conflict_type": "path_busy",
    "retryable": true
  }
}
```

### 阶段 5：memory schema 锁收敛（已完成）

落地内容：

- 固定文件名 schema 使用 `ExactPathLock(具体文件路径)`。
- 变量文件名 schema 使用 `TreeLock(schema 目录)`。
- 新增批量获取 `ExactPathLock + TreeLock` 的统一接口，按固定顺序加锁，失败时释放已获得的锁。
- `SessionCompressorV2` 不再使用父目录锁保护 fixed schema。
- agent experience 的 `source_trajectories` 回填在 experience phase 的 schema 锁内执行，和模型读取、operations apply 处于同一个锁作用域。

验收例子：

```text
profile.md -> ExactPathLock(viking://user/default/memories/profile.md)
events/* -> TreeLock(viking://user/default/memories/events)
```

### 阶段 6：Semantic Refresh 合并与派生文件写回（已完成）

落地内容：

- 相同 dirty key enqueue 时递增 `coalesce_version`。
- worker dequeue、目录语义写回前、向量化前都检查 stale。
- 旧任务只 mark done / release lifecycle lock，不写派生文件。
- `.abstract.md` 和 `.overview.md` 写回使用 `ExactPathLock(派生文件路径)`。
- resource 和 memory 的父目录摘要刷新都走同一套 coalesce 语义。

验收例子：

```text
任务 A 刷新 docs/，version=1。
任务 B 因新文件提交刷新 docs/，version=2。
任务 A 写回前检查失败，丢弃，不覆盖新 overview。
任务 B 写回 docs/.overview.md 和 docs/.abstract.md。
```

## 最终验收标准

功能：

```text
同目录并发添加不同文件，不再因为父目录锁随机 500。
```

一致性：

```text
源数据强一致。
派生语义最终一致。
旧语义任务不能覆盖新源版本的派生结果。
```

性能：

```text
批量上传 N 个文件，父目录 overview 不应稳定触发 N 次完整生成。
```

可观测性：

```text
锁冲突返回 409 CONFLICT，并携带 uri、conflict_type、retryable。
压测报告能区分真实服务错误和可重试路径冲突。
```
