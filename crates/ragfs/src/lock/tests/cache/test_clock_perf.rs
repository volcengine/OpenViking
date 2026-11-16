//! Manual A/B benchmark: production holder Lua vs the same Lua with frozen time.
//! No latency assertions: results depend on the host and Redis configuration.

use super::*;
use crate::cache_runtime::RedisProviderConfig;
use std::io::Write;
use tokio::sync::Barrier;

fn setting(name: &str, default: usize) -> usize {
    let value = std::env::var(name)
        .map(|raw| raw.parse().expect("invalid perf setting"))
        .unwrap_or(default);
    assert!(value > 0, "{name} must be positive");
    value
}

fn clock_variant(script: &'static str, now: u128) -> &'static str {
    let start = script.find("local function server_now_ns()").unwrap();
    let end = start + script[start..].find("\nend").unwrap() + "\nend".len();
    let mut variant = script.to_string();
    variant.replace_range(
        start..end,
        &format!("local function server_now_ns()\n    return \"{now}\"\nend"),
    );
    assert!(!variant.contains("redis.call(\"TIME\")"));
    // ScriptDefinition owns static references; register only two test variants.
    Box::leak(variant.into_boxed_str())
}

#[derive(Default)]
struct Samples {
    acquire: Vec<f64>,
    refresh: Vec<f64>,
    seconds: f64,
}

async fn cycle(
    provider: &RedisPathLockProvider,
    key: &str,
    paths: &[String],
    kind: &str,
    acquire_id: &str,
    refresh_id: &str,
) -> (f64, f64) {
    let mut args = vec![Bytes::from("v2|perf-owner|holder|"), Bytes::from("3600000")];
    for path in paths {
        args.extend([Bytes::from(path.clone()), Bytes::from(kind.to_string())]);
    }
    let start = Instant::now();
    let acquired = provider
        .execute_array(acquire_id, key.into(), args)
        .await
        .expect("perf acquire failed");
    let acquire_ms = start.elapsed().as_secs_f64() * 1_000.0;
    assert_eq!(
        RedisPathLockProvider::result_status(&acquired, "perf").unwrap(),
        "ok"
    );
    assert_eq!(acquired.len(), 2 + 4 * paths.len());
    for result in acquired[2..].chunks_exact(4) {
        assert_eq!(
            RedisPathLockProvider::result_string(&result[1], "perf").unwrap(),
            "created"
        );
        let raw = RedisPathLockProvider::result_string(&result[3], "perf").unwrap();
        let token = LockTokenCodec::decode(raw).expect("valid acquired token");
        assert_eq!(token.holders.len(), 1);
    }
    let mut args = vec![
        Bytes::from("v2|perf-owner|holder|0"),
        Bytes::from("3600000"),
    ];
    args.extend(paths.iter().cloned().map(Bytes::from));
    let start = Instant::now();
    let refreshed = provider
        .execute_array(refresh_id, key.into(), args)
        .await
        .expect("perf refresh failed");
    let refresh_ms = start.elapsed().as_secs_f64() * 1_000.0;
    assert_eq!(refreshed.len(), paths.len());
    for value in refreshed {
        let ScriptValue::Array(result) = value else {
            panic!("invalid refresh response");
        };
        assert_eq!(
            RedisPathLockProvider::result_status(&result, "perf").unwrap(),
            "ok"
        );
    }
    // Only this test's UUID scope is removed; never FLUSHDB or touch user keys.
    provider.runtime.del(&[key.into()]).await.unwrap();
    (acquire_ms, refresh_ms)
}

/// REDIS_URL=redis://127.0.0.1:6379 cargo test --release --locked -p ragfs
/// --features cache --lib perf_server_clock -- --ignored --nocapture
#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "manual real Redis server-clock performance comparison"]
async fn perf_server_clock() {
    let endpoint = std::env::var("REDIS_URL").expect("set REDIS_URL");
    let iterations = setting("PATHLOCK_CLOCK_PERF_ITERATIONS", 1_000);
    let warmup = setting("PATHLOCK_CLOCK_PERF_WARMUP", 100);
    let rounds = setting("PATHLOCK_CLOCK_PERF_ROUNDS", 3);
    let concurrency = setting("PATHLOCK_CLOCK_PERF_CONCURRENCY", 16);
    let runtime = CacheRuntime::redis(RedisProviderConfig {
        endpoints: vec![endpoint],
        command_timeout_ms: 10_000,
        ..RedisProviderConfig::default()
    })
    .await
    .unwrap();
    let service = format!("clock-perf-{}", uuid::Uuid::new_v4());
    let provider =
        Arc::new(RedisPathLockProvider::new(runtime.clone(), &service, 3_600.0).unwrap());
    let now = super::time_tests::now(&provider).await;
    assert_eq!(
        ACQUIRE_BATCH_SCRIPT
            .matches("local now_ns = server_now_ns()")
            .count(),
        1
    );
    assert_eq!(
        REFRESH_BATCH_SCRIPT
            .matches("local time_ns = server_now_ns()")
            .count(),
        1
    );
    const FIXED_ACQUIRE: &str = "pathlock.perf.fixed_acquire.v1";
    const FIXED_REFRESH: &str = "pathlock.perf.fixed_refresh.v1";
    for (id, script) in [
        (FIXED_ACQUIRE, ACQUIRE_BATCH_SCRIPT),
        (FIXED_REFRESH, REFRESH_BATCH_SCRIPT),
    ] {
        runtime
            .register_script(ScriptDefinition {
                id,
                redis_lua: clock_variant(script, now),
            })
            .unwrap();
    }
    let mut output = std::env::var("PATHLOCK_CLOCK_PERF_OUTPUT")
        .ok()
        .map(|path| std::fs::File::create(path).unwrap());
    if let Some(file) = &mut output {
        writeln!(
            file,
            "concurrency,batch,kind,round,clock,operation,sample,latency_ms"
        )
        .unwrap();
    }
    println!("concurrency,batch,kind,clock,operation,samples,p50_ms,p95_ms,p99_ms,cycle_per_sec");
    let levels = if concurrency == 1 {
        vec![1]
    } else {
        vec![1, concurrency]
    };
    for workers in levels {
        for (batch, kind) in [(1, "E"), (20, "E"), (1, "T")] {
            let mut totals: [Samples; 2] = Default::default();
            for round in 0..rounds {
                // Reverse whole-phase order each round to reduce drift bias.
                for offset in 0..2 {
                    let mode = (round + offset) % 2;
                    let ids = if mode == 0 {
                        (FIXED_ACQUIRE, FIXED_REFRESH)
                    } else {
                        (ACQUIRE_BATCH_ID, REFRESH_BATCH_ID)
                    };
                    let barrier = Arc::new(Barrier::new(workers + 1));
                    let mut tasks = Vec::new();
                    for worker in 0..workers {
                        let provider = provider.clone();
                        let barrier = barrier.clone();
                        tasks.push(tokio::spawn(async move {
                            let paths: Vec<_> = (0..batch)
                                .map(|i| format!("/local/perf-{worker}/target-{i}"))
                                .collect();
                            let key = provider.tokens_key_for_path(&paths[0]).unwrap();
                            for _ in 0..warmup {
                                cycle(&provider, &key, &paths, kind, ids.0, ids.1).await;
                            }
                            barrier.wait().await;
                            let mut samples = Vec::with_capacity(iterations);
                            for _ in 0..iterations {
                                samples
                                    .push(cycle(&provider, &key, &paths, kind, ids.0, ids.1).await);
                            }
                            assert!(provider.read_token(&paths[0]).await.unwrap().is_none());
                            samples
                        }));
                    }
                    // Warmup, connection setup and NOSCRIPT recovery are excluded.
                    tokio::time::timeout(Duration::from_secs(60), barrier.wait())
                        .await
                        .expect("perf warmup failed or took more than 60 seconds");
                    let start = Instant::now();
                    let mut values = Vec::new();
                    for task in tasks {
                        values.extend(task.await.unwrap());
                    }
                    totals[mode].seconds += start.elapsed().as_secs_f64();
                    for (sample, (acquire, refresh)) in values.into_iter().enumerate() {
                        totals[mode].acquire.push(acquire);
                        totals[mode].refresh.push(refresh);
                        if let Some(file) = &mut output {
                            for (operation, latency) in [("acquire", acquire), ("refresh", refresh)]
                            {
                                writeln!(file, "{workers},{batch},{kind},{round},{mode},{operation},{sample},{latency:.6}").unwrap();
                            }
                        }
                    }
                }
            }
            for (mode, total) in totals.into_iter().enumerate() {
                let cycles_per_sec = total.acquire.len() as f64 / total.seconds;
                for (operation, mut values) in
                    [("acquire", total.acquire), ("refresh", total.refresh)]
                {
                    values.sort_by(f64::total_cmp);
                    let percentile = |q: f64| values[(q * values.len() as f64).ceil() as usize - 1];
                    println!("{workers},{batch},{kind},{},{operation},{},{:.6},{:.6},{:.6},{cycles_per_sec:.2}",
                        ["fixed", "server"][mode], values.len(), percentile(0.5), percentile(0.95), percentile(0.99));
                }
            }
        }
    }
    runtime.close().await.unwrap();
}
