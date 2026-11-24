# Redis directory TTL

TTL is optional and disabled by default. The first implementation requires Redis;
there is no Redis-free backend. The implementation and tests ship with OpenViking.
It uses the existing expiry Redis client and storage/configuration lifecycle.

Install OpenViking with the `expiry` extra, then merge into your server config:

```json
{
  "server": {
    "ttl_adapter": {
      "enabled": true,
      "config": {
        "resource_id": "stable-library-id",
        "redis": {"mode": "standalone", "endpoints": ["redis://127.0.0.1:6379"]}
      }
    }
  },
  "expiry": {
    "service_name": "stable-library-id",
    "publisher_separate_process": false,
    "redis": {"mode": "standalone", "endpoints": ["redis://127.0.0.1:6379"]}
  },
  "ttl": {"sessions": 30, "user_events": 60, "peer_events": 60}
}
```

Use a stable ResourceID across restarts; each Redis scope also includes the
OpenViking Account ID. Production credentials and Redis durability are deployment
configuration. The regular OV Docker image already includes the expiry extra.
No external Python class path, wheel or overlay image is needed.
`ttl_adapter.config.redis` stores directory indexes; `expiry.redis` stores the
shared scheduler's tasks. The example uses the same Redis and runs the publisher
in-process. If auto commit already configures expiry, retain that configuration
and its publisher deployment. TTL also requires `expiry.service_name` and
`expiry.redis.endpoints`.

An omitted or disabled `server.ttl_adapter` installs no write hook, starts no TTL
worker and loads no TTL Redis client. Explicit policy writes then return
`501 UNIMPLEMENTED`: `Redis-backed TTL is not enabled in this deployment.`
With the adapter enabled, writes only enqueue in-memory candidates. The
registration worker loads policy and drops disabled candidates before Redis I/O.
UserConfig cold reads stay off the business write path. A reader uses
`config.writer=false`: expiry display is
available, but it never scans or recycles. TTL is soft: searches and reads keep
returning expired content until physical deletion. Historical Event writes are
allowed; they do not renew the date directory's lifetime.

The existing write path buffers successful writes and flushes them asynchronously
in batches. Session directory scores use the write time (`ZADD GT`); Event date
directories use the directory date (`ZADD NX`). Deletion rechecks current policy
and Session `messages.jsonl` ModTime under the existing directory lock. Redis
index removal compares the original score to preserve a concurrent renewal.

TTL adds no directory locks to Session, Event or vector writes. Session mutations
and memory updates keep their existing native file/transaction locks. Deletion
uses the native Tree lock for the policy/ModTime recheck, recursive removal and
conditional index cleanup; lock conflicts retain the registration for retry.

TTL adds no Session existence preflight or missing-file rule. Session APIs keep
their existing lookup behavior; TTL does not add guards to stale Session objects
or delayed writes. Redis reporting remains asynchronous and acquires no path locks.

User TTL lives in `settings/user_config.json` and uses the existing
`GET/PATCH /api/v1/admin/accounts/{account_id}/users/{user_id}/settings` API:

```json
{"ttl": {"sessions": 30, "user_events": 60, "peer_events": 60}}
```

For each field, User overrides Account, which overrides global configuration.
All Peers under one User share `peer_events`. `user_events` and `sessions`
remain independent. Type defaults precede `global`.
Missing fields inherit, `disabled` stops fallback, and PATCH `null` removes an
override. `roots` is no longer part of the cluster/Account configuration schema.
The existing UserConfig update lock, validation, backup and deletion apply.
Account configuration path queries also require an existing User; a missing
User returns 404, matching the User settings endpoint.
TTL caches at most 1024 user snapshots, keyed by Account and User. A local save
publishes immediately; other processes refresh on demand after 30 seconds.
Concurrent misses share one load. This cache adds no periodic filesystem scan.

The hidden ROOT-authenticated `/api/v1/admin/ttl/initial` endpoint registers or
resets a fixed `initial` scan. Redis keeps its pending marker, checkpoint and done
marker across OV restarts. Registration never lists business directories;
`initial.discover=1` defers missing or reset scope discovery to maintenance.
Reset retains the old roots for policy checks until discovery replaces them.
The OV writer registers hourly maintenance with the existing expiry scheduler,
using a stable ResourceID offset. Its TTL consumer starts on first registration;
Redis retains that activation across restarts. An overdue callback after restart
schedules the next window instead of starting a scan or recycling. Configuration
saves, first access and startup cannot bypass this window. OV discovers initial
roots there, resumes saved checkpoints and recycles in later hourly rounds.
The callback only schedules the next window and wakes maintenance; it never
loads UserConfig. Maintenance isolates failures per Account. Completed initial
scans are not replayed unless reset intent exists. A disabled scope retains the
hourly check, but its directories are neither backfilled nor deleted.
Each round uses the existing Account user registry and UserConfig cache to
compute its TTL cutoff, then queries the directory ZSETs by score in batches.
It does not enumerate every directory index entry or rely on initial roots to
find later users. Policy changes during a round may wait until the next round;
deletion still rechecks current policy. No business directories are listed.
Existing deletion throttling applies to eligible attempts, including failed or
renewed Sessions.
Background directory listings, policy batches and index pages wait
`batch_interval` (50 ms by default), including Events and already-indexed entries.
Incremental registration coalesces its first batch for at most 50 ms and paces
further batches independently. Event summary writes do not enqueue registration;
cross-batch Event writes may still repeat the idempotent `ZADD NX`.
Failure backoff includes jitter and is capped at 60 seconds.
These limits do not bound a single large `ls` or recursive `rm`, or total load
across libraries sharing Redis. No new global coordinator is introduced.
Local dev mode has no user registry, so it uses the minimum legal TTL as a
conservative query bound and checks each candidate's policy before deletion.
`/api/v1/admin/ttl/recycle` also supports explicit internal requests. The hidden
GET `/api/v1/admin/ttl/initial` reads internal recovery state; no caller needs a
Redis connection. TTL policy PATCHes persist reset intent before saving, so the
next maintenance trigger can recover an interrupted handoff. Commercial Server
configures the writer and forwards internal repair requests; it does not run the
hourly scheduler. TTL adds no separate timer or callback queue.

Account deletion uses the existing durable cleanup task to stop its TTL work,
unregister maintenance, and remove the three directory indexes and backfill
state. Redis failure keeps the deletion fence for the normal cleanup retry.
Late callbacks for a deleted Account only repeat cleanup; they do not reschedule
maintenance or scan the filesystem.

TTL does not inspect archives before delayed writes: no `.meta.json` read,
source stat or archive URI propagation. Existing Session and queue checks remain
unchanged; in-flight extraction may finish after its source Session is deleted.
Vector delivery adds no TTL policy or filesystem check. Deletion still rechecks
current policy and Session ModTime. Rare late vectors may remain after directory
recycling; once the directory index is removed, another TTL round is not
guaranteed to find them. No automatic orphan-vector repair is added.

Retention coverage lives in the existing admin API, storage deletion, Session
commit and service lifecycle tests, using native local RAGFS and fakeredis with
Lua support.
