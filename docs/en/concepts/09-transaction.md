# Path Locks and Crash Recovery

OpenViking uses two simple primitives — **path locks** and **redo log** — to protect the consistency of core write operations (`rm`, `mv`, `add_resource`, `session.commit`), ensuring that VikingFS, VectorDB, and QueueManager remain consistent even when failures occur.

## Design Philosophy

OpenViking is a context database where FS is the source of truth and VectorDB is a derived index. A lost index can be rebuilt from source data, but lost source data is unrecoverable. Therefore:

> **Better to miss a search result than to return a bad one.**

## Design Principles

1. **Write-exclusive**: Path locks ensure only one write operation can operate on a path at a time
2. **On by default**: All data operations automatically acquire locks; no extra configuration needed
3. **Lock as protection**: LockContext acquires locks on entry, releases on exit — no undo/journal/commit semantics
4. **Only session_memory needs crash recovery**: RedoLog re-executes memory extraction after a process crash
5. **Queue operations run outside locks**: SemanticQueue/EmbeddingQueue enqueue operations are idempotent and retriable

## Architecture

```
Service Layer (rm / mv / add_resource / session.commit)
    |
    v
+--[LockContext async context manager]--+
|                                       |
|  1. Create LockHandle                 |
|  2. Acquire path lock (poll+timeout)  |
|  3. Execute operations (FS+VectorDB)  |
|  4. Release lock                      |
|                                       |
|  On exception: auto-release lock,     |
|  exception propagates unchanged       |
+---------------------------------------+
    |
    v
Storage Layer (VikingFS, VectorDB, QueueManager)
```

## Two Core Components

### Component 1: PathLock + LockManager + LockContext (Path Lock System)

**PathLock** implements file-based distributed locks with two lock types — POINT and SUBTREE — using fencing tokens to prevent TOCTOU races and automatic stale lock detection and cleanup.

**LockHandle** is a lightweight lock holder token:

```python
@dataclass
class LockHandle:
    id: str          # Unique ID used to generate fencing tokens
    locks: list[str] # Acquired lock file paths
    created_at: float # Creation time
```

**LockManager** is a global singleton managing lock lifecycle:
- Creates/releases LockHandles
- Background cleanup of leaked locks (in-process safety net)
- Executes RedoLog recovery on startup

**LockContext** is an async context manager encapsulating the lock/unlock lifecycle:

```python
from openviking.storage.transaction import LockContext, get_lock_manager

async with LockContext(get_lock_manager(), [path], lock_mode="point") as handle:
    # Perform operations under lock protection
    ...
# Lock automatically released on exit (including exceptions)
```

### Component 2: RedoLog (Crash Recovery)

Used only for the memory extraction phase of `session.commit`. Writes a marker before the operation, deletes it after success, and scans for leftover markers on startup to redo.

```
/local/_system/redo/{task_id}/redo.json
```

Memory extraction is idempotent — re-extracting from the same archive produces the same result.

## Consistency Issues and Solutions

### rm(uri)

| Problem | Solution |
|---------|----------|
| Delete file first, then index -> file gone but index remains -> search returns non-existent file | **Reverse order**: delete index first, then file. Index deletion failure -> both file and index intact |

**Locking strategy** (depends on target type):
- Deleting a **directory**: `lock_mode="subtree"`, locks the directory itself
- Deleting a **file**: `lock_mode="point"`, locks the file's parent directory

Operation flow:

```
1. Check whether target is a directory or file, choose lock mode
2. Acquire lock
3. Delete VectorDB index -> immediately invisible to search
4. Delete FS file
5. Release lock
```

VectorDB deletion fails -> exception thrown, lock auto-released, file and index both intact. FS deletion fails -> VectorDB already deleted but file remains, retry is safe.

### mv(old_uri, new_uri)

| Problem | Solution |
|---------|----------|
| File moved to new path but index points to old path -> search returns old path (doesn't exist) | Copy first then update index; clean up copy on failure |

**Locking strategy** (handled automatically via `lock_mode="mv"`):
- Moving a **directory**: SUBTREE lock on both source path and destination parent
- Moving a **file**: POINT lock on both source's parent and destination parent

Operation flow:

```
1. Check whether source is a directory or file, set src_is_dir
2. Acquire mv lock (internally chooses SUBTREE or POINT based on src_is_dir)
3. Copy to new location (source still intact, safe)
4. If directory, remove the lock file carried over by cp into the copy
5. Update VectorDB URIs
   - Failure -> clean up copy, source and old index intact, consistent state
6. Delete source
7. Release lock
```

### add_resource

| Problem | Solution |
|---------|----------|
| File moved from temp to final directory, then crash -> file exists but never searchable | Two separate paths for first-time add vs incremental update |
| Resource already on disk but rm deletes it while semantic processing / vectorization is still running -> wasted work | Lifecycle SUBTREE lock held from finalization through processing completion |

**First-time add** (target does not exist) — handled in `ResourceProcessor.process_resource` Phase 3.5:

```
1. Acquire POINT lock on parent of final_uri
2. agfs.mv temp directory -> final location
3. Acquire SUBTREE lock on final_uri (inside POINT lock, eliminating race window)
4. Release POINT lock
5. Clean up temp directory
6. Enqueue SemanticMsg(lifecycle_lock_handle_id=...) -> DAG runs on final
7. DAG starts lock refresh loop (refreshes timestamp every lock_expire/2 seconds)
8. DAG complete + all embeddings done -> release SUBTREE lock
```

During this period, `rm` attempting to acquire a SUBTREE lock on the same path will fail with `ResourceBusyError`.

**Incremental update** (target already exists) — temp stays in place:

```
1. Acquire SUBTREE lock on target_uri (protect existing resource)
2. Enqueue SemanticMsg(uri=temp, target_uri=final, lifecycle_lock_handle_id=...)
3. DAG runs on temp, lock refresh loop active
4. DAG completion triggers sync_diff_callback or move_temp_to_target_callback
5. Callback completes -> release SUBTREE lock
```

Note: DAG callbacks do NOT wrap operations in an outer lock. Each `VikingFS.rm` and `VikingFS.mv` has its own lock internally. An outer lock would conflict with these inner locks causing deadlock.

**Server restart recovery**: SemanticMsg is persisted in QueueFS. On restart, `SemanticProcessor` detects that the `lifecycle_lock_handle_id` handle is missing from the in-memory LockManager and re-acquires a SUBTREE lock.

### session.commit()

| Problem | Solution |
|---------|----------|
| Messages cleared but archive not written -> conversation data lost | Phase 1 without lock (incomplete archive has no side effects) + Phase 2 with RedoLog |

LLM calls have unpredictable latency (5s~60s+) and cannot be inside a lock-holding operation. The design splits into two phases:

```
Phase 1 — Archive (no lock):
  1. Generate archive summary (LLM)
  2. Write archive (history/archive_N/messages.jsonl + summaries)
  3. Clear messages.jsonl
  4. Clear in-memory message list

Phase 2 — Memory extraction + write (RedoLog):
  1. Write redo marker (archive_uri, session_uri, user identity)
  2. Extract memories from archived messages (LLM)
  3. Write current message state
  4. Write relations
  5. Directly enqueue SemanticQueue
  6. Delete redo marker
```

**Crash recovery analysis**:

| Crash point | State | Recovery action |
|------------|-------|----------------|
| During Phase 1 archive write | No marker | Incomplete archive; next commit scans history/ for index, unaffected |
| Phase 1 archive complete but messages not cleared | No marker | Archive complete + messages still present = redundant but safe |
| During Phase 2 memory extraction/write | Redo marker exists | On startup: redo extraction + write + enqueue from archive |
| Phase 2 complete | Redo marker deleted | No recovery needed |

## LockContext

`LockContext` is an **async** context manager that encapsulates lock acquisition and release:

```python
from openviking.storage.transaction import LockContext, get_lock_manager

lock_manager = get_lock_manager()

# Point lock (write operations, semantic processing)
async with LockContext(lock_manager, [path], lock_mode="point"):
    # Perform operations...
    pass

# Subtree lock (delete operations)
async with LockContext(lock_manager, [path], lock_mode="subtree"):
    # Perform operations...
    pass

# MV lock (move operations)
async with LockContext(lock_manager, [src], lock_mode="mv", mv_dst_parent_path=dst):
    # Perform operations...
    pass
```

**Lock modes**:

| lock_mode | Use case | Behavior |
|-----------|----------|----------|
| `point` | Write operations, semantic processing | Lock the specified path; conflicts with any lock on the same path and any SUBTREE lock on ancestors |
| `subtree` | Delete operations | Lock the subtree root; conflicts with any lock on the same path, any lock on descendants, and any SUBTREE lock on ancestors |
| `mv` | Move operations | Directory move: SUBTREE lock on both source and destination; File move: POINT lock on source parent and destination (controlled by `src_is_dir`) |

**Exception handling**: `__aexit__` always releases locks and does not swallow exceptions. Lock acquisition failure raises `LockAcquisitionError`.

## Lock Types (POINT vs SUBTREE)

The lock mechanism uses two lock types to handle different conflict patterns:

| | POINT on same path | SUBTREE on same path | POINT on descendant | SUBTREE on ancestor |
|---|---|---|---|---|
| **POINT** | Conflict | Conflict | — | Conflict |
| **SUBTREE** | Conflict | Conflict | Conflict | Conflict |

- **POINT (P)**: Used for write and semantic-processing operations. Only locks a single directory. Blocks if any ancestor holds a SUBTREE lock.
- **SUBTREE (S)**: Used for rm and mv operations. Logically covers the entire subtree but only writes **one lock file** at the root. Before acquiring, scans all descendants and ancestor directories for conflicting locks.

## Lock Mechanism

### Lock Protocol

Lock file path: `{path}/.path.ovlock`

Lock file content (Fencing Token):
```
{handle_id}:{time_ns}:{lock_type}
```

Where `lock_type` is `P` (POINT) or `S` (SUBTREE).

### Lock Acquisition (POINT mode)

```
loop until timeout (poll interval: 200ms):
    1. Check target directory exists
    2. Check if target directory is locked by another operation
       - Stale lock? -> remove and retry
       - Active lock? -> wait
    3. Check all ancestor directories for SUBTREE locks
       - Stale lock? -> remove and retry
       - Active lock? -> wait
    4. Write POINT (P) lock file
    5. TOCTOU double-check: re-scan ancestors for SUBTREE locks
       - Conflict found: compare (timestamp, handle_id)
       - Later one (larger timestamp/handle_id) backs off (removes own lock) to prevent livelock
       - Wait and retry
    6. Verify lock file ownership (fencing token matches)
    7. Success

Timeout (default 0 = no-wait) raises LockAcquisitionError
```

### Lock Acquisition (SUBTREE mode)

```
loop until timeout (poll interval: 200ms):
    1. Check target directory exists
    2. Check if target directory is locked by another operation
       - Stale lock? -> remove and retry
       - Active lock? -> wait
    3. Check all ancestor directories for SUBTREE locks
       - Stale lock? -> remove and retry
       - Active lock? -> wait
    4. Scan all descendant directories for any locks by other operations
       - Stale lock? -> remove and retry
       - Active lock? -> wait
    5. Write SUBTREE (S) lock file (only one file, at the root path)
    6. TOCTOU double-check: re-scan descendants and ancestors
       - Conflict found: compare (timestamp, handle_id)
       - Later one (larger timestamp/handle_id) backs off (removes own lock) to prevent livelock
       - Wait and retry
    7. Verify lock file ownership (fencing token matches)
    8. Success

Timeout (default 0 = no-wait) raises LockAcquisitionError
```

### Lock Expiry Cleanup

**Stale lock detection**: PathLock checks the fencing token timestamp. Locks older than `lock_expire` (default 300s) are considered stale and are removed automatically during acquisition.

**In-process cleanup**: LockManager checks active LockHandles every 60 seconds. Handles created more than 3600 seconds ago are force-released.

**Orphan locks**: Lock files left behind after a process crash are automatically removed via stale lock detection when any operation next attempts to acquire a lock on the same path.

## Crash Recovery

`LockManager.start()` automatically scans for leftover markers in `/local/_system/redo/` on startup:

| Scenario | Recovery action |
|----------|----------------|
| session_memory extraction crash | Redo memory extraction + write + enqueue from archive |
| Crash while holding lock | Lock file remains in AGFS; stale detection auto-cleans on next acquisition (default 300s expiry) |
| Crash after enqueue, before worker processes | QueueFS SQLite persistence; worker auto-pulls after restart |
| Orphan index | Cleaned on L2 on-demand load |

### Defense Summary

| Failure scenario | Defense | Recovery timing |
|-----------------|--------|-----------------|
| Crash during operation | Lock auto-expires + stale detection | Next acquisition of same path lock |
| Crash during add_resource semantic processing | Lifecycle lock expires + SemanticProcessor re-acquires on restart | Worker restart |
| Crash during session.commit Phase 2 | RedoLog marker + redo | On restart |
| Crash after enqueue, before worker | QueueFS SQLite persistence | Worker restart |
| Orphan index | L2 on-demand load cleanup | When user accesses |

## Configuration

Path locks are enabled by default with no extra configuration needed. **The default behavior is no-wait**: if the path is locked, `LockAcquisitionError` is raised immediately. To allow wait/retry, configure the `storage.transaction` section:

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

| Parameter | Type | Description | Default |
|-----------|------|-------------|---------|
| `lock_timeout` | float | Lock acquisition timeout (seconds). `0` = fail immediately if locked (default). `> 0` = wait/retry up to this many seconds. | `0.0` |
| `lock_expire` | float | Stale lock expiry threshold (seconds). Locks held longer than this by a crashed process are force-released. | `300.0` |

### QueueFS Persistence

The lock mechanism relies on QueueFS using the SQLite backend to ensure enqueued tasks survive process restarts. This is the default configuration and requires no manual setup.

## Related Documentation

- [Architecture](./01-architecture.md) - System architecture overview
- [Storage](./05-storage.md) - AGFS and vector store
- [Session Management](./08-session.md) - Session and memory management
- [Configuration](../guides/01-configuration.md) - Configuration reference
