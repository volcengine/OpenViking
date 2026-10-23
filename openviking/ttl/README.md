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
  "ttl": {"sessions": 30, "user_events": 60, "peer_events": 60}
}
```

Use a stable ResourceID across restarts; each Redis scope also includes the
OpenViking Account ID. Production credentials and Redis durability are deployment
configuration. The regular OV Docker image already includes the expiry extra.
No external Python class path, wheel or overlay image is needed.

An omitted or disabled `server.ttl_adapter` installs no write hook, starts no TTL
worker and loads no TTL Redis client. Explicit policy writes then return
`501 UNIMPLEMENTED`: `Redis-backed TTL is not enabled in this deployment.`
An enabled adapter still skips Redis, stat and TTL locks when the target's
effective policy is disabled. A reader uses `config.writer=false`: Events
filtering and expiry display are available, but it never scans or recycles.

The existing write path buffers successful writes and flushes them asynchronously
in batches. Session directory scores use the write time (`ZADD GT`); Event date
directories use the directory date (`ZADD NX`). Deletion rechecks current policy
and Session `messages.jsonl` ModTime under the existing directory lock. Redis
index removal compares the original score to preserve a concurrent renewal.

The hidden ROOT-authenticated `/api/v1/admin/ttl/initial` endpoint registers or
resets a fixed `initial` scan. Redis keeps its pending marker, checkpoint and done
marker across OV restarts; startup does not discover objects by scanning AGFS.
`/api/v1/admin/ttl/recycle` performs a recycle sweep on explicit request. Deployment
orchestration supplies identity and hourly staggered calls. OV discovers initial
roots, reuses saved checkpoints and checks due candidates itself. The hidden
GET `/api/v1/admin/ttl/initial` reads internal recovery state; no caller needs a
Redis connection. TTL policy PATCHes persist reset intent before saving, so the
next internal trigger can recover an interrupted handoff. The kernel does not
add another timer or callback queue.

Tests live in `tests/ttl/`. `TTL_TEST_REDIS` and `TTL_TEST_CLUSTER` opt into isolated
real Redis tests; ordinary tests use fakeredis with Lua support. The separate
commercial Server integration test exercises its scheduler against an OV writer.
`tests/ttl/benchmark_ttl.py` is a local cost sample, not production acceptance.
