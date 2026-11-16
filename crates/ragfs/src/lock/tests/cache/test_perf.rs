use super::*;

    /// Measure HASH-only atomic batch latency for representative lock counts.
    #[tokio::test]
    #[ignore = "manual Redis performance baseline"]
    async fn perf_hash_only_batches() {
        let Some((runtime, provider)) = test_provider("perf").await else {
            return;
        };
        for count in [100usize, 500, 1_000] {
            let requests = (0..count)
                .map(|index| PathLockRequest {
                    path: format!("/local/account-a/perf/{index}"),
                    kind: PathLockKind::Exact,
                })
                .collect::<Vec<_>>();
            let start = Instant::now();
            let acquired = provider
                .try_acquire_batch_atomic(&requests, "perf-owner", 100, 0)
                .await
                .unwrap()
                .unwrap();
            let elapsed = start.elapsed();
            println!(
                "pathlock HASH-only count={count} acquire_ms={}",
                elapsed.as_secs_f64() * 1_000.0
            );
            provider
                .rollback_acquisitions_atomic(&acquired, "perf-owner")
                .await
                .unwrap();
        }

        runtime.close().await.unwrap();
    }
