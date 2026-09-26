# 队列状态与完成语义

## 问题

当前队列完成状态由两个相互独立的数据源推断：

- QueueFS `/size`：返回仍可被 dequeue 的 pending 消息数。
- Python `NamedQueue._in_progress`：返回当前进程观察到的执行中任务数。

这两个值无法原子读取或更新。QueueFS 将消息从 `pending` 移到
`processing` 后，另一个线程、event loop 或进程可能同时观察到 `size == 0`，
且其读取到的 `_in_progress == 0`，从而在消息 ACK 前错误地判断队列已经完成。

将本地计数放到 task 创建之前或之后，只能缩小部分时序窗口。它无法让 backend
状态迁移与本地计数更新成为同一个原子操作，因此无法严格解决跨线程、跨 event loop
或跨进程的并发判断问题。

## 改动前的后端行为

SQLite 和缓存后端实现了 ACK 生命周期：

```text
enqueue -> pending -> dequeue -> processing -> ack -> removed
```

改动前，MemoryBackend 没有实现相同的生命周期。它的 `dequeue` 会直接从唯一的
队列中删除消息。虽然接口上存在 `ack` 方法，但被 dequeue 的消息已经不再保存，
后续 ACK 通常找不到可删除的消息。因此，MemoryBackend 实际上没有有效的
`processing` 状态和 ACK 生命周期。

## 状态模型

队列长度不是单一数字。QueueFS 必须维护以下当前状态指标：

| 字段 | 含义 |
| --- | --- |
| `pending` | 尚未 dequeue、可被 worker 获取的消息数 |
| `processing` | 已 dequeue、尚未 ACK 的消息数 |
| `unacked` | `pending + processing` |

队列完成条件必须是：

```text
pending == 0 && processing == 0
```

等价于 `unacked == 0`。

当 handler 失败、ACK 失败或 worker 退出时，只要消息仍未 ACK，就不能视为完成。
消息只能通过恢复流程重新进入 pending，或通过成功 ACK 离开队列。

## 状态归属

QueueFS 是队列生命周期状态的 Owner。各 backend 必须在 enqueue、dequeue、ACK、
clear 和 recovery 操作中维护 `pending` 与 `processing`。

Python 不应再组合 backend 的 `pending` 和进程内计数来判断完成。本地 worker
计数仍可作为运行时观测指标，但它不属于队列长度，也不是完成状态的权威来源。

以下累计计数同样不属于队列长度：

- `processed`
- `requeue_count`
- `error_count`

它们描述的是处理结果，而不是当前队列占用。本次修复中可以继续由处理层或指标层
维护。如果需要将它们下沉到 QueueFS，必须先定义显式的处理结果协议，因为 backend
无法仅根据 dequeue 或 ACK 推断 handler 的处理结果。

## Backend 契约

QueueFS 应提供一个原子状态操作：

```json
{
  "pending": 3,
  "processing": 2
}
```

`unacked` 由 `pending + processing` 派生。为保持兼容和 worker 调度语义，现有
`/size` 可以继续表示 `pending`。
QueueFS 控制文件名属于保留路径段，队列名不能以 `enqueue`、`dequeue`、`peek`、
`size`、`status`、`messages`、`clear` 或 `ack` 结尾。

各后端要求：

- **SQLite：** 在同一个数据库快照中读取两个计数。
- **Cache：** 通过一个 Lua 脚本返回 `LLEN(pending)` 和
  `ZCARD(processing)`。
- **Memory：** 增加 processing 集合；dequeue 将消息移入该集合，ACK 从该集合
  删除消息。

`NamedQueue.get_status()` 只消费 backend 返回的状态快照。`wait_complete()` 和
`is_all_complete()` 只使用 backend 维护的 `pending` 与 `processing` 判断完成。
