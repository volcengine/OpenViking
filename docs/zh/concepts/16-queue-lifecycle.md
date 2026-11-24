# 任务状态

QueueFS 通过同一份后端状态快照判断队列是否完成：只有 `pending` 和 `processing` 都为零，队列才为空。仅检查待出队消息数不能证明处理已结束。

队列为空只描述当前没有未确认的消息，不证明每个业务操作成功，也不保证之后不会入队新任务。需要确认一次导入或会话提交是否成功，请查询对应 [task](../api/17-tasks.md)，检查最终状态及错误。

## 问题

旧实现通过两个相互独立的数据源推断完成状态：

- QueueFS `/size`：返回仍可被 dequeue 的 pending 消息数。
- Python `NamedQueue._in_progress`：返回当前进程观察到的执行中任务数。

这两个值无法原子读取或更新。QueueFS 将消息从 `pending` 移到
`processing` 后，另一个线程、event loop 或进程可能同时观察到 `size == 0`，
且其读取到的 `_in_progress == 0`，从而在消息 ACK 前错误地判断队列已经完成。

将本地计数放到 task 创建之前或之后，只能缩小部分时序窗口。它无法让 backend
状态迁移与本地计数更新成为同一个原子操作，因此无法严格解决跨线程、跨 event loop
或跨进程的并发判断问题。

## ACK 生命周期

SQLite 和缓存后端实现了 ACK 生命周期：

```text
enqueue -> pending -> dequeue -> processing -> ack -> removed
```

MemoryBackend 也维护 `processing` 集合：dequeue 将消息移入该集合，ACK 再移除消息。它与其他后端使用相同的完成条件，但不提供进程重启后的持久化恢复。

## 状态模型

队列长度不是单一数字。QueueFS 维护以下当前状态指标：

| 字段 | 含义 |
| --- | --- |
| `pending` | 尚未 dequeue 的消息数，包含暂时因分组占用而不可投递的消息 |
| `processing` | 已 dequeue、尚未 ACK 的消息数 |
| `unacked` | `pending + processing` |

队列完成条件必须是：

```text
pending == 0 && processing == 0
```

等价于 `unacked == 0`。

当 handler 失败、ACK 失败或 worker 退出时，只要消息仍未 ACK，就不能视为完成。
消息可通过 `/retry` 或恢复流程重新进入 pending，或通过成功 ACK 离开队列。

## 状态归属

QueueFS 是队列生命周期状态的 Owner。各 backend 必须在 enqueue、dequeue、ACK、
retry、clear 和 recovery 操作中维护 `pending` 与 `processing`。

Python 不应再组合 backend 的 `pending` 和进程内计数来判断完成。本地 worker
计数仍可作为运行时观测指标，但它不属于队列长度，也不是完成状态的权威来源。

以下累计计数同样不属于队列长度：

- `processed`
- `requeue_count`
- `error_count`

它们描述的是处理结果，而不是当前队列占用。由处理层或指标层维护。如果需要将它们下沉到 QueueFS，必须先定义显式的处理结果协议，因为 backend
无法仅根据 dequeue 或 ACK 推断 handler 的处理结果。

## Backend 契约

QueueFS `/status` 返回同一次原子读取取得的状态：

```json
{
  "pending": 3,
  "processing": 2
}
```

`unacked` 由 `pending + processing` 派生。为保持兼容和 worker 调度语义，现有
`/size` 可以继续表示 `pending`。
QueueFS 控制文件名属于保留路径段，队列名不能以 `enqueue`、`enqueue_ext`、`dequeue`、`peek`、
`size`、`status`、`messages`、`clear`、`ack` 或 `retry` 结尾。

各后端要求：

- **SQLite：** 在同一个数据库快照中读取两个计数。
- **Cache：** 通过一个 Lua 脚本返回 `ZCARD(pending)` 和
  `ZCARD(processing)`。
- **Memory：** 维护 processing 集合；dequeue 将消息移入该集合，ACK 从该集合
  删除消息。

`NamedQueue.get_status()` 只消费 backend 返回的状态快照。`wait_complete()` 和
`is_all_complete()` 只使用 backend 维护的 `pending` 与 `processing` 判断完成。

## 内核分组调度

`/enqueue_ext` 按消息提供可选、互斥的 key，同一队列可以混用三类消息：

| 消息元数据 | 同一队列内的语义 |
| --- | --- |
| 两个 key 都没有 | 普通消息，独立投递 |
| `serial_key` | 同 key 同时只处理一条消息，最终 ACK 后才能投递下一条 |
| `group_key` | 同 key 可以并发处理多条消息，但全部归属同一个消费者实例 |

两个 key 的命名空间及不同队列相互独立，不承诺严格 FIFO。退出恢复保留原消息 ID；
`serial_key` 的原消息 ACK 前，同 key 后续消息仍阻塞。`group_key` 在最后一条
在途消息 ACK 后释放消费者归属。重复 ACK 不改变其他消息的占用。

- **Redis/Cache：** 相同 `service_name` 和队列路径下支持跨 backend 实例约束，
  消费者身份为 backend 实例，退出恢复沿用心跳机制。
- **Memory：** 支持单 backend 实例内的两种 key，退出后丢失状态。
- **SQLite：** 仅支持单消费者进程；`group_key` 保留元数据、按普通消息分发，
  `serial_key` 约束进程内并发，启动时重投未 ACK 消息。

SQLite 自动补齐可空 `serial_key` 列与索引，旧消息该列为 `NULL`；
补齐 `schedule_seq` 调度序号并按原行 ID 初始化，替换原 status 索引。
存量消息的 ID、payload 和原始顺序保持不变。
Redis 布局与旧版不兼容：升级前须排空 service 下共享队列、统一升级生产者与消费者，
不支持新旧版本混用或自动迁移积压。

扩展入队使用 `QFSE` v1 大端帧（固定头、TLV、原始 payload），header 上限 64 KiB。
`group_key` / `serial_key` 的 TLV type 为 `2` / `3`，flags 为 `1`，
key 长度为 1–4096 UTF-8 bytes；拒绝互斥字段并存、重复或无效 key、未知必选字段，
跳过未知可选字段。完整帧定义见源码树中的
`crates/ragfs/src/plugins/queuefs/enqueue_protocol.rs`。

旧 `/enqueue` 仍接收原始 payload；dequeue、peek、messages 和 ACK 格式不变。
无可投递消息时 `/dequeue` 返回 `{}`，即使 pending 非零。扩展 write 返回字节数，
UUID 由调用方持有；同队列尚存 ID 重复入队返回 `AlreadyExists`，ACK 后不保留去重记录。
`clear` 和删除队列同时清理调度状态。业务处理、合并及写回结束后才 ACK。

Python `enqueue(..., serial_key=..., group_key=...)` 在注册 Work 前校验 key，
扩展入队返回实际 UUID；无 key 时保持旧路径。ExternalTask 使用账号、任务类型和
provider 显式返回的 key 生成 `serial_key`；返回 `None` 时不启用串行调度。
同账号跨用户的相同 key 共享串行约束。
admission busy 或本地 finalization 未完成时返回 `ProcessResult.retry()`；
通用 middleware 先保存 Work 为 pending，再写 `/retry`，释放 worker 槽位。
原消息 ID、payload、时间戳和 key 不变，无 delay 调度；
同 serial key 的后续消息仍须等待原消息最终 ACK，其他 key 可继续投递。
`/retry` 只移动 processing 消息，重复 retry 和 pending ACK 均为空操作。
旧 `ProcessResult.requeued()` 仍表示 handler 已发布替代消息，原消息照常 ACK。
External 业务结果保存最多尝试三次；向外传播的执行、清理及
Task/Work 保存异常均不 ACK，已处理的业务失败在 Work 结算后 ACK。
取消沿用通用 Task middleware 契约；ACK 沿用通用队列行为，写入失败记录
warning，消息留待恢复，消费继续。本版本 ExternalTaskService 保留基线的目标
占用和执行保护，并在 worker 启动前恢复占用，以兼容未携带 key 的旧消息；
这些过渡保护计划在下个版本移除。

SessionCommit 的首次提交和恢复入队均按账号/用户生成 `group_key`，
让同用户的并发会话共用流式 Memory 批处理器。等待前一个 archive 时返回
`ProcessResult.retry()`，不发布替代消息；原消息回到该组队尾，让前一个 archive
可继续消费。RETRY 与 REQUEUED 都会累加持久化 Work 的 `requeue_count`，
该计数不设重投上限。批处理及全部合并分组结束后才返回，
失败或取消也须等待实际写回结束；停机取消保留未 ACK 消息。
取消 submit 不改变原有 count/time/close 批处理窗口；取消 flush 会传递给
批处理，并将等待项结算为取消。多个合并分组失败时，等待其他组结束后仍抛出
最先发生的异常。
