use super::*;

    #[tokio::test]
    async fn redis_acquire_metrics_export_outcomes_and_slow_calls() {
        let calls = AtomicU64::new(0);
        let mock = crate::cache_runtime::MemoryMockProvider::new().with_script_handler(move |_| {
            // Inject real response latency so the slow counter follows the timed call.
            std::thread::sleep(Duration::from_millis(15));
            let values = match calls.fetch_add(1, Ordering::Relaxed) {
                0 => vec!["ok", "0", "/local/account-a/file", "reentrant", "", ""],
                1 => vec!["conflict", "/local/account-a/file", "other:100:E"],
                2 => return Err(CacheError::Unavailable("injected failure".into())),
                _ => panic!("unexpected Redis call"),
            };
            crate::cache_runtime::ScriptResult::encode(&ScriptValue::Array(
                values
                    .into_iter()
                    .map(|value| ScriptValue::Bytes(value.as_bytes().to_vec()))
                    .collect(),
            ))
        });
        let runtime = CacheRuntime::memory_with_provider(Arc::new(mock));
        let provider =
            Arc::new(RedisPathLockProvider::new(runtime.clone(), "metrics-test", 30.0).unwrap());
        assert!(provider
            .try_acquire_batch_atomic(&[], "owner", 100, 0)
            .await
            .is_err());
        for (index, kind) in [PathLockKind::Exact, PathLockKind::Tree, PathLockKind::Exact]
            .into_iter()
            .enumerate()
        {
            let result = provider
                .try_acquire_batch_atomic(
                    &[PathLockRequest {
                        path: "/local/account-a/file".into(),
                        kind,
                    }],
                    "owner",
                    100,
                    0,
                )
                .await;
            match index {
                0 => assert!(result.is_ok()),
                1 => assert!(matches!(result, Err(PathLockError::Conflict { .. }))),
                _ => assert!(matches!(result, Err(PathLockError::Io(_)))),
            }
        }
        let manager = PathLockManager::new(
            Arc::new(MemFileSystem::new()),
            provider,
            PathLockConfig::default(),
        );
        // Repeated exports must retain cumulative observations.
        for _ in 0..2 {
            let metrics = crate::metrics::lock_metrics(manager.metrics_snapshot().await);
            let slow = metrics
                .iter()
                .find(|metric| metric.name == "lock_redis_slow_acquisitions_total")
                .unwrap();
            assert!(matches!(
                slow.value,
                crate::metrics::RagfsMetricValue::Counter { value: 3, .. }
            ));
            let durations = metrics
                .iter()
                .filter(|metric| metric.name == "lock_redis_acquire_duration_seconds")
                .collect::<Vec<_>>();
            assert_eq!(durations.len(), 3);
            for (kind, result) in [("exact", "ok"), ("tree", "conflict"), ("exact", "error")] {
                let metric = durations
                    .iter()
                    .find(|metric| {
                        metric.labels["kind"] == kind && metric.labels["result"] == result
                    })
                    .unwrap();
                assert_eq!(metric.labels.len(), 2);
                match &metric.value {
                    crate::metrics::RagfsMetricValue::Histogram {
                        count,
                        sum,
                        bucket_counts,
                        scale,
                        ..
                    } => {
                        assert_eq!(*count, 1);
                        assert_eq!(bucket_counts.iter().sum::<u64>(), 1);
                        assert!(*sum > 10_000_000);
                        assert_eq!(*scale, 1e-9);
                    }
                    _ => panic!("expected duration histogram"),
                }
            }
        }
        runtime.close().await.unwrap();
    }

    /// Verify an unknown atomic write result never publishes a local lease.
    #[tokio::test]
    async fn manager_unknown_atomic_result_does_not_publish_lease() {
        let provider = Arc::new(FaultProvider::new(AtomicFault::WriteThenIo));
        let manager = PathLockManager::new(
            Arc::new(MemFileSystem::new()),
            provider.clone(),
            PathLockConfig {
                provider: "cache".to_string(),
                service_name: Some("timeout-test".to_string()),
                ..PathLockConfig::default()
            },
        );

        assert!(matches!(
            manager
                .acquire_exact("/timeout", Duration::ZERO, None)
                .await,
            Err(PathLockError::Io(_))
        ));
        assert_eq!(manager.observe().await.active_locks, 0);
        assert!(provider.read_token("/timeout").await.unwrap().is_some());
    }

    /// Verify Manager never performs non-atomic stale cleanup after a logical conflict.
    #[tokio::test]
    async fn manager_logical_conflict_does_not_remove_stale_snapshot() {
        let provider = Arc::new(FaultProvider::new(AtomicFault::Conflict));
        let manager = PathLockManager::new(
            Arc::new(MemFileSystem::new()),
            provider.clone(),
            PathLockConfig {
                provider: "cache".to_string(),
                service_name: Some("race-test".to_string()),
                lock_expire_secs: 1.0,
                ..PathLockConfig::default()
            },
        );

        assert!(manager
            .acquire_exact("/race", Duration::from_millis(5), None)
            .await
            .is_err());
        assert_eq!(provider.remove_calls.load(Ordering::SeqCst), 0);
    }

    /// Verify storage-side stale recovery reaches manager metrics.
    #[tokio::test]
    async fn stale_recovery_count_reaches_manager_observation() {
        let Some((runtime, provider)) = test_provider("stale-metrics").await else {
            return;
        };
        let path = "/local/account-a/old";
        seed_token(&provider, path, &token("dead", 1, PathLockKind::Exact)).await;
        let manager = PathLockManager::new(
            Arc::new(MemFileSystem::new()),
            Arc::new(provider),
            PathLockConfig::default(),
        );
        let lease = manager
            .acquire_tree(path, Duration::ZERO, None)
            .await
            .unwrap();
        assert_eq!(manager.observe().await.stale_locks_removed, 1);
        manager.release(&lease).await.unwrap();
        runtime.close().await.unwrap();
    }
