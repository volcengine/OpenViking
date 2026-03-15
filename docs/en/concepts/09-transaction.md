# Transaction Mechanism

OpenViking's transaction mechanism protects the consistency of core write operations (`rm`, `mv`, `add_resource`, `session.commit`), ensuring that VikingFS, VectorDB, and QueueManager remain consistent even when failures occur.

## Design Philosophy

OpenViking is a context database where FS is the source of truth and VectorDB is a derived index. A lost index can be rebuilt from source data, but lost source data is unrecoverable. Therefore:

> **Better to miss a search result than to return a bad one.**

## Design Principles

1. **Transactions cover synchronous operations only**: FS + VectorDB operations run inside transactions; SemanticQueue/EmbeddingQueue enqueue runs after commit (as post_actions) — they are idempotent and retriable
2. **On by default**: All data operations automatically use transactions; no extra configuration needed
3. **Write-exclusive**: Path locks ensure only one write transaction can operate on a path at a time
4. **Undo Log model**: Record reverse operations before each change; replay them in reverse order on failure
5. **Persistent journal**: Each transaction writes a journal file to AGFS for crash recovery

## Architecture

```
Service Layer (rm / mv / add_resource / session.commit)
    |
    v
+--[TransactionContext async context manager]--+
|                                              |
|  1. Create transaction + write journal       |
|  2. Acquire path lock (poll + timeout)       |
|  3. Execute operations (FS + VectorDB)       |
|  4. Record Undo Log (mark completed)         |
|  5. Commit / Rollback                        |
|  6. Execute post_actions (enqueue etc)       |
|  7. Release lock + clean up journal          |
|                                              |
|  On exception: reverse Undo Log + unlock     |
+----------------------------------------------+
    |
    v
Storage Layer (VikingFS, VectorDB, QueueManager)
```

## Consistency Issues and Solutions

### rm(uri)

| Problem | Solution |
|---------|----------|
| Delete file first, then index -> file gone but index remains -> search returns non-existent file | **Reverse order**: delete index first, then file. Index deletion failure -> both file and index intact |

Transaction flow:

```
1. Begin transaction, acquire lock (lock_mode="subtree")
2. Snapshot VectorDB records (for rollback recovery)
3. Delete VectorDB index -> immediately invisible to search
4. Delete FS file
5. Commit -> release lock -> delete journal
```

Rollback: Step 4 fails -> restore VectorDB records from snapshot.

### mv(old_uri, new_uri)

| Problem | Solution |
|---------|----------|
| File moved to new path but index points to old path -> search returns old path (doesn't exist) | Transaction wrapper; rollback on failure |

Transaction flow:

```
1. Begin transaction, acquire lock (lock_mode="mv", SUBTREE on source + POINT on destination)
2. Move FS file
3. Update VectorDB URIs
4. Commit -> release lock -> delete journal
```

Rollback: Step 3 fails -> move file back to original location.

### add_resource (TreeBuilder.finalize_from_temp)

| Problem | Solution |
|---------|----------|
| File moved from temp to final directory, then crash -> file exists but never searchable | Transaction wrapper for mv + post_action protects enqueue |

Transaction flow:

```
1. Begin transaction, lock final_uri (lock_mode="point")
2. mv temp directory -> final location
3. Register post_action: enqueue SemanticQueue
4. Commit -> execute post_action -> release lock -> delete journal
```

Crash recovery: Journal records the post_action; replayed automatically on restart.

### session.commit()

| Problem | Solution |
|---------|----------|
| Messages cleared but archive not written -> conversation data lost | Split into two transactions + checkpoint |

LLM calls have unpredictable latency (5s~60s+), so they cannot be inside a transaction. Split into:

```
Transaction 1 (Archive):
  1. Write archive (history/archive_N/messages.jsonl + summaries)
  2. Clear messages.jsonl
  3. Write checkpoint (status="archived")
  4. Commit

LLM call (no transaction):
  Extract memories from archived messages

Transaction 2 (Memory write):
  1. Write memory files
  2. Write relations
  3. Update checkpoint (status="completed")
  4. Register post_action: enqueue SemanticQueue
  5. Commit
```

Crash recovery: Read checkpoint, resume from the appropriate step based on status.

## TransactionContext

`TransactionContext` is an **async** context manager that encapsulates the full transaction lifecycle:

```python
from openviking.storage.transaction import TransactionContext, get_transaction_manager

tx_manager = get_transaction_manager()

async with TransactionContext(tx_manager, "rm", [path], lock_mode="subtree") as tx:
    # Record undo (call before making changes)
    seq = tx.record_undo("vectordb_delete", {"record_ids": ids, "records_snapshot": snapshot})
    # Execute change
    delete_from_vector_store(uris)
    # Mark completed
    tx.mark_completed(seq)

    # Register post-commit action (optional)
    tx.add_post_action("enqueue_semantic", {"uri": uri, ...})

    # Commit
    await tx.commit()
# Auto-rollback if commit() not called
```

**Lock modes**:

| lock_mode | Use case | Behavior |
|-----------|----------|----------|
| `point` | Write operations | Lock the specified path; conflicts with any lock on the same path and any SUBTREE lock on ancestors |
| `subtree` | Delete operations | Lock the subtree root; conflicts with any lock on the same path and any lock on descendants |
| `mv` | Move operations | Acquire SUBTREE lock on source path, then POINT lock on destination path |

## Lock Types (POINT vs SUBTREE)

The lock mechanism uses two lock types to handle different conflict patterns:

| | POINT on same path | SUBTREE on same path | POINT on descendant | SUBTREE on ancestor |
|---|---|---|---|---|
| **POINT** | Conflict | Conflict | — | Conflict |
| **SUBTREE** | Conflict | Conflict | Conflict | — |

- **POINT (P)**: Used for write and semantic-processing operations. Only locks a single directory. Blocks if any ancestor holds a SUBTREE lock.
- **SUBTREE (S)**: Used for rm and mv-source operations. Logically covers the entire subtree but only writes **one lock file** at the root. Before acquiring, scans all descendants for conflicting locks.

## Undo Log

Each transaction maintains an Undo Log recording the reverse action for each step:

| op_type | Forward operation | Rollback action |
|---------|-------------------|-----------------|
| `fs_mv` | Move file | Move back |
| `fs_rm` | Delete file | Skip (irreversible; rm is always the last step by design) |
| `fs_write_new` | Create new file/directory | Delete |
| `fs_mkdir` | Create directory | Delete |
| `vectordb_delete` | Delete index records | Restore from snapshot |
| `vectordb_upsert` | Insert index records | Delete |
| `vectordb_update_uri` | Update URI | Restore old value |

Rollback rules: Only entries with `completed=True` are rolled back, in **reverse order**. Each step has independent try-catch (best-effort). During crash recovery, `recover_all=True` also reverses uncompleted entries to clean up partial operations.

## Lock Mechanism

### Lock Protocol

Lock file path: `{path}/.path.ovlock`

Lock file content (Fencing Token):
```
{transaction_id}:{time_ns}:{lock_type}
```

Where `lock_type` is `P` (POINT) or `S` (SUBTREE).

### Lock Acquisition (POINT mode)

```
loop until timeout (poll interval: 200ms):
    1. Check target directory exists
    2. Check if target directory is locked by another transaction
       - Stale lock? -> remove and retry
       - Active lock? -> wait
    3. Check all ancestor directories for SUBTREE locks
       - Stale lock? -> remove and retry
       - Active lock? -> wait
    4. Write POINT (P) lock file
    5. TOCTOU double-check: re-scan ancestors for SUBTREE locks
       - Conflict found: compare (timestamp, tx_id)
       - Later one (larger timestamp/tx_id) backs off (removes own lock) to prevent livelock
       - Wait and retry
    6. Verify lock file ownership (fencing token matches)
    7. Success

Timeout (default 0 = no-wait) raises LockAcquisitionError
```

### Lock Acquisition (SUBTREE mode)

```
loop until timeout (poll interval: 200ms):
    1. Check target directory exists
    2. Check if target directory is locked by another transaction
    3. Scan all descendant directories for any locks by other transactions
    4. Write SUBTREE (S) lock file (only one file, at the root path)
    5. TOCTOU double-check: re-scan descendants for new locks
       - Conflict found: later one backs off (livelock prevention)
    6. Verify lock file ownership
    7. Success
```

### Lock Expiry Cleanup

**Stale lock detection**: PathLock checks the fencing token timestamp. Locks older than `lock_expire` (default 300s) are considered stale and are removed automatically during acquisition.

**Transaction timeout**: TransactionManager checks active transactions every 60 seconds. Transactions with `updated_at` exceeding the transaction timeout (default 3600s) are rolled back.

## Transaction Journal

Each transaction persists a journal in AGFS:

```
/local/_system/transactions/{tx_id}/journal.json
```

Contains: transaction ID, status, lock paths, init_info, undo_log, post_actions.

### Lifecycle

```
Create transaction -> write journal (INIT)
Acquire lock       -> update journal (AQUIRE -> EXEC)
Execute changes    -> update journal per step (mark undo entry completed)
Commit             -> update journal (COMMIT + post_actions)
                   -> execute post_actions -> release locks -> delete journal
Rollback           -> execute undo log -> release locks -> delete journal
```

## Crash Recovery

`TransactionManager.start()` automatically scans for residual journals on startup:

| Journal status at crash | Recovery action |
|------------------------|----------------|
| `COMMIT` + non-empty post_actions | Replay post_actions -> release locks -> delete journal |
| `COMMIT` + empty post_actions / `RELEASED` | Release locks -> delete journal |
| `EXEC` / `FAIL` / `RELEASING` | Execute undo log rollback (`recover_all=True`) -> release locks -> delete journal |
| `INIT` / `AQUIRE` | Clean up orphan locks (using init_info.lock_paths) -> delete journal (no changes were made) |

### Defense Summary

| Failure scenario | Defense | Recovery timing |
|-----------------|--------|-----------------|
| Crash during transaction | Journal + undo log rollback | On restart |
| Crash after commit, before enqueue | Journal post_actions replay | On restart |
| Crash after enqueue, before worker processes | QueueFS SQLite persistence | Worker auto-pulls after restart |
| Crash during session.commit LLM call | Checkpoint file recovery | On restart, re-invoke LLM |
| Orphan index | Cleaned on L2 on-demand load | When user accesses |
| Crash between lock creation and journal update | init_info records intended lock paths; recovery checks and cleans orphan locks | On restart |

## Transaction State Machine

```
INIT -> AQUIRE -> EXEC -> COMMIT -> RELEASING -> RELEASED
                    |
                   FAIL -> RELEASING -> RELEASED
```

- `INIT`: Transaction created, waiting for lock
- `AQUIRE`: Acquiring lock
- `EXEC`: Transaction operations executing
- `COMMIT`: Committed, post_actions may be pending
- `FAIL`: Execution failed, entering rollback
- `RELEASING`: Releasing locks
- `RELEASED`: Locks released, transaction complete

## Configuration

The transaction mechanism is enabled by default with no extra configuration needed. **The default behavior is no-wait**: if the path is locked, `LockAcquisitionError` is raised immediately. To allow wait/retry, configure the `storage.transaction` section:

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

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `lock_timeout` | float | Lock acquisition timeout (seconds). `0` = fail immediately if locked (default). `> 0` = wait/retry up to this many seconds. | `0.0` |
| `lock_expire` | float | Stale lock expiry threshold (seconds). Locks held longer than this by a crashed process are force-released. | `300.0` |
| `max_parallel_locks` | int | Max parallel locks for rm/mv operations | `8` |

### QueueFS Persistence

The transaction mechanism relies on QueueFS using the SQLite backend to ensure enqueued tasks survive process restarts. This is the default configuration and requires no manual setup.

## Related Documentation

- [Architecture](./01-architecture.md) - System architecture overview
- [Storage](./05-storage.md) - AGFS and vector store
- [Session Management](./08-session.md) - Session and memory management
- [Configuration](../guides/01-configuration.md) - Configuration reference
