# Upgrading collection lifecycle handling

`del collection` and garbage collection no longer synchronously flush indexes,
release database locks, or stop collection threads. A finalizer can execute
inside a logging handler or another critical section; waiting for the scheduler
there can deadlock.

Call `collection.close()` explicitly when finished, including **before reopening
the same persistent database path**. A `try/finally` block is recommended:

```python
collection = get_or_create_local_collection(meta_data=meta, path=path)
try:
    # Read or write through collection.
    ...
finally:
    collection.close()
```

Abandoned backends remain strongly referenced until normal lifecycle code drains
them. Service shutdown and project-group close perform this drain. Standalone
callers can use `close_unclosed_collections()` from
`openviking.storage.vectordb.collection.collection` during their shutdown. Normal
process exit has an atexit fallback. Do not call the drain from a finalizer or
while holding application locks. Prefer timely explicit close over relying on
this fallback: retained collections still consume memory, database locks, and
scheduler threads.

`GET /health` includes process-wide `abandoned_collections.pending` and
`abandoned_collections.closing` counts. Pending includes failed closes awaiting
retry; closing counts the batch currently being processed. These are diagnostic
counts and do not change the health endpoint's HTTP status. Monitor a sustained
increase as a resource ownership problem. Each nonempty drain logs how many
collections closed and how many failed, outside the registry lock.

Explicit close still waits for in-flight maintenance without a timeout. It does
not safely cancel arbitrary stuck jobs; applications must bound their jobs and
collect thread stacks if shutdown stalls. Index persistence/close failures keep
maintenance stopped and retain unfinished resources for another `close()`.
Do not resume normal operations on a handle after a failed close.

A failed abandoned-backend drain is logged without skipping independent service
cleanup. The data-directory lock is retained because failed backends may still
own live storage. Retrying service `close()` successfully drains them and releases
the lock. This prevents a second service from opening the same workspace while
failed resources are still alive.
