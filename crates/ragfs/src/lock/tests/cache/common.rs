    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;
    use std::time::{Duration, Instant};

    use crate::cache_runtime::{
        CacheRuntime, RedisProviderConfig, ScriptDefinition, ScriptRequest, ScriptValue,
    };
    use crate::core::FileSystem;
    use crate::plugins::memfs::MemFileSystem;

    use super::super::manager::{PathLockConfig, PathLockManager};
    use super::super::provider::{
        AcquisitionChange, AtomicAcquisition, PathLockHandleMode, PathLockProvider,
    };
    use super::super::types::{
        LockToken, PathLockError, PathLockHandoffRef, PathLockKind, PathLockRequest,
    };
    use super::*;

    const TEST_PTTL_ID: &str = "pathlock.test.pttl.v1";
    const TEST_PTTL_SCRIPT: &str = "return redis.call('PTTL', KEYS[1])";
    const TEST_HASH_ID: &str = "pathlock.test.hash.v1";
    const TEST_HASH_SCRIPT: &str = r#"
if ARGV[1] == "set" then
    redis.call("HSET", KEYS[1], ARGV[2], ARGV[3])
    return ARGV[3]
end
return redis.call("HGET", KEYS[1], ARGV[2])
"#;

    enum AtomicFault {
        WriteThenIo,
        Conflict,
    }

    /// Logical test provider for atomic Manager failure paths.
    struct FaultProvider {
        inner: super::super::provider::MemoryPathLockProvider,
        fault: AtomicFault,
        remove_calls: AtomicUsize,
    }

    impl FaultProvider {
        /// Create one provider with the selected injected fault.
        fn new(fault: AtomicFault) -> Self {
            Self {
                inner: super::super::provider::MemoryPathLockProvider::new(),
                fault,
                remove_calls: AtomicUsize::new(0),
            }
        }
    }

    #[async_trait]
    impl PathLockProvider for FaultProvider {
        /// Return the provider name used by test diagnostics.
        fn name(&self) -> &'static str {
            "fault"
        }

        /// Return logical handles to exercise the atomic Manager branch.
        fn handle_mode(&self) -> PathLockHandleMode {
            PathLockHandleMode::LogicalPath
        }

        /// Inject either an unknown write result or a conflict.
        async fn try_acquire_holders_atomic(
            &self,
            requests: &[PathLockRequest],
            owner_id: &str,
            holder_id: &str,
            now_ns: u128,
            _stale_before_ns: u128,
            _source: Option<&str>,
        ) -> PathLockResult<Option<AtomicAcquisitionResult>> {
            match self.fault {
                AtomicFault::WriteThenIo => {
                    let request = &requests[0];
                    self.inner
                        .try_create_token(
                            &request.path,
                            &LockToken::held(owner_id, holder_id, request.kind, now_ns),
                        )
                        .await?;
                    Err(PathLockError::Io(
                        "simulated response timeout after write".to_string(),
                    ))
                }
                AtomicFault::Conflict => Err(PathLockError::Conflict {
                    lock_path: "/race".to_string(),
                    owner: "owner-a".to_string(),
                    kind: PathLockKind::Exact,
                }),
            }
        }

        /// Return stored data or the stale conflict snapshot.
        async fn read_token(&self, lock_path: &str) -> PathLockResult<Option<LockToken>> {
            match self.fault {
                AtomicFault::Conflict => Ok(Some(token("owner-a", 1, PathLockKind::Exact))),
                AtomicFault::WriteThenIo => self.inner.read_token(lock_path).await,
            }
        }

        /// Delegate fallback token creation to memory storage.
        async fn try_create_token(&self, lock_path: &str, token: &LockToken) -> PathLockResult<()> {
            self.inner.try_create_token(lock_path, token).await
        }

        /// Delegate fallback token replacement to memory storage.
        async fn compare_and_write_token(
            &self,
            lock_path: &str,
            expected: &LockToken,
            replacement: &LockToken,
        ) -> PathLockResult<bool> {
            self.inner
                .compare_and_write_token(lock_path, expected, replacement)
                .await
        }

        /// Delegate refresh to memory storage.
        async fn refresh_token(
            &self,
            lock_path: &str,
            owner_id: &str,
            time_ns: u128,
        ) -> PathLockResult<bool> {
            self.inner.refresh_token(lock_path, owner_id, time_ns).await
        }

        /// Record cleanup calls and delegate removal.
        async fn remove_token(
            &self,
            lock_path: &str,
            owner_id: &str,
            force: bool,
        ) -> PathLockResult<bool> {
            self.remove_calls.fetch_add(1, Ordering::SeqCst);
            self.inner.remove_token(lock_path, owner_id, force).await
        }

        /// Delegate descendant scans to memory storage.
        async fn scan_descendant_locks(&self, root: &str) -> PathLockResult<Vec<String>> {
            self.inner.scan_descendant_locks(root).await
        }
    }

    /// Build a real Redis provider when the integration endpoint is configured.
    async fn test_provider(test_name: &str) -> Option<(Arc<CacheRuntime>, RedisPathLockProvider)> {
        let endpoint = std::env::var("REDIS_URL").ok()?;
        let runtime = CacheRuntime::redis(RedisProviderConfig {
            endpoints: vec![endpoint],
            command_timeout_ms: 1_000,
            ..RedisProviderConfig::default()
        })
        .await
        .unwrap();
        let service_name = format!("pathlock-test-{}-{test_name}", std::process::id());
        let provider = RedisPathLockProvider::new(runtime.clone(), &service_name, 1.0).unwrap();
        Some((runtime, provider))
    }

    /// Build two managers that share one service name.
    async fn test_managers(
        test_name: &str,
    ) -> Option<(Arc<CacheRuntime>, PathLockManager, PathLockManager, String)> {
        let endpoint = std::env::var("REDIS_URL").ok()?;
        let runtime = CacheRuntime::redis(RedisProviderConfig {
            endpoints: vec![endpoint],
            command_timeout_ms: 1_000,
            ..RedisProviderConfig::default()
        })
        .await
        .unwrap();
        let service_name = format!("pathlock-manager-test-{}-{test_name}", std::process::id());
        let config = PathLockConfig {
            provider: "cache".to_string(),
            service_name: Some(service_name.clone()),
            sqlite_path: None,
            lock_timeout_secs: 0.0,
            lock_expire_secs: 3.0,
        };
        let first_provider =
            Arc::new(RedisPathLockProvider::new(runtime.clone(), &service_name, 3.0).unwrap());
        let key = first_provider
            .tokens_key_for_path("/local/account-a")
            .unwrap();
        let first = PathLockManager::new(
            Arc::new(MemFileSystem::new()),
            first_provider,
            config.clone(),
        );
        let second = PathLockManager::new(
            Arc::new(MemFileSystem::new()),
            Arc::new(RedisPathLockProvider::new(runtime.clone(), &service_name, 3.0).unwrap()),
            config,
        );
        Some((runtime, first, second, key))
    }

    /// Read a comma-separated Redis endpoint environment variable.
    fn topology_endpoints(variable: &str) -> Option<Vec<String>> {
        let endpoints = std::env::var(variable).ok()?;
        let endpoints = endpoints
            .split(',')
            .map(str::trim)
            .filter(|endpoint| !endpoint.is_empty())
            .map(str::to_string)
            .collect::<Vec<_>>();
        (!endpoints.is_empty()).then_some(endpoints)
    }

    /// Exercise one provider against a configured Redis topology.
    async fn exercise_topology(
        mode: &str,
        endpoints: Vec<String>,
        master_name: Option<String>,
        test_name: &str,
    ) {
        let runtime = CacheRuntime::redis(RedisProviderConfig {
            mode: mode.to_string(),
            endpoints,
            master_name,
            connect_timeout_ms: 10_000,
            command_timeout_ms: 3_000,
            ..RedisProviderConfig::default()
        })
        .await
        .unwrap();
        let service_name = format!("pathlock-topology-test-{}-{test_name}", std::process::id());
        let provider = RedisPathLockProvider::new(runtime.clone(), &service_name, 3.0).unwrap();

        provider
            .try_acquire_batch_atomic(
                &[request("/local/account-a/root", PathLockKind::Tree)],
                "owner-a",
                100,
                0,
            )
            .await
            .unwrap();
        assert!(matches!(
            provider
                .try_acquire_batch_atomic(
                    &[request("/local/account-a/root/child", PathLockKind::Exact)],
                    "owner-b",
                    101,
                    0,
                )
                .await,
            Err(PathLockError::Conflict { .. })
        ));

        runtime.close().await.unwrap();
    }

    /// Build one lock token for provider tests.
    fn token(owner_id: &str, time_ns: u128, lock_type: PathLockKind) -> LockToken {
        LockToken {
            holders: Vec::new(),
            owner_id: owner_id.to_string(),
            time_ns,
            lock_type,
        }
    }

    /// Seed historical/legacy timestamps without a production time injection API.
    async fn seed_token(provider: &RedisPathLockProvider, path: &str, token: &LockToken) {
        provider
            .runtime
            .register_script(ScriptDefinition {
                id: TEST_HASH_ID,
                redis_lua: TEST_HASH_SCRIPT,
            })
            .unwrap();
        provider
            .execute(
                TEST_HASH_ID,
                provider.tokens_key_for_path(path).unwrap(),
                vec![
                    RedisPathLockProvider::bytes("set"),
                    RedisPathLockProvider::bytes(path),
                    RedisPathLockProvider::bytes(LockTokenCodec::encode(token)),
                ],
            )
            .await
            .unwrap();
    }

    /// Build one logical lock request.
    fn request(path: &str, kind: PathLockKind) -> PathLockRequest {
        PathLockRequest {
            path: path.to_string(),
            kind,
        }
    }
