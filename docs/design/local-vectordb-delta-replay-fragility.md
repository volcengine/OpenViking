# Local VectorDB Delta Replay Fragility

> Incident write-up + proposed fix for `_recover` crashing on a single
> malformed delta record after a hard container shutdown.

## Symptom

After `docker compose up -d` re-created the bot container (which hard-stops
the previous one mid-traffic), the server came up and answered `/health` with
`status: ok, healthy: true`, but every authenticated endpoint returned
`HTTP 500`:

```
2026-05-07 08:21:26 - uvicorn.access - "POST /api/v1/search/find HTTP/1.1" 500
2026-05-07 08:21:27 - openviking.server.routers.system - WARNING -
  Failed to resolve identity: api_key_manager not initialized in api_key mode
```

`auth.py:325` (`raise RuntimeError("api_key_manager not initialized in api_key mode")`)
ran on every request. Health probe stayed green the whole time.

## Root cause

Two independent issues stacked:

### 1. One corrupt delta makes `_recover` unrecoverable

`PersistCollection._recover` (`openviking/storage/vectordb/collection/local_collection.py:945`)
loads the latest snapshot, then replays everything written after it from the
LevelDB delta store:

```python
# local_collection.py:984-1000
delta_list = self.store_mgr.get_delta_data_after_ts(newest_version)
upsert_list: List[DeltaRecord] = []
for data in delta_list:
    ...
    upsert_list.append(data)
    ...
if upsert_list:
    index.upsert_data(upsert_list)
```

`upsert_data` eventually calls `convert_fields_for_index`
(`storage/vectordb/utils/data_processor.py:337`), which `json.loads`-es each
record's `old_fields` blob. If **any** record's blob is truncated, the whole
batch raises and the collection refuses to come up:

```
File "data_processor.py", line 337, in convert_fields_for_index
    data = json.loads(fields_json)
json.decoder.JSONDecodeError:
    Unterminated string starting at: line 1 column 334 (char 333)
```

The corruption was introduced earlier when `docker compose up -d` SIGKILL-ed
the previous container while it was mid-write to the delta store. Snapshot
files (vector_index/scalar_index) were intact — only one entry in the LevelDB
WAL was half-written.

Rolling back to the prior snapshot **does not help**: `get_delta_data_after_ts`
filters by snapshot timestamp, so a snapshot from before the corruption
still reads the corrupt delta record from the LevelDB store. The only
in-place recovery we found was `rm -rf vectordb/`, which forces a re-embed
from AGFS source data.

### 2. Staged lifespan turns "fail to start" into "silently broken"

PR #1878 (`660835a0 fix(server): 分阶段 lifespan`) moves
`service.initialize()` and `APIKeyManager` setup into a deferred background
task so `/health` can answer during init. When the task crashes, the server
keeps running but `app.state.api_key_manager` stays `None` forever. Every
auth-protected dependency hits `auth.py:325` and 500s.

Before #1878, `_recover` exploding would have crashed the lifespan startup
and the container would have failed `docker compose up -d` visibly. After
#1878, the symptom moved to "`/health` is green and every business endpoint
returns 500" — much harder to notice from a healthcheck-driven
orchestrator.

## Proposed fixes

### A. Tolerate one corrupt delta record (high priority)

`_recover` should treat a single bad record as a recoverable warning, not a
fatal recovery failure. Pseudocode at
`local_collection.py:984` (the `for data in delta_list` loop):

```python
for data in delta_list:
    try:
        # existing per-record decode/dispatch
        upsert_list.append(data)
    except Exception as exc:
        logger.error(
            "skipping unrecoverable delta record id=%s ts=%s: %s",
            data.id, data.ts, exc,
        )
        # optional: persist a quarantine row so ops can inspect later
        continue
```

The decode currently happens deeper, inside `upsert_data` →
`_convert_delta_list_for_index` → `convert_fields_for_index`, so the
sensible boundary is to push the `json.loads` into `_recover`'s loop (per
record) so a single failure is contained, instead of bringing down the
batch.

We accept losing the corrupt record's data because it is already corrupt —
the alternative ("refuse to start") is strictly worse for a dev/prod box.

### B. Make staged-init failure visible (high priority)

Two parts:

1. **`/health` should reflect init status.** Right now `/health` returns
   `healthy: true` regardless of whether `_deferred_init` succeeded.
   Suggested behavior:

   ```
   { "status": "ok", "healthy": true,  "init": "complete"  }   # 200
   { "status": "ok", "healthy": true,  "init": "running"   }   # 200
   { "status": "degraded", "healthy": false, "init": "failed",
     "error": "<short message>" }                              # 503
   ```

   Compose's `healthcheck` would then mark the container as unhealthy
   instead of green — which surfaces the problem to the orchestrator and
   to anyone running `docker compose ps`.

2. **Auth dependency should fail with a more honest error.**
   `auth.py:325` raises `RuntimeError("api_key_manager not initialized
   in api_key mode")` and the request becomes a 500. Better: detect this
   exact condition and return `503 Service Unavailable` with
   `Retry-After: 5`, so callers can distinguish "server is initializing /
   degraded" from "server bug".

### C. Optional: snapshot-only recovery mode

When delta replay fails wholesale (B happens but corrupted record is
unsafe to skip), provide a startup flag like `--vectordb-skip-deltas` /
`OPENVIKING_VECTORDB_SKIP_DELTAS=1` that loads only the snapshot and
discards pending deltas. Strictly worse than (A) for normal operation but
useful as an emergency lever.

## Repro / forensic notes

- The exact delta record can be located by walking the LevelDB store for
  values whose `old_fields` JSON does not parse. The record we hit had
  333 bytes of UTF-8 that ended mid-multibyte character — consistent with
  a write that was truncated by SIGKILL before the OS flushed the page.
- `*.write_done` markers under `vectordb/<collection>/index/<engine>/versions/`
  are how `_recover` picks the latest snapshot. Disabling the marker
  rolls back the snapshot but **not** the delta store, so it is not a
  workaround for this class of corruption.
- AGFS data under `viking/` is independent and was not affected. After
  wiping `vectordb/`, the next call into search/retrieve re-embeds the
  affected entries from AGFS source.

## Why this needs to be on by default

Container shutdowns mid-write are routine — every redeploy, every
`docker compose restart`, every host reboot triggers it. LevelDB itself
recovers cleanly because it has its own crash-safe write path. The
fragility is at the application layer, where we serialize a JSON blob
into the delta record without atomicity guarantees and assume on read
that it is well-formed.

(A) and (B) together turn this from an outage into a logged warning
plus an unhealthy probe that the orchestrator can see.
