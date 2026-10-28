//! Lost lease authorization, renewal deadlines, and bounded cleanup regressions.
use super::*;
use crate::core::{FsContextInner, PathLockContext, WriteFlag, FS_CTX};
use crate::lock::provider::{FilesystemPathLockProvider, MemoryPathLockProvider};
use crate::lock::wrapper::PathLockWrappedFS;
use crate::plugins::memfs::MemFileSystem;
use async_trait::async_trait;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

struct FaultProvider {
    inner: Arc<dyn PathLockProvider>,
    /// false: definite loss; true: uncertain transport failure.
    failures: StdMutex<HashMap<String, bool>>,
    fail_remove: AtomicBool,
    refresh_calls: AtomicUsize,
    remove_calls: AtomicUsize,
    delay: Duration,
}

impl FaultProvider {
    fn new(inner: Arc<dyn PathLockProvider>) -> Self {
        Self {
            inner,
            failures: StdMutex::new(HashMap::new()),
            fail_remove: AtomicBool::new(false),
            refresh_calls: AtomicUsize::new(0),
            remove_calls: AtomicUsize::new(0),
            delay: Duration::ZERO,
        }
    }
}

#[async_trait]
impl PathLockProvider for FaultProvider {
    fn name(&self) -> &'static str {
        "fault-injection"
    }
    fn handle_mode(&self) -> PathLockHandleMode {
        self.inner.handle_mode()
    }
    async fn try_acquire_batch_atomic(
        &self,
        requests: &[PathLockRequest],
        owner: &str,
        now: u128,
        stale: u128,
    ) -> PathLockResult<Option<Vec<AtomicAcquisition>>> {
        self.inner
            .try_acquire_batch_atomic(requests, owner, now, stale)
            .await
    }
    async fn read_token(&self, path: &str) -> PathLockResult<Option<LockToken>> {
        self.inner.read_token(path).await
    }
    async fn try_create_token(&self, path: &str, token: &LockToken) -> PathLockResult<()> {
        self.inner.try_create_token(path, token).await
    }
    async fn compare_and_write_token(
        &self,
        path: &str,
        expected: &LockToken,
        replacement: &LockToken,
    ) -> PathLockResult<bool> {
        self.inner
            .compare_and_write_token(path, expected, replacement)
            .await
    }
    async fn refresh_token(&self, path: &str, owner: &str, now: u128) -> PathLockResult<bool> {
        self.refresh_calls.fetch_add(1, Ordering::SeqCst);
        if !self.delay.is_zero() {
            tokio::time::sleep(self.delay).await;
        }
        let fault = self.failures.lock().unwrap().get(path).copied();
        match fault {
            Some(true) => Err(PathLockError::Io("injected refresh timeout".into())),
            Some(false) => Ok(false),
            None => self.inner.refresh_token(path, owner, now).await,
        }
    }
    async fn remove_token(&self, path: &str, owner: &str, force: bool) -> PathLockResult<bool> {
        assert!(!force, "cleanup must use ownership CAS");
        self.remove_calls.fetch_add(1, Ordering::SeqCst);
        if self.fail_remove.load(Ordering::SeqCst) {
            Err(PathLockError::Io("injected cleanup timeout".into()))
        } else {
            self.inner.remove_token(path, owner, force).await
        }
    }
    async fn scan_descendant_locks(&self, root: &str) -> PathLockResult<Vec<String>> {
        self.inner.scan_descendant_locks(root).await
    }
}

async fn fixture() -> (PathLockManager, Arc<FaultProvider>) {
    let fs = Arc::new(MemFileSystem::new());
    fs.mkdir("/data", 0o755).await.unwrap();
    let provider = Arc::new(FaultProvider::new(Arc::new(MemoryPathLockProvider::new())));
    let manager = PathLockManager::new(fs, provider.clone(), PathLockConfig::default());
    (manager, provider)
}

fn capability(lease: &OwnedPathLockLease) -> Option<(&str, &str)> {
    Some((&lease.lease.lease_ref, &lease.ownership_ref))
}

async fn age_token(
    manager: &PathLockManager,
    lease: &OwnedPathLockLease,
    path: &str,
    age: Duration,
) {
    let owner = manager
        .lease_registry
        .resolve(&lease.lease.lease_ref)
        .unwrap();
    owner
        .registry
        .lock()
        .await
        .token_health
        .get_mut(path)
        .unwrap()
        .last_confirmed_at = Instant::now() - age;
}

async fn lease_invalidation(
    manager: &PathLockManager,
    lease: &OwnedPathLockLease,
) -> LeaseInvalidation {
    let owner = manager
        .lease_registry
        .resolve(&lease.lease.lease_ref)
        .unwrap();
    let registry = owner.registry.lock().await;
    registry.entries[&lease.lease.lease_ref]
        .invalidation
        .unwrap()
}

#[tokio::test]
async fn loss_invalidates_whole_leases_sharing_tokens_but_not_exact_descendants() {
    let (manager, provider) = fixture().await;
    let tree = manager
        .acquire_tree("/data", Duration::ZERO, None)
        .await
        .unwrap();
    let shared = manager
        .acquire_tree_batch(
            &["/data".into(), "/other".into()],
            Duration::ZERO,
            capability(&tree),
        )
        .await
        .unwrap();
    let child = manager
        .acquire_exact("/data/child", Duration::ZERO, capability(&tree))
        .await
        .unwrap();
    assert_eq!(shared.lease.lock_paths[0], tree.lease.lock_paths[0]);
    assert_ne!(child.lease.lock_paths[0], tree.lease.lock_paths[0]);
    provider
        .failures
        .lock()
        .unwrap()
        .insert(tree.lease.lock_paths[0].clone(), false);
    assert_eq!(manager.refresh(&tree).await.unwrap(), "lost");
    for lease in [&tree, &shared] {
        assert_eq!(
            lease_invalidation(&manager, lease).await.reason,
            InvalidationReason::TokenLost
        );
        assert!(manager
            .get_owned_lease_by_ref(&lease.lease.lease_ref)
            .await
            .is_none());
        assert!(manager
            .require_covered_lease_ref(&lease.lease.lease_ref, &lease.lease.covered_paths)
            .await
            .is_err());
        assert!(manager
            .acquire_exact("/new", Duration::ZERO, capability(lease))
            .await
            .is_err());
        assert!(manager.handoff(lease).await.is_err());
        assert!(manager.refresh(lease).await.is_err());
        assert!(manager
            .get_releasable_lease_by_capability(&lease.lease.lease_ref, &lease.ownership_ref)
            .await
            .is_some());
    }
    assert!(manager
        .get_owned_lease_by_ref(&child.lease.lease_ref)
        .await
        .is_some());
    manager.release(&shared).await.unwrap();
    assert!(provider
        .read_token(&child.lease.lock_paths[0])
        .await
        .unwrap()
        .is_some());
    manager.release(&tree).await.unwrap();
    manager.release(&child).await.unwrap();
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
    let before = owner.registry.lock().await.token_health[&failed].last_confirmed_at;
    assert_eq!(manager.refresh(&lease).await.unwrap(), "failed");
    assert!(manager
        .get_owned_lease_by_ref(&lease.lease.lease_ref)
        .await
        .is_some());
    assert_eq!(
        owner.registry.lock().await.token_health[&failed].last_confirmed_at,
        before
    );
    assert_eq!(
        owner.registry.lock().await.token_health[&failed].consecutive_failures,
        1
    );
    provider.failures.lock().unwrap().clear();
    assert_eq!(manager.refresh(&lease).await.unwrap(), "refreshed");
    assert_eq!(
        owner.registry.lock().await.token_health[&failed].consecutive_failures,
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
async fn reentry_and_handoff_do_not_restart_token_deadlines() {
    let (manager, _) = fixture().await;
    let lease = manager
        .acquire_tree("/data", Duration::ZERO, None)
        .await
        .unwrap();
    let path = &lease.lease.lock_paths[0];
    age_token(&manager, &lease, path, Duration::from_secs(23)).await;
    let owner = manager
        .lease_registry
        .resolve(&lease.lease.lease_ref)
        .unwrap();
    let before = owner.registry.lock().await.token_health[path].last_confirmed_at;
    let reentrant = manager
        .acquire_tree("/data", Duration::ZERO, capability(&lease))
        .await
        .unwrap();
    assert_eq!(
        owner.registry.lock().await.token_health[path].last_confirmed_at,
        before
    );
    let handoff = manager.to_handoff(&lease);
    manager.handoff(&lease).await.unwrap();
    let adopted = manager.adopt(&handoff).await.unwrap();
    assert_eq!(
        owner.registry.lock().await.token_health[path].last_confirmed_at,
        before
    );
    age_token(&manager, &adopted, path, Duration::from_secs(25)).await;
    assert!(manager
        .get_owned_lease_by_ref(&adopted.lease.lease_ref)
        .await
        .is_none());
    assert!(manager
        .get_owned_lease_by_ref(&reentrant.lease.lease_ref)
        .await
        .is_none());
    manager.release(&reentrant).await.unwrap();
    manager.release(&adopted).await.unwrap();
}

#[tokio::test]
async fn invalidated_pending_handoff_cannot_be_adopted() {
    let (manager, provider) = fixture().await;
    let lease = manager
        .acquire_tree("/data", Duration::ZERO, None)
        .await
        .unwrap();
    let handoff = manager.to_handoff(&lease);
    manager.handoff(&lease).await.unwrap();
    let owner = manager
        .lease_registry
        .resolve(&lease.lease.lease_ref)
        .unwrap();
    let mut registry = owner.registry.lock().await;
    provider
        .failures
        .lock()
        .unwrap()
        .insert(lease.lease.lock_paths[0].clone(), false);
    assert_eq!(
        PathLockManager::refresh_entry_with(
            &manager.provider,
            &mut registry,
            &owner.owner_id,
            &lease.lease.lease_ref,
            PathLockManager::lease_validity(&manager.config),
        )
        .await,
        "lost"
    );
    drop(registry);
    assert!(manager.adopt(&handoff).await.is_err());
    assert!(PathLockManager::release_lease_paths_with(
        &manager.provider,
        &manager.lease_registry,
        owner,
        &lease.lease.lease_ref,
        None,
        None,
        Some(PathLockManager::cleanup_window(&manager.config)),
    )
    .await
    .unwrap());
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
async fn delayed_result_for_old_handoff_ref_cannot_update_the_adopted_lease() {
    let (manager, _) = fixture().await;
    let lease = manager
        .acquire_tree("/data", Duration::ZERO, None)
        .await
        .unwrap();
    let handoff = manager.to_handoff(&lease);
    manager.handoff(&lease).await.unwrap();
    let adopted = manager.adopt(&handoff).await.unwrap();
    let owner = manager
        .lease_registry
        .resolve(&adopted.lease.lease_ref)
        .unwrap();
    let mut registry = owner.registry.lock().await;
    let before = registry.token_health[&lease.lease.lock_paths[0]].last_confirmed_at;
    assert_eq!(
        registry.apply_refresh_results(
            &lease.lease.lease_ref,
            &lease.lease.lock_paths,
            vec![Ok(false)],
            Instant::now(),
            PathLockManager::lease_validity(&manager.config),
        ),
        "lost"
    );
    assert_eq!(
        registry.token_health[&lease.lease.lock_paths[0]].last_confirmed_at,
        before
    );
    assert!(registry.entries[&adopted.lease.lease_ref]
        .invalidation
        .is_none());
    drop(registry);
    assert!(manager.release(&lease).await.is_err());
    manager.release(&adopted).await.unwrap();
}

#[tokio::test]
async fn cross_manager_adoption_cannot_revive_an_expired_token() {
    let (producer, provider) = fixture().await;
    let lease = producer
        .acquire_tree("/data", Duration::ZERO, None)
        .await
        .unwrap();
    let handoff = producer.to_handoff(&lease);
    producer.handoff(&lease).await.unwrap();
    let path = &lease.lease.lock_paths[0];
    let token = provider.read_token(path).await.unwrap().unwrap();
    let expired = LockToken {
        time_ns: 1,
        ..token.clone()
    };
    assert!(provider
        .compare_and_write_token(path, &token, &expired)
        .await
        .unwrap());
    let consumer = PathLockManager::new(
        producer.resolver.fs.clone(),
        provider.clone(),
        PathLockConfig::default(),
    );
    assert!(consumer.adopt(&handoff).await.is_err());
    assert_eq!(provider.read_token(path).await.unwrap(), Some(expired));
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
        time_ns: PathLockManager::now_ns(),
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
        .refresh_tokens(
            &lease.lease.lock_paths,
            &lease.lease.owner_id,
            PathLockManager::now_ns(),
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
    let confirmed_before = owner.registry.lock().await.token_health[path].last_confirmed_at;
    proxy.delay_once();
    assert_eq!(manager.refresh(&lease).await.unwrap(), "failed");
    {
        let registry = owner.registry.lock().await;
        assert!(registry.entries[&lease.lease.lease_ref]
            .invalidation
            .is_none());
        assert_eq!(
            registry.token_health[path].last_confirmed_at,
            confirmed_before
        );
        assert_eq!(registry.token_health[path].consecutive_failures, 1);
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
        assert!(registry.token_health[path].last_confirmed_at > confirmed_before);
        assert_eq!(registry.token_health[path].consecutive_failures, 0);
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
