use super::*;
use crate::core::{Error, FileInfo, FsContextInner, PathLockContext, Result as FsResult, WriteFlag, FS_CTX};
use crate::lock::provider::{AtomicAcquisitionResult, FilesystemPathLockProvider, MemoryPathLockProvider};
use crate::lock::wrapper::PathLockWrappedFS;
use crate::plugins::memfs::MemFileSystem;
use async_trait::async_trait;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use tokio::sync::Barrier;

    struct FailNextRemoveProvider {
        inner: crate::lock::provider::MemoryPathLockProvider,
        fail_next_read: AtomicBool,
        fail_next_remove: AtomicBool,
        return_false_next_remove: AtomicBool,
        busy_next_takeover: AtomicBool,
        busy_takeover_count: AtomicUsize,
        reject_compare: bool,
        refresh_on_takeover: bool,
    }

    impl FailNextRemoveProvider {
        /// Build a memory provider whose next token read fails.
        fn with_read_failure() -> Self {
            Self {
                inner: crate::lock::provider::MemoryPathLockProvider::new(),
                fail_next_read: AtomicBool::new(true),
                fail_next_remove: AtomicBool::new(false),
                return_false_next_remove: AtomicBool::new(false),
                busy_next_takeover: AtomicBool::new(false),
                busy_takeover_count: AtomicUsize::new(0),
                reject_compare: false,
                refresh_on_takeover: false,
            }
        }

        /// Build a memory provider whose next takeover reports a retryable busy error.
        fn with_busy_takeover() -> Self {
            Self {
                inner: crate::lock::provider::MemoryPathLockProvider::new(),
                fail_next_read: AtomicBool::new(false),
                fail_next_remove: AtomicBool::new(false),
                return_false_next_remove: AtomicBool::new(false),
                busy_next_takeover: AtomicBool::new(true),
                busy_takeover_count: AtomicUsize::new(0),
                reject_compare: false,
                refresh_on_takeover: false,
            }
        }

        /// Build a memory provider that always reports compare-and-write misses.
        fn with_compare_miss() -> Self {
            Self {
                inner: crate::lock::provider::MemoryPathLockProvider::new(),
                fail_next_read: AtomicBool::new(false),
                fail_next_remove: AtomicBool::new(false),
                return_false_next_remove: AtomicBool::new(false),
                busy_next_takeover: AtomicBool::new(false),
                busy_takeover_count: AtomicUsize::new(0),
                reject_compare: true,
                refresh_on_takeover: false,
            }
        }
    }

    #[async_trait]
    impl PathLockProvider for FailNextRemoveProvider {
        async fn compare_and_remove_token(
            &self,
            path: &str,
            expected: &LockToken,
        ) -> PathLockResult<bool> {
            self.inner.compare_and_remove_token(path, expected).await
        }
        fn name(&self) -> &'static str {
            "fail-next-remove"
        }

        async fn read_token(&self, lock_path: &str) -> PathLockResult<Option<LockToken>> {
            if self.fail_next_read.swap(false, Ordering::SeqCst) {
                return Err(PathLockError::Io("injected read failure".to_string()));
            }
            self.inner.read_token(lock_path).await
        }

        async fn try_create_token(&self, lock_path: &str, token: &LockToken) -> PathLockResult<()> {
            self.inner.try_create_token(lock_path, token).await
        }

        async fn compare_and_write_token(
            &self,
            lock_path: &str,
            expected: &LockToken,
            replacement: &LockToken,
        ) -> PathLockResult<bool> {
            if self.busy_next_takeover.swap(false, Ordering::SeqCst) {
                self.busy_takeover_count.fetch_add(1, Ordering::SeqCst);
                return Err(PathLockError::Busy {
                    lock_path: lock_path.to_string(),
                    operation: "compare_and_write".to_string(),
                });
            }
            if self.reject_compare && replacement.holders.len() < expected.holders.len() {
                return Ok(false);
            }
            if self.refresh_on_takeover {
                self.inner
                    .refresh_token(lock_path, &expected.owner_id, PathLockManager::now_ns())
                    .await?;
            }
            self.inner
                .compare_and_write_token(lock_path, expected, replacement)
                .await
        }

        async fn refresh_token(
            &self,
            lock_path: &str,
            owner_id: &str,
            time_ns: u128,
        ) -> PathLockResult<bool> {
            self.inner.refresh_token(lock_path, owner_id, time_ns).await
        }

        async fn remove_token(
            &self,
            lock_path: &str,
            owner_id: &str,
            force: bool,
        ) -> PathLockResult<bool> {
            if self.return_false_next_remove.swap(false, Ordering::SeqCst) {
                return Ok(false);
            }
            if self.fail_next_remove.swap(false, Ordering::SeqCst) {
                return Err(PathLockError::Io("injected remove failure".to_string()));
            }
            if self.refresh_on_takeover {
                self.inner
                    .refresh_token(lock_path, owner_id, PathLockManager::now_ns())
                    .await?;
            }
            self.inner.remove_token(lock_path, owner_id, force).await
        }

        async fn scan_descendant_locks(&self, root: &str) -> PathLockResult<Vec<String>> {
            self.inner.scan_descendant_locks(root).await
        }
    }

    /// Build a manager backed by the real in-memory filesystem.
    async fn make_manager() -> PathLockManager {
        make_manager_with_config(PathLockConfig::default()).await
    }

    /// Build a manager with custom lock configuration.
    async fn make_manager_with_config(config: PathLockConfig) -> PathLockManager {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        fs.mkdir("/data/sub", 0o755).await.unwrap();
        let provider = Arc::new(crate::lock::provider::MemoryPathLockProvider::new());
        PathLockManager::new(fs, provider, config)
    }

    /// Build a manager and expose its real in-memory filesystem.
    async fn make_manager_with_fs() -> (PathLockManager, Arc<MemFileSystem>) {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        let provider = Arc::new(crate::lock::provider::MemoryPathLockProvider::new());
        (
            PathLockManager::new(fs.clone(), provider, PathLockConfig::default()),
            fs,
        )
    }

    async fn make_filesystem_managers() -> (PathLockManager, PathLockManager) {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        fs.mkdir("/data/a", 0o755).await.unwrap();
        fs.mkdir("/data/a/b", 0o755).await.unwrap();
        fs.mkdir("/data/a/c", 0o755).await.unwrap();
        let config = PathLockConfig::default();
        let first_provider = Arc::new(crate::lock::provider::FilesystemPathLockProvider::new(
            fs.clone(),
            config.lock_expire_secs,
        ));
        let second_provider = Arc::new(crate::lock::provider::FilesystemPathLockProvider::new(
            fs.clone(),
            config.lock_expire_secs,
        ));
        (
            PathLockManager::new(fs.clone(), first_provider, config.clone()),
            PathLockManager::new(fs, second_provider, config),
        )
    }

    async fn acquire_kind(
        manager: &PathLockManager,
        path: &str,
        kind: PathLockKind,
    ) -> PathLockResult<OwnedPathLockLease> {
        match kind {
            PathLockKind::Exact => manager.acquire_exact(path, Duration::ZERO, None).await,
            PathLockKind::Tree => manager.acquire_tree(path, Duration::ZERO, None).await,
        }
    }

struct FaultProvider {
    inner: Arc<dyn PathLockProvider>,
    /// false: definite loss; true: uncertain transport failure.
    failures: StdMutex<HashMap<String, bool>>,
    fail_remove: AtomicBool,
    refresh_calls: AtomicUsize,
    remove_calls: AtomicUsize,
    delay: Duration,
    /// Fail the next token write at this path with an I/O error.
    fail_write: StdMutex<Option<String>>,
    /// Bump a holder heartbeat just before the next token write.
    race_write: AtomicBool,
    /// Delete the source token immediately before CAS at this path.
    lose_source_on_write: StdMutex<Option<String>>,
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
            fail_write: StdMutex::new(None),
            race_write: AtomicBool::new(false),
            lose_source_on_write: StdMutex::new(None),
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
    async fn try_acquire_holders_atomic(
        &self,
        requests: &[PathLockRequest],
        owner: &str,
        holder: &str,
        now: u128,
        stale: u128,
        source: Option<&str>,
    ) -> PathLockResult<Option<AtomicAcquisitionResult>> {
        self.inner
            .try_acquire_holders_atomic(requests, owner, holder, now, stale, source)
            .await
    }
    async fn compare_and_remove_token(
        &self,
        path: &str,
        expected: &LockToken,
    ) -> PathLockResult<bool> {
        self.inner.compare_and_remove_token(path, expected).await
    }
    async fn mutate_holder(
        &self,
        path: &str,
        owner: &str,
        holder: &str,
        now: u128,
        stale: u128,
        release: bool,
    ) -> PathLockResult<bool> {
        if release {
            self.remove_calls.fetch_add(1, Ordering::SeqCst);
            if self.fail_remove.load(Ordering::SeqCst) {
                return Err(PathLockError::Io("injected cleanup timeout".into()));
            }
        } else {
            self.refresh_calls.fetch_add(1, Ordering::SeqCst);
            if !self.delay.is_zero() {
                tokio::time::sleep(self.delay).await;
            }
            let fault = self.failures.lock().unwrap().get(path).copied();
            match fault {
                Some(true) => return Err(PathLockError::Io("injected refresh timeout".into())),
                Some(false) => return Ok(false),
                None => {}
            }
        }
        self.inner
            .mutate_holder(path, owner, holder, now, stale, release)
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
        if self
            .fail_write
            .lock()
            .unwrap()
            .take_if(|p| p == path)
            .is_some()
        {
            return Err(PathLockError::Io("injected write failure".into()));
        }
        if self.race_write.swap(false, Ordering::SeqCst) {
            let mut refreshed = expected.clone();
            refreshed.holders[0].time_ns += 1;
            refreshed.recompute();
            assert!(
                self.inner
                    .compare_and_write_token(path, expected, &refreshed)
                    .await?
            );
        }
        let lose_source = self
            .lose_source_on_write
            .lock()
            .unwrap()
            .take_if(|p| p == path)
            .is_some();
        if lose_source {
            assert!(self.inner.compare_and_remove_token(path, expected).await?);
        }
        self.inner
            .compare_and_write_token(path, expected, replacement)
            .await
    }
    async fn refresh_token(&self, path: &str, owner: &str, now: u128) -> PathLockResult<bool> {
        self.inner.refresh_token(path, owner, now).await
    }
    async fn remove_token(&self, path: &str, owner: &str, force: bool) -> PathLockResult<bool> {
        self.inner.remove_token(path, owner, force).await
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

async fn filesystem_fixture() -> (Arc<MemFileSystem>, Arc<FaultProvider>, PathLockManager) {
    let fs = Arc::new(MemFileSystem::new());
    fs.mkdir("/data", 0o755).await.unwrap();
    let provider = Arc::new(FaultProvider::new(Arc::new(
        FilesystemPathLockProvider::new(fs.clone(), 30.0),
    )));
    let manager = PathLockManager::new(fs.clone(), provider.clone(), PathLockConfig::default());
    (fs, provider, manager)
}

fn holder_ids(token: &LockToken) -> Vec<&str> {
    token.holders.iter().map(|h| h.id.as_str()).collect()
}

fn capability(lease: &OwnedPathLockLease) -> Option<(&str, &str)> {
    Some((&lease.lease.lease_ref, &lease.ownership_ref))
}

fn health_key(lease: &OwnedPathLockLease, path: &str) -> String {
    format!("{}\0{path}", lease.lease.lease_ref)
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
        .get_mut(&health_key(lease, path))
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
