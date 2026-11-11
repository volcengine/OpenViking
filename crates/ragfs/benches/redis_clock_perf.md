# Redis PathLock server-clock perf test

Run against a local standalone Redis:

```bash
REDIS_URL=redis://127.0.0.1:6379 \
PATHLOCK_CLOCK_PERF_OUTPUT=/tmp/redis-clock-samples.csv \
cargo test --release --locked -p ragfs --features cache --lib \
  perf_server_clock -- --ignored --nocapture
```

The test is ignored during normal test runs. It uses UUID-prefixed scopes and
deletes only its own keys after each cycle. It does not change Redis configuration
or flush the database. Failed runs may leave test keys with a two-hour TTL.
Do not use a production Redis for this manual load test.

| Environment variable | Default | Meaning |
| --- | ---: | --- |
| `REDIS_URL` | required | Redis endpoint |
| `PATHLOCK_CLOCK_PERF_ITERATIONS` | 1000 | Measured cycles per worker, phase and round |
| `PATHLOCK_CLOCK_PERF_WARMUP` | 100 | Warmup cycles per worker and phase |
| `PATHLOCK_CLOCK_PERF_ROUNDS` | 3 | A/B rounds; execution order alternates |
| `PATHLOCK_CLOCK_PERF_CONCURRENCY` | 16 | Additional concurrency level besides 1 |
| `PATHLOCK_CLOCK_PERF_OUTPUT` | unset | Optional raw sample CSV path |

Each cycle acquires fresh holder tokens, refreshes them, then deletes its test
scope. Workloads cover one Exact lock, a batch of 20 Exact locks, and one Tree
lock, at concurrency 1 and the configured additional level. Concurrent workers
use independent account scopes; this measures Redis contention, not logical
conflict retries. Scopes have no unrelated tokens or descendants.
All workers share one cache runtime and its default `pool_size=32` in-flight
command limit, using the same production Redis client configuration.

`fixed` runs the production acquisition/refresh Lua with only `server_now_ns()`
replaced by a frozen valid timestamp. `server` runs the actual production scripts.
Raw CSV uses clock `0` for fixed and `1` for server. Both variants use the same
lease, holder encoding, time subtraction, response validation and network calls.
The frozen clock is suitable only for this short, isolated benchmark.
This comparison isolates Redis TIME and its formatting/normalization; it is
**not** a full comparison with the old client-clock implementation.

Latency includes the cache runtime, EVALSHA round trip and script-result decoding;
request construction, fixture validation and scope deletion are outside the
individual acquisition/refresh timers. Warmup excludes connection setup and
initial script loading. No manager lifecycle or acquisition metrics are measured.
Each phase starts after all workers finish warmup. Summary percentiles use
nearest-rank over samples pooled across rounds.

`cycle_per_sec` counts complete acquire + refresh + cleanup cycles over phase
wall time, including validation and the DEL round trip. It is repeated for the
acquire and refresh rows and should not be interpreted as standalone command QPS.
No fixed performance thresholds are asserted; Redis errors and malformed or
unexpected lock results fail the test.
Warmup synchronization has a 60-second deadline so a failed worker cannot leave
the test waiting indefinitely. Use reasonable settings for the local endpoint.

## Local results (2026-11-05)

Apple M5 Pro, macOS 26.5.1 arm64, Rust 1.99.1, Redis 8.10.3 standalone at
`127.0.0.1:6379`, DB 0. Existing Redis configuration was retained: AOF disabled,
RDB `save = 3600 1 300 100 60 10000`. No control of other local activity was made.
Release build, 100 warmup cycles and 1,000 measured cycles per worker per phase,
3 rounds, concurrency 1/16. The test passed in 83.10 seconds. Raw CSV contained
612,000 validated acquisition/refresh samples: 3,000 per single-worker group and
48,000 per 16-worker group. An earlier exploratory run used 30 warmups and 300
cycles and had substantially greater between-phase drift.

Values below are **fixed → server**, in milliseconds.

| Workers | Batch | Kind | Operation | p50 | p99 |
| ---: | ---: | --- | --- | --- | --- |
| 1 | 1 | Exact | acquire | 0.123958 → 0.126167 | 0.395750 → 0.324417 |
| 1 | 1 | Exact | refresh | 0.116708 → 0.119916 | 0.382750 → 0.312917 |
| 1 | 20 | Exact | acquire | 0.220958 → 0.220833 | 0.549416 → 0.523916 |
| 1 | 20 | Exact | refresh | 0.210917 → 0.208959 | 0.519375 → 0.508625 |
| 1 | 1 | Tree | acquire | 0.122833 → 0.123167 | 0.348208 → 0.318542 |
| 1 | 1 | Tree | refresh | 0.116500 → 0.118167 | 0.347667 → 0.305458 |
| 16 | 1 | Exact | acquire | 0.341208 → 0.358958 | 0.880000 → 0.958084 |
| 16 | 1 | Exact | refresh | 0.332667 → 0.351417 | 0.872958 → 0.939709 |
| 16 | 20 | Exact | acquire | 1.247542 → 1.306750 | 2.489625 → 2.893625 |
| 16 | 20 | Exact | refresh | 1.236708 → 1.293750 | 2.501166 → 2.921958 |
| 16 | 1 | Tree | acquire | 0.329667 → 0.342125 | 0.828125 → 0.744916 |
| 16 | 1 | Tree | refresh | 0.323333 → 0.336042 | 0.812958 → 0.735250 |

At concurrency 16, Exact single-lock cycle throughput was 14,742 → 13,892 cycles/s;
Exact batch-20 was 4,239 → 3,926 cycles/s; Tree single-lock was
15,404 → 15,169 cycles/s. These include DEL and validation as described above.

Single-worker Exact single-lock p50 differences were +2.209µs for acquisition
and +3.208µs for refresh. At concurrency 16 they were +17.750µs and +18.750µs;
these end-to-end differences include queueing and are not the isolated execution
time of Redis TIME. Acquisition p50 deltas in the three single-worker rounds
were +2.979/+1.250/+3.166µs.

Batch-20 concurrency-16 acquisition round deltas were -8.750/+242.250/+11.021µs,
showing substantial phase variation. Negative differences and lower server-clock
p99 values in some groups should not be interpreted as an optimization. This
local test establishes the observed latency scale, not a universal overhead,
confidence interval, production QPS limit or throughput guarantee.
