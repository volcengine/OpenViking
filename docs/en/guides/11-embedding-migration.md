# Embedding Model Blue-Green Migration

OpenViking supports migrating the embedding model from one version to another without service interruption. The migration is driven by a REST API, with reads and writes available throughout the process, and every phase supports safe rollback.

## Prerequisites

### 1. Server Version

Requires OpenViking `0.2.x` or later.

### 2. Multi-Embedder Configuration

Configure the `embeddings` field in `ov.conf`, naming each embedding model version:

```json
{
  "embedding": {
    "dense": {
      "provider": "volcengine",
      "model": "doubao-embedding-v1",
      "dimension": 1024,
      "api_key": "your-api-key",
      "api_base": "https://ark.cn-beijing.volces.com/api/v3"
    },
    "max_concurrent": 10
  },
  "embeddings": {
    "v1": {
      "dense": {
        "provider": "volcengine",
        "model": "doubao-embedding-v1",
        "dimension": 1024,
        "api_key": "your-api-key",
        "api_base": "https://ark.cn-beijing.volces.com/api/v3"
      },
      "max_concurrent": 10
    },
    "v2": {
      "dense": {
        "provider": "volcengine",
        "model": "doubao-embedding-v2",
        "dimension": 2048,
        "api_key": "your-api-key",
        "api_base": "https://ark.cn-beijing.volces.com/api/v3"
      },
      "max_concurrent": 8
    }
  }
}
```

**Key points**:

- The `embedding` field is retained for backward compatibility. Used when `embeddings` is empty.
- `embeddings` is a dict keyed by version name (e.g., `v1`, `v2`), each value is a full `EmbeddingConfig`.
- Each version config must include a `dense` sub-field (same structure as the `embedding` field).
- On first `embeddings` setup, the system auto-creates a migration state file with the current active config as `default`.

### 3. Authentication

All migration write operations (`/start`, `/build`, `/switch`, `/disable-dual-write`, `/finish`, `/abort`, `/rollback`) require **admin** or **root** role. Read operations (`/status`, `/targets`) require any authenticated identity.

```bash
# Include API Key in requests
curl -H "X-API-Key: your-admin-key" ...
```

## Migration Workflow

### Step 1: List Available Targets

```bash
curl -H "X-API-Key: your-admin-key" \
  http://localhost:1933/api/v1/migration/targets
```

Returns available target embedder configurations:

```json
{
  "status": "ok",
  "result": {
    "targets": [
      { "name": "v1", "provider": "volcengine", "model": "doubao-embedding-v1", "dimension": 1024 },
      { "name": "v2", "provider": "volcengine", "model": "doubao-embedding-v2", "dimension": 2048 }
    ]
  }
}
```

### Step 2: Start Migration

```bash
curl -X POST http://localhost:1933/api/v1/migration/start \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d '{"target_name": "v2"}'
```

The system enters **dual-write** phase:
- All new writes go to both source and target collections
- Reads still come from the source collection
- Target write failures do **not** affect normal service

### Step 3: Begin Reindex

```bash
curl -X POST http://localhost:1933/api/v1/migration/build \
  -H "X-API-Key: your-admin-key"
```

The system starts a background reindex task:
- Scans all URIs in the source collection
- Skips URIs already present in the target collection (supports incremental rebuild)
- Re-embeds with the target model and upserts into the target collection

### Step 4: Monitor Progress

```bash
curl http://localhost:1933/api/v1/migration/status \
  -H "X-API-Key: your-admin-key"
```

```json
{
  "status": "ok",
  "result": {
    "migration_id": "mig_a1b2c3d4e5f6",
    "phase": "building",
    "active_side": "source",
    "dual_write_enabled": true,
    "source_embedder_name": "v1",
    "target_embedder_name": "v2",
    "degraded_write_failures": 0,
    "reindex_progress": {
      "processed": 75000,
      "total": 100000,
      "errors": 0,
      "skipped": 1200
    }
  }
}
```

**Progress fields**:

| Field | Meaning |
|-------|---------|
| `phase` | Current phase (`building` means reindex in progress) |
| `reindex_progress.processed` | Number of URIs processed |
| `reindex_progress.total` | Total URIs to process |
| `reindex_progress.errors` | Number of failed URIs |
| `reindex_progress.skipped` | Number of skipped URIs (already in target) |
| `degraded_write_failures` | Standby write failure count (indicates target health) |

When reindex completes, `phase` automatically changes to `building_complete`.

### Step 5: Verify Quality

In the `building_complete` phase, you can:

- **Check error rate**: via `reindex_progress.errors / total` in `/status`
- **Rebuild if needed**: call `POST /build` again — the system skips already-embedded URIs and only processes missing/new ones

```
POST /build → building → building_complete → POST /build → building → building_complete → ...
```

### Step 6: Switch Reads

After confirming quality, switch reads to the new model:

```bash
curl -X POST http://localhost:1933/api/v1/migration/switch \
  -H "X-API-Key: your-admin-key"
```

Now:
- All reads come from the target collection
- Dual-write continues, ensuring read-write consistency

### Step 7: Disable Dual-Write

After observing stable operation, disable dual-write:

```bash
curl -X POST http://localhost:1933/api/v1/migration/disable-dual-write \
  -H "X-API-Key: your-admin-key"
```

Now:
- Only the target collection receives writes
- The source collection is frozen

> ⚠️ **Note**: Rollback is not possible after disabling dual-write. If you need to revert, use `/rollback` while still in the `switched` phase.

### Step 8: Finish Migration

```bash
curl -X POST http://localhost:1933/api/v1/migration/finish \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-admin-key" \
  -d '{"confirm_cleanup": true}'
```

- `confirm_cleanup: true`: Drop the source collection (free storage)
- `confirm_cleanup: false`: Keep the source collection (default)

On completion, the migration state file permanently records this migration. On next service startup, the target config is automatically used.

## Complete Happy Path

```bash
# 1. List targets
curl http://localhost:1933/api/v1/migration/targets -H "X-API-Key: admin-key"

# 2. Start migration
curl -X POST http://localhost:1933/api/v1/migration/start \
  -H "Content-Type: application/json" -H "X-API-Key: admin-key" \
  -d '{"target_name": "v2"}'

# 3. Begin reindex
curl -X POST http://localhost:1933/api/v1/migration/build -H "X-API-Key: admin-key"

# 4. Poll until reindex completes
while true; do
  STATUS=$(curl -s http://localhost:1933/api/v1/migration/status -H "X-API-Key: admin-key")
  PHASE=$(echo "$STATUS" | python -c "import sys,json;print(json.load(sys.stdin)['result']['phase'])")
  echo "Current phase: $PHASE"
  if [ "$PHASE" = "building_complete" ]; then break; fi
  sleep 30
done

# 5. Switch reads
curl -X POST http://localhost:1933/api/v1/migration/switch -H "X-API-Key: admin-key"

# 6. Disable dual-write (after observing stability)
curl -X POST http://localhost:1933/api/v1/migration/disable-dual-write -H "X-API-Key: admin-key"

# 7. Finish migration
curl -X POST http://localhost:1933/api/v1/migration/finish \
  -H "Content-Type: application/json" -H "X-API-Key: admin-key" \
  -d '{"confirm_cleanup": true}'
```

## Rollback

### Non-Destructive Rollback (switched → dual_write)

If the new model doesn't perform as expected, safely roll back from the `switched` phase:

```bash
curl -X POST http://localhost:1933/api/v1/migration/rollback \
  -H "X-API-Key: your-admin-key"
```

This:
- Switches reads back to the source collection
- Keeps dual-write enabled
- Does **not** drop the target collection (data preserved, can `/build` again)

### Destructive Abort (any phase → idle)

To abandon a migration from any phase:

```bash
curl -X POST http://localhost:1933/api/v1/migration/abort \
  -H "X-API-Key: your-admin-key"
```

Phase-specific cleanup:
- `dual_write`: Disable dual-write, drop target collection
- `building`: Cancel reindex, disable dual-write, drop target, clear queue
- `building_complete`: Disable dual-write, drop target, clear queue
- `switched` / `dual_write_off`: Disable dual-write, drop target

> ⚠️ **Note**: `abort` is destructive — it drops the target collection and all its data.

## Crash Recovery

If the service crashes and restarts during migration, recovery is automatic:

| Phase at Crash | Recovery Behavior |
|---------------|-------------------|
| `dual_write` | Rebuild dual-write adapter, continue dual-write |
| `building` | Rebuild adapter + resume reindex engine from checkpoint |
| `building_complete` | Keep state, wait for operator to `/switch` |
| `switched` | Auto-recover, reads from target |
| `dual_write_off` | Auto-recover, writes to target only |
| `completed` | Auto-cleanup runtime state, return to idle |

Check current state after restart via `/status`.

## State Files

Two state files are involved in migration:

| File | Path | Purpose | Lifecycle |
|------|------|---------|-----------|
| Runtime State | `{workspace}/.migration/state/migration_runtime_state.json` | Migration progress and temporary state | Deleted on completion |
| Migration History | `{config_dir}/embedding_migration_state.json` | Current active config and history | **Permanent** |

Migration history file format:

```json
{
  "version": 1,
  "current_active": "v2",
  "history": [
    {
      "id": "mig_a1b2c3d4e5f6",
      "from_name": "v1",
      "to_name": "v2",
      "status": "completed"
    }
  ]
}
```

## State Machine Overview

```
                    ┌──────────── Forward Flow ────────────┐
                    │                                       │
idle ──(start)──→ dual_write ──(build)──→ building ──→ building_complete
                        │                │           │
                        └──(abort)───────┘           │
                                                     │
                    building_complete ──(switch)──→ switched ──(disable-dw)──→ dual_write_off ──(finish)──→ completed → idle
                                                     │                              │
                                                     └──(rollback)──→ dual_write    └──(abort)──→ idle
```

## API Reference

| Endpoint | Method | Description | Auth |
|----------|--------|-------------|------|
| `/api/v1/migration/targets` | GET | List available migration targets | Authenticated |
| `/api/v1/migration/status` | GET | Get current migration status and progress | Authenticated |
| `/api/v1/migration/start` | POST | Start migration (enter dual-write) | admin/root |
| `/api/v1/migration/build` | POST | Begin background reindex | admin/root |
| `/api/v1/migration/switch` | POST | Switch reads to target model | admin/root |
| `/api/v1/migration/disable-dual-write` | POST | Disable dual-write | admin/root |
| `/api/v1/migration/finish` | POST | Complete migration | admin/root |
| `/api/v1/migration/abort` | POST | Abort migration (destructive) | admin/root |
| `/api/v1/migration/rollback` | POST | Rollback to dual-write (non-destructive) | admin/root |

## FAQ

### Q: Are reads and writes available during reindex?

Yes. Dual-write is enabled during reindex, so new writes go to the target collection via dual-write. The reindex engine re-processing an existing URI only results in an idempotent upsert — no data risk.

### Q: How long does reindex take?

Depends on data volume and the embedding API concurrency limit. Monitor progress in real-time via `/status`. Large collections support multiple `/build` calls, each processing only missing/new URIs.

### Q: Can I skip a phase?

No. The migration follows a strict state machine with explicit preconditions per phase. Skipping a phase results in a 409 Conflict error.

### Q: Can I rollback after disabling dual-write?

No. After dual-write is disabled, the source collection has stopped receiving writes. Catching up on the delta would be too complex. Use `/rollback` while still in the `switched` phase if needed.

### Q: How do I know if the target collection is healthy?

Monitor the `degraded_write_failures` field from `/status`. This counter tracks standby write failures during dual-write. If it keeps growing, the target collection may have issues — consider `/abort` and restarting.
