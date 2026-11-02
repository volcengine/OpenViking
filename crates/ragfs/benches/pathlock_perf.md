# PathLock performance baselines

Run from the repository root:

```sh
cargo bench --locked -p ragfs --bench pathlock_perf -- \
  --mode all --iterations 20 --worker-iterations 100 --workers 1,4,16 \
  --output /tmp/pathlock-perf.csv
```

Use `--mode tree` or `--mode concurrency` to run either benchmark independently.
These are manual measurements, with no timing thresholds in CI. No server, model,
Redis service, or additional dependencies are required. All business data, databases,
and subprocess coordination files live in temporary directories and are cleaned up.

The Tree benchmark compares SQLite and filesystem providers on the same real
LocalFS datasets: 10–10,000 flat files, plus 200/1,000 subdirectories with one file
each. Setup and three warmup acquisitions are excluded. Latencies measure
`acquire_tree` only; locks are released between samples. `directory_reads` counts
directory listing calls during measured acquire/release cycles, not individual
metadata system calls. A lightweight atomic counter wraps LocalFS and forwards
native CAS operations. SQLite keeps its database outside the business subtree.

The concurrency benchmark uses 1/4/16 workers on disjoint existing file paths.
Each measured operation uses a different path, so a failed release does not turn
the following samples into logical conflicts on the same retained token.
It exercises `PathLockManager` with `timeout=0`, with no benchmark-side Busy
retries. It compares shared-provider threads (SQLite's single connection/mutex),
independent-provider threads (separate SQLite connections), and separate processes
sharing the same filesystem and database. Each worker warms up three operations
before a start barrier. Each runtime uses four Tokio worker threads, including
each subprocess. Latencies include both acquire and release, including
scheduling, mutex queues, and database waits. Logical lock contention is deliberately
excluded so database contention failures remain visible.

CSV rows include successful operations/second, successful-operation P50/P95/P99,
and failure counts by acquisition/release phase and Timeout/Busy error type.
All workers' latency samples are pooled; quantiles use nearest
rank. Throughput uses the controller's wall time, including barrier/reporting
overhead, especially for subprocesses. Failed operations are counted separately
and excluded from latency percentiles; always check failures before comparing rates.

The benchmark uses SQLite WAL with `synchronous=FULL` and the production 100 ms
Busy timeout per attempt, with three total attempts and 1 ms / 4 ms backoff.
Retries are inside the provider and cover all operations; the benchmark adds none.
It measures warm filesystem caches, not cold storage or application
file deletion/copy time. Twenty Tree samples give only a coarse tail estimate.
Repeat runs on otherwise idle hardware, record OS/CPU/Rust version, and use more
samples for stable P95/P99 comparisons. Thread and process results are different
workloads and should not be interpreted as a pure connection-pool speedup.
