# Queue State and Completion Semantics

## Problem

Queue completion is currently inferred from two independent sources:

- QueueFS `/size`, which reports messages still pending for dequeue.
- Python `NamedQueue._in_progress`, which reports work observed by the current
  process.

These values cannot be read or updated atomically. After QueueFS moves a message
from `pending` to `processing`, another thread, event loop, or process can
observe `size == 0` while the `_in_progress` value it reads is zero. It may
therefore report the queue as complete before the message is acknowledged.

Moving the local counter update before or after task creation only narrows some
timing windows. It cannot make the backend state transition and local counter
update one atomic operation, so it does not strictly solve the problem across
threads, event loops, or processes.

## Previous Backend Behavior

SQLite and cache-backed queues implement an acknowledgement lifecycle:

```text
enqueue -> pending -> dequeue -> processing -> ack -> removed
```

Before this change, the memory backend did not implement the same lifecycle.
Its `dequeue` operation removed the message from its only queue immediately.
Although the backend exposed an `ack` method, the dequeued message was no
longer stored, so a later ACK normally had nothing to remove. The memory
backend therefore had no effective `processing` state or ACK lifecycle.

## State Model

Queue length is not one scalar. QueueFS must maintain these current-state
gauges:

| Field | Meaning |
| --- | --- |
| `pending` | Messages available for dequeue |
| `processing` | Messages dequeued but not yet acknowledged |
| `unacked` | `pending + processing` |

A queue is complete only when:

```text
pending == 0 && processing == 0
```

Equivalently, `unacked == 0`.

An unacknowledged message remains incomplete when its handler fails, its ACK
fails, or its worker exits. It becomes pending again through recovery or leaves
the queue through a successful ACK.

## Ownership

QueueFS is the owner of queue lifecycle state. Each backend must update
`pending` and `processing` together with enqueue, dequeue, ACK, clear, and
recovery operations.

Python must not combine backend `pending` with a process-local counter to decide
completion. A local worker counter may still be exposed as runtime telemetry,
but it is not queue length and is not authoritative.

The following cumulative counters are also not queue length:

- `processed`
- `requeue_count`
- `error_count`

They describe processing outcomes rather than current queue occupancy. They can
remain in the processing or metrics layer for this fix. Moving them into
QueueFS would require an explicit backend outcome protocol because the backend
cannot infer a handler result from dequeue or ACK alone.

## Backend Contract

QueueFS should expose one atomic status operation:

```json
{
  "pending": 3,
  "processing": 2
}
```

`unacked` is derived as `pending + processing`. The existing `/size` operation
can continue to mean `pending` for compatibility and worker scheduling.
QueueFS control names are reserved path components: a queue name cannot end in
`enqueue`, `dequeue`, `peek`, `size`, `status`, `messages`, `clear`, or `ack`.

Backend requirements:

- **SQLite:** read both counts from one database snapshot.
- **Cache:** return `LLEN(pending)` and `ZCARD(processing)` from one Lua script.
- **Memory:** add a processing collection; dequeue moves a message into it and
  ACK removes the message from it.

`NamedQueue.get_status()` consumes this backend snapshot. `wait_complete()` and
`is_all_complete()` use only the backend-owned `pending` and `processing`
values.
