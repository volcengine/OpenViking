use super::*;

async fn takeover_rejects_protected_write(
    provider: Arc<dyn PathLockProvider>,
    fs: Arc<MemFileSystem>,
) {
    let manager = Arc::new(PathLockManager::new(
        fs.clone(),
        provider.clone(),
        PathLockConfig::default(),
    ));
    let lease = manager
        .acquire_exact("/data/file", Duration::ZERO, None)
        .await
        .unwrap();
    let lock_path = &lease.lease.lock_paths[0];
    let old = provider.read_token(lock_path).await.unwrap().unwrap();
    let replacement = LockToken {
        owner_id: "new-owner".into(),
        ..old.clone()
    };
    assert!(provider
        .compare_and_write_token(lock_path, &old, &replacement)
        .await
        .unwrap());
    assert_eq!(manager.refresh(&lease).await.unwrap(), "lost");
    let wrapped = PathLockWrappedFS::new(manager.clone(), fs.clone());
    let context = Arc::new(FsContextInner::with_pathlock(
        "",
        PathLockContext {
            lease_ref: Some(lease.lease.lease_ref.clone()),
            ..PathLockContext::default()
        },
    ));
    let result = FS_CTX
        .scope(context, async {
            wrapped
                .write("/data/file", b"corruption", 0, WriteFlag::Create)
                .await
        })
        .await;
    assert!(result.is_err(), "old lease must be refused before writing");
    assert!(fs.stat("/data/file").await.is_err());
    manager.release(&lease).await.unwrap();
    assert_eq!(
        provider.read_token(lock_path).await.unwrap(),
        Some(replacement)
    );
}

#[cfg(feature = "cache")]
struct RedisResponseDelayProxy {
    endpoint: String,
    delay_next_response: Arc<AtomicBool>,
    response_forwarded: Arc<tokio::sync::Notify>,
    task: tokio::task::JoinHandle<()>,
}

#[cfg(feature = "cache")]
impl RedisResponseDelayProxy {
    async fn start(endpoint: &str) -> Self {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        use tokio::net::{TcpListener, TcpStream};

        let mut url = url::Url::parse(endpoint).unwrap();
        assert_eq!(
            url.scheme(),
            "redis",
            "delay proxy requires a plain TCP Redis endpoint"
        );
        let host = url.host_str().unwrap().to_string();
        let port = url.port().unwrap_or(6379);
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        url.set_host(Some("127.0.0.1")).unwrap();
        url.set_port(Some(listener.local_addr().unwrap().port()))
            .unwrap();
        let delay_next_response = Arc::new(AtomicBool::new(false));
        let response_forwarded = Arc::new(tokio::sync::Notify::new());
        let armed = delay_next_response.clone();
        let forwarded = response_forwarded.clone();
        let task = tokio::spawn(async move {
            // JoinSet aborts every connection pump when this accept task is dropped.
            let mut connections = tokio::task::JoinSet::new();
            loop {
                tokio::select! {
                    accepted = listener.accept() => {
                        let (client, _) = accepted.unwrap();
                        let host = host.clone();
                        let armed = armed.clone();
                        let forwarded = forwarded.clone();
                        connections.spawn(async move {
                            let upstream = TcpStream::connect((host.as_str(), port)).await.unwrap();
                            client.set_nodelay(true).unwrap();
                            upstream.set_nodelay(true).unwrap();
                            let (mut client_read, mut client_write) = client.into_split();
                            let (mut server_read, mut server_write) = upstream.into_split();
                            let requests = async {
                                tokio::io::copy(&mut client_read, &mut server_write).await?;
                                server_write.shutdown().await
                            };
                            let responses = async {
                                let mut buffer = [0u8; 16 * 1024];
                                loop {
                                    let count = server_read.read(&mut buffer).await?;
                                    if count == 0 { return client_write.shutdown().await; }
                                    let delayed = armed.swap(false, Ordering::SeqCst);
                                    if delayed {
                                        // Redis has executed the command, but its reply arrives
                                        // after the real client's 20 ms deadline.
                                        tokio::time::sleep(Duration::from_millis(80)).await;
                                    }
                                    client_write.write_all(&buffer[..count]).await?;
                                    if delayed { forwarded.notify_one(); }
                                }
                            };
                            tokio::try_join!(requests, responses)
                        });
                    }
                    completed = connections.join_next(), if !connections.is_empty() => {
                        completed.unwrap().unwrap().unwrap();
                    }
                }
            }
        });
        Self {
            endpoint: url.to_string(),
            delay_next_response,
            response_forwarded,
            task,
        }
    }

    fn delay_once(&self) {
        assert!(!self.delay_next_response.swap(true, Ordering::SeqCst));
    }

    async fn wait_for_recovery(&self) {
        tokio::time::timeout(Duration::from_secs(2), self.response_forwarded.notified())
            .await
            .expect("delayed Redis response must be forwarded");
        assert!(!self.delay_next_response.load(Ordering::SeqCst));
    }
}

#[cfg(feature = "cache")]
impl Drop for RedisResponseDelayProxy {
    fn drop(&mut self) {
        self.task.abort();
    }
}

    #[tokio::test]
    async fn release_tree_lease_succeeds_after_locked_directory_is_deleted() {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        fs.mkdir("/data/delete-me", 0o755).await.unwrap();
        let provider = Arc::new(crate::lock::provider::FilesystemPathLockProvider::new(
            fs.clone(),
            PathLockConfig::default().lock_expire_secs,
        ));
        let mgr = PathLockManager::new(fs.clone(), provider, PathLockConfig::default());
        let lease = mgr
            .acquire_tree("/data/delete-me", Duration::ZERO, None)
            .await
            .unwrap();
        assert!(lease.lease.lock_paths[0].starts_with("/data/.exact.ovlock.delete-me."));

        fs.remove_all("/data/delete-me").await.unwrap();

        assert!(
            mgr.acquire_tree("/data/delete-me", Duration::ZERO, None)
                .await
                .is_err(),
            "the sidecar must keep the deleted path locked until release"
        );
        mgr.release(&lease).await.unwrap();
        let next = mgr
            .acquire_tree("/data/delete-me", Duration::ZERO, None)
            .await
            .unwrap();
        mgr.release(&next).await.unwrap();
        assert!(mgr
            .get_owned_lease_by_ref(&lease.lease.lease_ref)
            .await
            .is_none());
        assert_eq!(mgr.metrics_snapshot().await.active_lock_count, 0);
    }

    /// Keep coverage for paths that remain after a partial lease release.
    #[tokio::test]
    async fn release_selected_preserves_remaining_coverage() {
        let mgr = make_manager().await;
        let lease = mgr
            .acquire_exact_batch(
                &["/data/a.txt".to_string(), "/data/b.txt".to_string()],
                Duration::ZERO,
                None,
            )
            .await
            .unwrap();
        let released_lock_path = lease.lease.lock_paths[0].clone();

        mgr.release_selected(&lease, &[released_lock_path])
            .await
            .unwrap();

        assert!(mgr
            .require_covered_lease_ref(
                &lease.lease.lease_ref,
                &[PathLockRequest {
                    path: "/data/b.txt".to_string(),
                    kind: PathLockKind::Exact,
                }],
            )
            .await
            .is_ok());
        mgr.release(&lease).await.unwrap();
    }

    #[tokio::test]
    async fn pending_handoff_token_loss_invalidates_the_entire_lease() {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        let provider = Arc::new(crate::lock::provider::MemoryPathLockProvider::new());
        let config = PathLockConfig {
            lock_expire_secs: 0.3,
            ..PathLockConfig::default()
        };
        let producer = PathLockManager::new(fs.clone(), provider.clone(), config.clone());
        let consumer = PathLockManager::new(fs, provider.clone(), config);
        let lease = producer
            .acquire_exact_batch(
                &["/data/a".to_string(), "/data/b".to_string()],
                Duration::ZERO,
                None,
            )
            .await
            .unwrap();
        let first_lock_path = lease.lease.lock_paths[0].clone();
        let second_lock_path = lease.lease.lock_paths[1].clone();
        let handoff = producer.to_handoff(&lease);
        producer.handoff(&lease).await.unwrap();

        let adopted = consumer.adopt(&handoff).await.unwrap();
        consumer
            .release_selected(&adopted, &[first_lock_path.clone()])
            .await
            .unwrap();

        // Producer cleanup must never remove the consumer's remaining holder.
        tokio::time::sleep(Duration::from_millis(700)).await;
        assert_eq!(producer.observe().await.active_locks, 0);
        assert_eq!(provider.read_token(&first_lock_path).await.unwrap(), None);
        assert!(provider
            .read_token(&second_lock_path)
            .await
            .unwrap()
            .is_some());

        assert!(consumer
            .get_owned_lease_by_ref(&adopted.lease.lease_ref)
            .await
            .is_some());
        consumer.release(&adopted).await.unwrap();
        tokio::time::timeout(Duration::from_millis(400), async {
            loop {
                if producer.observe().await.active_locks == 0 {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("producer must forget every released handoff path");
    }

    /// Verify a downgrade CAS miss returns without dropping local lease state.
    #[tokio::test]
    async fn downgrade_compare_miss_is_bounded_and_retryable() {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        fs.mkdir("/data/sub", 0o755).await.unwrap();
        let provider = Arc::new(FailNextRemoveProvider::with_compare_miss());
        let mgr = PathLockManager::new(fs, provider, PathLockConfig::default());
        let tree = mgr
            .acquire_tree("/data/sub", Duration::ZERO, None)
            .await
            .unwrap();
        let capability = (&*tree.lease.lease_ref, &*tree.ownership_ref);
        let exact = mgr
            .acquire_exact("/data/sub", Duration::ZERO, Some(capability))
            .await
            .unwrap();

        let error = tokio::time::timeout(Duration::from_secs(1), mgr.release(&tree))
            .await
            .expect("downgrade CAS miss must not loop")
            .unwrap_err();
        assert!(matches!(error, PathLockError::Busy { .. }));
        assert!(mgr
            .get_owned_lease_by_capability(&tree.lease.lease_ref, &tree.ownership_ref)
            .await
            .is_none());
        assert!(mgr
            .get_releasable_lease_by_capability(&tree.lease.lease_ref, &tree.ownership_ref)
            .await
            .is_some());

        // After a CAS outage, references remain for the bounded cleanup worker.
        assert!(mgr.release(&exact).await.is_err());
        assert!(mgr.release(&tree).await.is_err());
    }

#[tokio::test]
async fn uncertain_partial_refresh_preserves_short_jitter_but_not_failed_token_deadline() {
    let (manager, provider) = fixture().await;
    let lease = manager
        .acquire_exact_batch(&["/data/a".into(), "/data/b".into()], Duration::ZERO, None)
        .await
        .unwrap();
    let failed = lease.lease.lock_paths[0].clone();
    provider
        .failures
        .lock()
        .unwrap()
        .insert(failed.clone(), true);
    age_token(&manager, &lease, &failed, Duration::from_secs(23)).await;
    let owner = manager
        .lease_registry
        .resolve(&lease.lease.lease_ref)
        .unwrap();
    let failed_key = health_key(&lease, &failed);
    let before = owner.registry.lock().await.token_health[&failed_key].last_confirmed_at;
    assert_eq!(manager.refresh(&lease).await.unwrap(), "failed");
    assert!(manager
        .get_owned_lease_by_ref(&lease.lease.lease_ref)
        .await
        .is_some());
    assert_eq!(
        owner.registry.lock().await.token_health[&failed_key].last_confirmed_at,
        before
    );
    assert_eq!(
        owner.registry.lock().await.token_health[&failed_key].consecutive_failures,
        1
    );
    provider.failures.lock().unwrap().clear();
    assert_eq!(manager.refresh(&lease).await.unwrap(), "refreshed");
    assert_eq!(
        owner.registry.lock().await.token_health[&failed_key].consecutive_failures,
        0
    );
    provider
        .failures
        .lock()
        .unwrap()
        .insert(failed.clone(), true);
    age_token(&manager, &lease, &failed, Duration::from_secs(25)).await;
    assert!(manager
        .require_covered_lease_ref(&lease.lease.lease_ref, &lease.lease.covered_paths)
        .await
        .is_err());
    let invalidation = lease_invalidation(&manager, &lease).await;
    assert_eq!(
        invalidation.reason,
        InvalidationReason::ConfirmationDeadlineExceeded
    );
    // A later definite-loss result must retain the initial deadline cause.
    {
        let mut registry = owner.registry.lock().await;
        assert_eq!(
            registry.apply_refresh_results(
                &lease.lease.lease_ref,
                &lease.lease.lock_paths,
                vec![Ok(false), Ok(true)],
                Instant::now(),
                PathLockManager::lease_validity(&manager.config),
            ),
            "lost"
        );
        assert_eq!(
            registry.entries[&lease.lease.lease_ref].invalidation,
            Some(invalidation)
        );
    }
    provider.failures.lock().unwrap().clear();
    assert!(
        manager.refresh(&lease).await.is_err(),
        "late recovery cannot resurrect"
    );
    manager.release(&lease).await.unwrap();
}

#[tokio::test]
async fn failed_cleanup_keeps_capability_and_refs_until_recovery() {
    let (manager, provider) = fixture().await;
    let lease = manager
        .acquire_tree("/data", Duration::ZERO, None)
        .await
        .unwrap();
    provider.fail_remove.store(true, Ordering::SeqCst);
    assert!(manager.release(&lease).await.is_err());
    let owner = manager
        .lease_registry
        .resolve(&lease.lease.lease_ref)
        .unwrap();
    let invalidation = lease_invalidation(&manager, &lease).await;
    assert_eq!(invalidation.reason, InvalidationReason::ReleaseFailed);
    assert!(manager
        .get_owned_lease_by_ref(&lease.lease.lease_ref)
        .await
        .is_none());
    assert!(manager
        .get_releasable_lease_by_capability(&lease.lease.lease_ref, "wrong")
        .await
        .is_none());
    assert!(manager.release(&lease).await.is_err());
    assert_eq!(
        owner.registry.lock().await.entries[&lease.lease.lease_ref].invalidation,
        Some(invalidation)
    );
    assert_eq!(
        owner.registry.lock().await.lock_refs[&lease.lease.lock_paths[0]],
        1
    );
    provider.fail_remove.store(false, Ordering::SeqCst);
    let cleanup = manager
        .get_releasable_lease_by_capability(&lease.lease.lease_ref, &lease.ownership_ref)
        .await
        .unwrap();
    manager.release(&cleanup).await.unwrap();
    assert!(owner.registry.lock().await.lock_refs.is_empty());
    assert!(owner.registry.lock().await.token_health.is_empty());
    assert!(manager
        .lease_registry
        .resolve(&lease.lease.lease_ref)
        .is_none());
}

#[tokio::test]
async fn bounded_cleanup_forgets_only_local_refs_and_preserves_valid_shared_token() {
    let (manager, provider) = fixture().await;
    let lease = manager
        .acquire_tree_batch(&["/data".into(), "/other".into()], Duration::ZERO, None)
        .await
        .unwrap();
    let valid = manager
        .acquire_tree("/other", Duration::ZERO, capability(&lease))
        .await
        .unwrap();
    provider
        .failures
        .lock()
        .unwrap()
        .insert(lease.lease.lock_paths[0].clone(), false);
    assert_eq!(manager.refresh(&lease).await.unwrap(), "lost");
    let invalidation = lease_invalidation(&manager, &lease).await;
    assert_eq!(invalidation.reason, InvalidationReason::TokenLost);
    provider.fail_remove.store(true, Ordering::SeqCst);
    assert!(manager.release(&lease).await.is_err());
    assert_eq!(lease_invalidation(&manager, &lease).await, invalidation);
    let owner = manager
        .lease_registry
        .resolve(&lease.lease.lease_ref)
        .unwrap();
    let calls = provider.remove_calls.load(Ordering::SeqCst);
    assert!(PathLockManager::release_lease_paths_with(
        &manager.provider,
        &manager.lease_registry,
        owner.clone(),
        &lease.lease.lease_ref,
        None,
        None,
        Some(Duration::ZERO),
    )
    .await
    .unwrap());
    assert_eq!(provider.remove_calls.load(Ordering::SeqCst), calls);
    assert_eq!(
        owner.registry.lock().await.lock_refs[&valid.lease.lock_paths[0]],
        1
    );
    assert!(manager
        .get_owned_lease_by_ref(&valid.lease.lease_ref)
        .await
        .is_some());
    assert!(!PathLockManager::release_lease_paths_with(
        &manager.provider,
        &manager.lease_registry,
        owner,
        &lease.lease.lease_ref,
        None,
        None,
        Some(Duration::ZERO),
    )
    .await
    .unwrap());
    provider.fail_remove.store(false, Ordering::SeqCst);
    manager.release(&valid).await.unwrap();
}

#[tokio::test]
async fn late_refresh_success_does_not_resurrect_an_expired_lease() {
    let fs = Arc::new(MemFileSystem::new());
    fs.mkdir("/data", 0o755).await.unwrap();
    let mut fault = FaultProvider::new(Arc::new(MemoryPathLockProvider::new()));
    fault.delay = Duration::from_millis(100);
    // Retain the invalidated entry while inspecting it; heartbeat cleanup races
    // the explicit caller after the delayed refresh releases the owner mutex.
    fault.fail_remove.store(true, Ordering::SeqCst);
    let manager = PathLockManager::new(
        fs,
        Arc::new(fault),
        PathLockConfig {
            lock_expire_secs: 0.2,
            ..PathLockConfig::default()
        },
    );
    let lease = manager
        .acquire_tree("/data", Duration::ZERO, None)
        .await
        .unwrap();
    age_token(
        &manager,
        &lease,
        &lease.lease.lock_paths[0],
        Duration::from_millis(100),
    )
    .await;
    assert_eq!(manager.refresh(&lease).await.unwrap(), "lost");
    assert!(manager
        .get_owned_lease_by_ref(&lease.lease.lease_ref)
        .await
        .is_none());
    assert!(manager.release(&lease).await.is_err());
}

#[tokio::test]
async fn background_refresh_invalidates_and_bounds_cleanup_during_outage() {
    for definite_loss in [true, false] {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        let provider = Arc::new(FaultProvider::new(Arc::new(MemoryPathLockProvider::new())));
        let manager = PathLockManager::new(
            fs,
            provider.clone(),
            PathLockConfig {
                lock_expire_secs: 0.3,
                ..PathLockConfig::default()
            },
        );
        let lease = manager
            .acquire_tree("/data", Duration::ZERO, None)
            .await
            .unwrap();
        provider
            .failures
            .lock()
            .unwrap()
            .insert(lease.lease.lock_paths[0].clone(), !definite_loss);
        provider.fail_remove.store(true, Ordering::SeqCst);
        let owner = manager
            .lease_registry
            .resolve(&lease.lease.lease_ref)
            .unwrap();
        tokio::time::timeout(Duration::from_secs(2), async {
            loop {
                if owner
                    .registry
                    .lock()
                    .await
                    .entries
                    .get(&lease.lease.lease_ref)
                    .is_some_and(|e| e.invalidation.is_some())
                {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .unwrap();
        assert!(manager
            .get_owned_lease_by_ref(&lease.lease.lease_ref)
            .await
            .is_none());
        let calls = provider.refresh_calls.load(Ordering::SeqCst);
        tokio::time::timeout(Duration::from_secs(2), async {
            while manager
                .lease_registry
                .resolve(&lease.lease.lease_ref)
                .is_some()
            {
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .unwrap();
        assert_eq!(provider.refresh_calls.load(Ordering::SeqCst), calls);
        assert!(provider.remove_calls.load(Ordering::SeqCst) >= 2);
        assert!(owner.registry.lock().await.entries.is_empty());
        assert!(owner.registry.lock().await.lock_refs.is_empty());
    }
}

#[tokio::test]
async fn takeover_denies_writes_and_cleanup_preserves_new_owner_for_builtin_providers() {
    for filesystem in [false, true] {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        let provider: Arc<dyn PathLockProvider> = if filesystem {
            Arc::new(FilesystemPathLockProvider::new(fs.clone(), 30.0))
        } else {
            Arc::new(MemoryPathLockProvider::new())
        };
        takeover_rejects_protected_write(provider, fs).await;
    }
    let dir = tempfile::TempDir::new().unwrap();
    let provider = Arc::new(
        crate::lock::SQLitePathLockProvider::new(
            dir.path().join("locks.db").to_str().unwrap(),
            "lost-lease",
        )
        .unwrap(),
    );
    let fs = Arc::new(MemFileSystem::new());
    fs.mkdir("/data", 0o755).await.unwrap();
    takeover_rejects_protected_write(provider, fs).await;
}

#[cfg(feature = "cache")]
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn redis_20ms_renew_timeout_preserves_lease_and_recovers_after_network_delay() {
    let Ok(endpoint) = std::env::var("REDIS_URL") else {
        return;
    };
    let proxy = RedisResponseDelayProxy::start(&endpoint).await;
    let runtime =
        crate::cache_runtime::CacheRuntime::redis(crate::cache_runtime::RedisProviderConfig {
            endpoints: vec![proxy.endpoint.clone()],
            command_timeout_ms: 20,
            ..crate::cache_runtime::RedisProviderConfig::default()
        })
        .await
        .unwrap();
    let provider = Arc::new(
        crate::lock::RedisPathLockProvider::new(
            runtime.clone(),
            &format!("renew-network-delay-{}", Uuid::new_v4()),
            30.0,
        )
        .unwrap(),
    );
    let dir = tempfile::TempDir::new().unwrap();
    let fs = Arc::new(
        crate::plugins::localfs::LocalFileSystem::new(dir.path().to_str().unwrap()).unwrap(),
    );
    fs.mkdir("/local", 0o755).await.unwrap();
    fs.mkdir("/local/account", 0o755).await.unwrap();
    let file = "/local/account/file";
    let manager = Arc::new(PathLockManager::new(
        fs.clone(),
        provider.clone(),
        PathLockConfig::default(),
    ));
    let lease = manager
        .acquire_exact(file, Duration::ZERO, None)
        .await
        .unwrap();
    let path = &lease.lease.lock_paths[0];
    // Load the real renewal script before arming the proxy so the delayed
    // response belongs to renewal rather than SCRIPT LOAD.
    assert_eq!(manager.refresh(&lease).await.unwrap(), "refreshed");

    proxy.delay_once();
    let result = provider
        .refresh_holders(
            &lease.lease.lock_paths,
            &lease.lease.owner_id,
            &lease.lease.lease_ref,
            PathLockManager::now_ns(),
            0,
        )
        .await;
    assert_eq!(result.len(), 1);
    assert!(
        matches!(&result[0], Err(PathLockError::Io(message))
        if message.to_ascii_lowercase().contains("timeout")
            || message.contains("exceeded 20 ms")),
        "expected a real Redis command timeout: {result:?}"
    );
    proxy.wait_for_recovery().await;
    assert_eq!(manager.refresh(&lease).await.unwrap(), "refreshed");

    let owner = manager
        .lease_registry
        .resolve(&lease.lease.lease_ref)
        .unwrap();
    let health = health_key(&lease, path);
    let confirmed_before = owner.registry.lock().await.token_health[&health].last_confirmed_at;
    proxy.delay_once();
    assert_eq!(manager.refresh(&lease).await.unwrap(), "failed");
    {
        let registry = owner.registry.lock().await;
        assert!(registry.entries[&lease.lease.lease_ref]
            .invalidation
            .is_none());
        assert_eq!(
            registry.token_health[&health].last_confirmed_at,
            confirmed_before
        );
        assert_eq!(registry.token_health[&health].consecutive_failures, 1);
    }
    assert!(manager
        .get_owned_lease_by_capability(&lease.lease.lease_ref, &lease.ownership_ref)
        .await
        .is_some());
    manager
        .require_covered_lease_ref(&lease.lease.lease_ref, &lease.lease.covered_paths)
        .await
        .unwrap();
    let wrapped = PathLockWrappedFS::new(manager.clone(), fs.clone());
    let context = Arc::new(FsContextInner::with_pathlock(
        "account",
        PathLockContext {
            lease_ref: Some(lease.lease.lease_ref.clone()),
            ..PathLockContext::default()
        },
    ));
    FS_CTX
        .scope(context.clone(), async {
            wrapped.write(file, b"before", 0, WriteFlag::Create).await
        })
        .await
        .unwrap();
    assert_eq!(fs.read(file, 0, 100).await.unwrap(), b"before");

    proxy.wait_for_recovery().await;
    assert_eq!(manager.refresh(&lease).await.unwrap(), "refreshed");
    {
        let registry = owner.registry.lock().await;
        assert!(registry.entries[&lease.lease.lease_ref]
            .invalidation
            .is_none());
        assert!(registry.token_health[&health].last_confirmed_at > confirmed_before);
        assert_eq!(registry.token_health[&health].consecutive_failures, 0);
    }
    let token = provider.read_token(path).await.unwrap().unwrap();
    assert_eq!(token.owner_id, lease.lease.owner_id);
    FS_CTX
        .scope(context, async {
            wrapped.write(file, b"after!", 0, WriteFlag::Create).await
        })
        .await
        .unwrap();
    assert_eq!(fs.read(file, 0, 100).await.unwrap(), b"after!");
    manager.release(&lease).await.unwrap();
    assert!(provider.read_token(path).await.unwrap().is_none());
    runtime.close().await.unwrap();
}

#[cfg(feature = "cache")]
#[tokio::test]
async fn redis_takeover_denies_writes_and_cleanup_preserves_new_owner() {
    let Ok(endpoint) = std::env::var("REDIS_URL") else {
        return;
    };
    let runtime =
        crate::cache_runtime::CacheRuntime::redis(crate::cache_runtime::RedisProviderConfig {
            endpoints: vec![endpoint],
            command_timeout_ms: 1_000,
            ..crate::cache_runtime::RedisProviderConfig::default()
        })
        .await
        .unwrap();
    let provider = Arc::new(
        crate::lock::cache_provder::RedisPathLockProvider::new(
            runtime.clone(),
            &format!("lost-lease-{}", Uuid::new_v4()),
            30.0,
        )
        .unwrap(),
    );
    let dir = tempfile::TempDir::new().unwrap();
    let fs = Arc::new(
        crate::plugins::localfs::LocalFileSystem::new(dir.path().to_str().unwrap()).unwrap(),
    );
    fs.mkdir("/local", 0o755).await.unwrap();
    fs.mkdir("/local/account", 0o755).await.unwrap();
    fs.write("/local/account/file", b"original", 0, WriteFlag::Create)
        .await
        .unwrap();
    // Redis handles are scoped logical paths.
    let manager = Arc::new(PathLockManager::new(
        fs.clone(),
        provider.clone(),
        PathLockConfig::default(),
    ));
    let lease = manager
        .acquire_exact("/local/account/file", Duration::ZERO, None)
        .await
        .unwrap();
    let path = &lease.lease.lock_paths[0];
    let old = provider.read_token(path).await.unwrap().unwrap();
    let replacement = LockToken {
        owner_id: "new-owner".into(),
        ..old.clone()
    };
    assert!(provider
        .compare_and_write_token(path, &old, &replacement)
        .await
        .unwrap());
    // Redis assigns the takeover confirmation time; cleanup must preserve the
    // actual server-written snapshot rather than the requested client token.
    let replacement = provider.read_token(path).await.unwrap().unwrap();
    assert_eq!(replacement.owner_id, "new-owner");
    assert_eq!(replacement.lock_type, old.lock_type);
    assert_eq!(manager.refresh(&lease).await.unwrap(), "lost");
    let wrapped = PathLockWrappedFS::new(manager.clone(), fs.clone());
    let context = Arc::new(FsContextInner::with_pathlock(
        "account",
        PathLockContext {
            lease_ref: Some(lease.lease.lease_ref.clone()),
            ..PathLockContext::default()
        },
    ));
    assert!(FS_CTX
        .scope(context, async {
            wrapped
                .write("/local/account/file", b"corruption", 0, WriteFlag::Create)
                .await
        })
        .await
        .is_err());
    assert_eq!(
        fs.read("/local/account/file", 0, 100).await.unwrap(),
        b"original"
    );
    manager.release(&lease).await.unwrap();
    assert_eq!(provider.read_token(path).await.unwrap(), Some(replacement));
    provider
        .remove_token(path, "new-owner", false)
        .await
        .unwrap();
    runtime.close().await.unwrap();
}
