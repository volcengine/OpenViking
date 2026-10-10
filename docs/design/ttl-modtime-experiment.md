# TTL modTime experiments

These independent branches start at main `144033826eb3d612de1a64b46efa7cb5f6073a41`.
They are review and performance experiments, not production rollout approval.

## Shared retrieval contract

- External policies: `days` and `disabled`; absent nodes inherit. Concrete root > type > global.
- Events use body `modTime` projected into existing vector `updated_at`.
- A whole Session uses existing Session metadata `created_at`, projected into vector `created_at`.
- Query filters run before TopK; no application TTL refill, per-hit stat/read/fetch, Redis access,
  forced configuration refresh, or new TTL lock in search.
- `expires_at` is calculated in memory and retained by MatchedContext, JSON and SDKs.
  Disabled TTL omits the field. A root container has no independent deadline.
- Reserved tags in existing `search_tags` encode scope. No new vector schema or file metadata.
- No directory sweep on policy save or process startup. Policy changes apply to indexed versions;
  content/index consistency remains eventual. Append/overwrite renews the entire event file.
- Late TTL-active indexing is fenced by the existing path lock and current file version.
- Deletion rechecks current source policy and file time, then calls VikingFS.rm under the same lease.
  Session deletion locks and removes its whole subtree. No new `.meta.json` fields are written.

## Branches

| Branch | Trigger / storage | Necessary cost |
| --- | --- | --- |
| feat/ttl-modtime-queuefs | Reads/writes coalesce bounded expired-vector discovery; QueueFS persists deletion tasks | Extra background vector query, queue I/O, final delete checks |
| feat/ttl-modtime-redis | Enabled writes register stable URI with current time in Redis; worker examines due scores | Write-side stat for events, Redis transaction, periodic Redis lookups, final delete checks |

QueueFS also has explicit `ttl_cleanup.execution=sync` for measuring inline discovery/deletion.
Normal mode is asynchronous. Redis acknowledgement compares the claimed score before removal so
an old job cannot remove a newer registration. QueueFS retries an unacknowledged delivery.
`ttl_cleanup.enabled=false` is the default and performs no cleanup I/O.

## Review gates

- Legacy records without trusted scope/time are not transparently migrated. Enabling TTL does not
  backfill files, vectors or Redis. Existing unregistered Redis objects have no scheduled recovery guarantee.
- Cross-file aggregate summaries are excluded where an active TTL could make their contents stale.
  Recall/product effects must be measured and reviewed.
- Passive deletion cannot promise when unaccessed data stops consuming storage or being billed.
  Logical expiration, physical deletion and billing completion are different events.
- The performance matrix must include main, TTL off, TTL on, and cleanup mixed with searches,
  including off accounts sharing those resources. Both real VikingDB adapters are required.
- Full list/glob/grep visibility and all direct access surfaces still require a separate rollout audit.
  Passing the retrieval experiment alone does not authorize production deployment.
