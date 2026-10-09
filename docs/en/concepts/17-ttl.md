# Directory TTL

TTL is off by default. It covers user and peer `events/YYYY/MM/DD` directories and `sessions/{session_id}`. Resources and other memory categories are outside this scope.

## Configuration and policy application

TTL uses instance defaults and Account overrides (a Web Studio "library" is an Account). Configure global defaults, type defaults, or one of these three kinds of policy root:

- `viking://user/{user_id}/memories/events`
- `viking://user/{user_id}/peers/{peer_id}/memories/events`
- `viking://user/{user_id}/sessions`

Each concrete root URI can have its own policy. Different users and peers can use different deadlines; a library is not limited to three entries. Sessions belong to users, with no peer sessions root. Roots without overrides inherit their type default, including roots for newly created users and peers.

For each node, merge Account overrides → instance runtime settings → startup settings. Then select concrete root → type (`user_events`, `peer_events`, `sessions`) → `global`. An Account `global` policy does not override a more specific instance type default: overriding instance `sessions=30 days` requires an Account `sessions` policy. User and peer identities locate roots; they add no configuration layer.

`disabled` stops inheritance at that node; `inherit` skips it. PATCH `null` removes the current layer's override. Unconfigured types inherit `global`, which defaults to disabled. The optional recommended preset is 60 days for User events, 60 days for Peer events, and 30 days for Sessions. It takes effect only when explicitly selected.

Years, months, dates, individual sessions, nested directories and files expose read-only deadlines. Enabling or changing a root policy also updates existing live directories, including previously unmanaged history, while respecting more-specific overrides. Relative expiry uses the original business timestamp plus the new duration; absolute expiry uses the configured deadline. Shortening may expire a directory immediately. Disabling the effective policy clears live deadlines. Expired and deleted objects are never revived.

The configuration request lists each parent's child directories once, keeps their names, and updates at most eight directories concurrently. Deleting an earlier directory cannot shift later ones out of the work list. The request awaits metadata writes; partial failures report incomplete directories so the same configuration can be retried. For relative policies, history without a reliable original timestamp is reported rather than assigned the configuration update time or directory modTime. Explicit empty event directories can be created and start their lifetime on the first body write.

Policy application and new directory initialization read current settings from the configured source. Each account's batch reuses one resolved policy; ordinary content updates use the saved lifetime. These reads leave the runtime configuration cache and its refresh loop unchanged. The directory name list uses memory proportional to the number of immediate children of the current parent.

## Deadline calculation

Each lifecycle directory stores one `expires_at` in `.meta.json`. Relative retention uses a fixed starting time: the existing `created_at` for Sessions, and `received_at` for the first successful event body write. `ttl_days` belongs only to policy configuration. Events can still read legacy `.ttl.json`. AGFS directory metadata updates preserve other business fields in the same file; directory stat exposes its own deadline.

- Events start their lifetime on the first successful content write. The path date only groups events. Later body writes never renew deadlines; explicit root policy changes can adjust live directories.
- Sessions inherit their root policy on creation. Relative deadlines are creation time plus the configured duration; absolute deadlines use the configured timestamp. Appends, completed commits and task replays leave the deadline unchanged.
- Automatic renewal and renewal recovery are deferred. Users can change library or root policies before expiry to update live directories. Relative deadlines retain the fixed starting time rather than using the policy-change time. Active Sessions still expire at their persisted deadline.

Messages, attachments, archives and L0/L1/L2 share the directory deadline. There is no message-level JSONL retention, `ttl_generation`, per-session override or per-file mode.

TTL adds no deadline fields to body/summary formats or extraction Context objects. Reads obtain the deadline from the owner directory; similarly named body fields remain user content. The first Event write registers its deadline before publishing the body and rolls back a failed write. It needs no extra journal or second metadata write after success.

## Visibility

At `now >= expires_at` in UTC, the directory and all descendants become invisible. Direct reads return 404. Session/file listings, find/search/recall, grep and glob filter expired content and refill visible candidates.

Structured objects expose their owner's `expires_at`, explicitly `null` without TTL. Mixed results carry per-item deadlines. Single-owner text/list responses include the deadline in the envelope. URI-only listing compatibility modes and download bytes keep their existing shape and still enforce server filtering. Root/year/month containers have no shared expiry; policy roots also expose `policy` and `effective_policy`.

Snapshot reads use the live directory deadline, or the snapshot deadline after deletion. Raw import and restore reject overwrites of directories that still have TTL, and reject expired source data, so restoration cannot detach content from its deadline. Each affected directory is checked once, avoiding repeated metadata reads for sibling files.

## Cleanup and performance

Cleanup uses the existing Session commit QueueFS worker framework. The scheduler scans owner directory metadata in accounts that have used TTL, sends expired owners to the queue, and continues in bounded batches while the queue drains. After finishing a pass it waits one day by default. The account marker stores no object deadlines; there is no per-object scheduling index or claim lease.

The worker rechecks the owner deadline under its metadata file lock and reuses the existing Session mutation mutex. It deletes files under individual exact locks, retries contention, and uses no tree lock or per-file expiry decision. It first deletes and confirms all vectors under the account and owner URI, then removes owned bodies, messages, attachments and L0/L1. It confirms body removal before deleting owner metadata and finally verifies that the directory is gone. Vector records need no `expires_at` field; the existing account and URI fields cover the entire subtree, including vectors whose source file is already missing. External parent summaries stay unchanged. Deletion triggers no LLM, embedding or summary rebuild.

Lock contention, vector deletion failure or remaining body files preserve the owner deadline for the next pass. Restarting the scheduler rediscovers candidates from directory metadata; it needs no durable scan cursor or task-history lookup. A failure of the final confirmation request still reports an error even if the data was already deleted. Pre-existing vector-only orphans whose owner metadata is also gone cannot be discovered by a directory scan; strict deletion can remove them when their owner URI is known.

Scanning costs O(owner directories in accounts that have used TTL) per pass. Each page examines at most `batch_size` owners, checks a time budget between owners, and waits for queued work to drain. This bounds queued deletion work, while storage listing latency and backlog can extend the daily pass. It avoids maintaining a second expiry record on writes, transfers, and restores.

System strict cleanup deletes vectors directly by directory URI scope, avoiding a separate file-tree traversal to collect vector URIs. File deletion still takes individual locks and confirms the result.

Vector queries share owner metadata reads across candidate refill rounds and check directory expiry without checking each body or summary file. Legacy objects without metadata require an existing owner directory. Refill excludes the entire expired or deleted owner subtree while retaining live candidates from other directories. Individual file deletion relies on the existing vector deletion path. Directory `count` uses the backend total and converges after physical cleanup, avoiding a full vector scan for real-time expiry counts. Returned content still enforces expiry immediately. Billing may lag physical deletion. OV cleanup alone does not verify cloud billing, gateway forwarding or backup erasure.

## Interfaces

- [TTL configuration](../configuration/01-server.md#ttl): library/type/root policies.
- [Expiry query](../api/12-content.md#document-expiry): `GET /api/v1/content/ttl`, SDK/MCP `get_ttl`, CLI `ov ttl get`.
- [Sessions](../api/05-sessions.md#session-ttl): create/config APIs inherit the root policy and accept no TTL input.

Asynchronous Session commit writes validate the original Phase 1 `task_id` under a short Session lock. Old work cannot modify a replacement Session with the same ID or recreate events from a deleted source. A fresh Session may still import an older calendar date. Ordinary body I/O releases the common metadata lock; cleanup blocks new admissions and checks existing file leases, including writes whose file does not yet exist.
