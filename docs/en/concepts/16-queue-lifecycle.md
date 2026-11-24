# Queue State and Completion Semantics

QueueFS determines completion from one backend status snapshot: a queue is empty only when both `pending` and `processing` are zero. An empty pending queue alone does not mean processing has finished.

An empty queue means no messages are currently unacknowledged. It does not prove every operation succeeded or prevent new work from arriving. To verify an import or session commit, inspect its [task](../api/17-tasks.md), including the terminal status and error.

## Problem

The previous implementation inferred completion from two independent sources:

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

## ACK Lifecycle

SQLite and cache-backed queues implement an acknowledgement lifecycle:

```text
enqueue -> pending -> dequeue -> processing -> ack -> removed
```

The memory backend also maintains a processing collection. Dequeue moves messages into it; ACK removes them. It uses the same completion condition as the other backends, but does not preserve queued work across process restarts.

## State Model

Queue length is not one scalar. QueueFS maintains these current-state
gauges:

| Field | Meaning |
| --- | --- |
| `pending` | Messages not yet dequeued, including groups temporarily blocked |
| `processing` | Messages dequeued but not yet acknowledged |
| `unacked` | `pending + processing` |

A queue is complete only when:

```text
pending == 0 && processing == 0
```

Equivalently, `unacked == 0`.

An unacknowledged message remains incomplete when its handler fails, its ACK
fails, or its worker exits. It becomes pending again through `/retry` or recovery, or leaves
the queue through a successful ACK.

## Ownership

QueueFS is the owner of queue lifecycle state. Each backend must update
`pending` and `processing` together with enqueue, dequeue, ACK, retry, clear, and
recovery operations.

Python must not combine backend `pending` with a process-local counter to decide
completion. A local worker counter may still be exposed as runtime telemetry,
but it is not queue length and is not authoritative.

The following cumulative counters are also not queue length:

- `processed`
- `requeue_count`
- `error_count`

They describe processing outcomes rather than current queue occupancy. They remain in the processing or metrics layer. Moving them into
QueueFS would require an explicit backend outcome protocol because the backend
cannot infer a handler result from dequeue or ACK alone.

## Backend Contract

QueueFS `/status` returns counts read atomically:

```json
{
  "pending": 3,
  "processing": 2
}
```

`unacked` is derived as `pending + processing`. The existing `/size` operation
can continue to mean `pending` for compatibility and worker scheduling.
QueueFS control names are reserved path components: a queue name cannot end in
`enqueue`, `enqueue_ext`, `dequeue`, `peek`, `size`, `status`, `messages`,
`clear`, `ack`, or `retry`.

Backend requirements:

- **SQLite:** read both counts from one database snapshot.
- **Cache:** return `ZCARD(pending)` and `ZCARD(processing)` from one Lua script.
- **Memory:** maintain a processing collection; dequeue moves a message into it and
  ACK removes the message from it.

`NamedQueue.get_status()` consumes this backend snapshot. `wait_complete()` and
`is_all_complete()` use only the backend-owned `pending` and `processing`
values.

## Grouped Scheduling in the Kernel

`/enqueue_ext` accepts optional, mutually exclusive keys per message. All three
kinds may coexist in one queue:

| Header metadata | Contract within one queue |
| --- | --- |
| Neither key | Ordinary independent delivery |
| `serial_key` | Only one message of this key may be processing; its final ACK permits the next message |
| `group_key` | Several messages of this key may be processing concurrently, all belonging to one consumer instance |

Key namespaces and queues are independent; strict FIFO is not promised.
Recovery preserves the original message ID. Later messages of a `serial_key`
remain blocked until the original is ACKed. A `group_key` releases consumer
ownership after its last in-flight ACK. Duplicate ACKs do not affect other claims.

- **Redis/Cache:** enforces both keys across backend instances sharing a
  `service_name` and queue path; consumer identity is a backend instance.
  Exit recovery uses the existing heartbeat mechanism.
- **Memory:** supports both keys within one backend instance; state is lost on exit.
- **SQLite:** supports one consumer process. `group_key` is retained as metadata
  and delivered normally; `serial_key` excludes concurrent deliveries within
  that process. Startup replays unacknowledged messages.

SQLite automatically adds a nullable `serial_key` column and its index. Old
messages have `NULL` in that column. A `schedule_seq` column is backfilled from
row IDs and replaces the status index's ordering column; IDs, payloads and
original ordering remain unchanged. Redis layout is incompatible with older versions:
drain shared queues under the service and upgrade producers and consumers
together. Mixed versions and automatic backlog migration are unsupported.

Extended enqueue uses a big-endian `QFSE` v1 frame (fixed header, TLVs, raw
payload), with a 64 KiB header limit. `group_key` / `serial_key` use TLV types
`2` / `3`, flags `1`, and 1–4096 UTF-8 bytes. Both keys together, duplicate or
invalid keys, and unknown required fields are rejected; unknown optional fields
are skipped. The complete frame definition is in
`crates/ragfs/src/plugins/queuefs/enqueue_protocol.rs` in the source tree.

Legacy `/enqueue` still accepts raw payload; dequeue, peek, messages and ACK
formats are unchanged. `/dequeue` returns `{}` when nothing is eligible, even
with pending messages. Extended write returns a byte count; the caller owns the
UUID. An ID still present in the same queue returns `AlreadyExists`; deduplication
records are not retained after ACK. `clear` and queue removal also clear scheduling
state. ACK after processing, batching and writeback finish.

Python `enqueue(..., serial_key=..., group_key=...)` validates keys before Work
registration and returns the actual UUID for extended writes; unkeyed writes
keep the legacy path. ExternalTask derives `serial_key` from the account, task
type and explicit provider key. A provider returning `None` uses ordinary
delivery without serialization.
Users in one account share provider key serialization. Pending admission or local
finalization returns `ProcessResult.retry()`. Generic middleware persists Work as
pending before writing `/retry`, releasing the worker slot. The original ID,
payload, timestamp and keys remain unchanged, with no delayed scheduling.
Later messages of the same serial key still wait for its final ACK; other keys
remain eligible. `/retry` only moves processing messages; duplicate retry and
pending ACK are no-ops. Existing `ProcessResult.requeued()` means the handler
already published a replacement and the original delivery is ACKed.
External outcome saves allow three attempts. Escaped execution,
cleanup or Task/Work persistence errors leave the delivery unacknowledged; handled
business failures are ACKed after Work settlement. Cancellation uses the common
Task middleware contract. ACK uses the common queue behavior: a failed write logs
a warning and leaves the delivery for recovery while consumption continues.
For this release, ExternalTaskService retains its baseline target reservations
and execution guard for pre-key deliveries, restoring reservations before workers
start. These transitional guards are scheduled for removal in the next release.

SessionCommit uses an account/user `group_key` on initial and recovery enqueue,
allowing concurrent sessions to share the streaming-memory batcher. Waiting for
a predecessor archive returns `ProcessResult.retry()` without publishing a
replacement. The original message returns to its group tail, allowing the
predecessor to continue. RETRY and REQUEUED both increment the persisted Work
`requeue_count`; this counter has no retry limit.
Batch work and all merge groups finish before processing returns, including on
failure or cancellation; shutdown leaves the message unacknowledged.
Submit cancellation keeps the existing count/time/close batching window.
Cancelling a flush propagates to batch processing and settles its waiting items
as cancelled. Multiple group failures preserve the first observed exception
after the remaining groups finish.
