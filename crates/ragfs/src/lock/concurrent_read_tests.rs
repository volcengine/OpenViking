//! Verify overlap, read limits, ordered errors, and publication boundaries.
use super::*;
use crate::lock::provider::MemoryPathLockProvider;
use crate::plugins::memfs::MemFileSystem;
use async_trait::async_trait;
use tokio::sync::{Notify, Semaphore};

struct GatedProvider {
    inner: MemoryPathLockProvider,
    permits: Semaphore,
    delayed_path: String,
    delayed_permit: Semaphore,
    errors: StdMutex<HashSet<String>>,
    started: AtomicUsize,
    finished: AtomicUsize,
    active: AtomicUsize,
    peak: AtomicUsize,
    creates: AtomicUsize,
    changed: Notify,
}

struct ActiveRead<'a>(&'a GatedProvider);

impl Drop for ActiveRead<'_> {
    fn drop(&mut self) {
        self.0.active.fetch_sub(1, AtomicOrdering::SeqCst);
        self.0.finished.fetch_add(1, AtomicOrdering::SeqCst);
        self.0.changed.notify_one();
    }
}

#[async_trait]
impl PathLockProvider for GatedProvider {
    fn name(&self) -> &'static str {
        "gated"
    }

    async fn read_token(&self, path: &str) -> PathLockResult<Option<LockToken>> {
        let active = self.active.fetch_add(1, AtomicOrdering::SeqCst) + 1;
        let _guard = ActiveRead(self);
        self.peak.fetch_max(active, AtomicOrdering::SeqCst);
        self.started.fetch_add(1, AtomicOrdering::SeqCst);
        self.changed.notify_one();
        self.permits.acquire().await.unwrap().forget();
        if path == self.delayed_path {
            self.delayed_permit.acquire().await.unwrap().forget();
        }
        if self.errors.lock().unwrap().contains(path) {
            return Err(PathLockError::Io("injected token read failure".into()));
        }
        self.inner.read_token(path).await
    }

    async fn try_create_token(&self, path: &str, token: &LockToken) -> PathLockResult<()> {
        // Token publication cannot overlap the preceding check's reads.
        assert_eq!(self.active.load(AtomicOrdering::SeqCst), 0);
        self.creates.fetch_add(1, AtomicOrdering::SeqCst);
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

    async fn refresh_token(&self, path: &str, owner: &str, time: u128) -> PathLockResult<bool> {
        self.inner.refresh_token(path, owner, time).await
    }

    async fn remove_token(&self, path: &str, owner: &str, force: bool) -> PathLockResult<bool> {
        assert_eq!(self.active.load(AtomicOrdering::SeqCst), 0);
        self.inner.remove_token(path, owner, force).await
    }

    async fn scan_descendant_locks(&self, path: &str) -> PathLockResult<Vec<String>> {
        self.inner.scan_descendant_locks(path).await
    }
}

async fn fixture(delayed_path: &str) -> (Arc<PathLockManager>, Arc<GatedProvider>) {
    let fs = Arc::new(MemFileSystem::new());
    fs.mkdir("/data", 0o755).await.unwrap();
    fs.mkdir("/data/user", 0o755).await.unwrap();
    let provider = Arc::new(GatedProvider {
        inner: MemoryPathLockProvider::new(),
        permits: Semaphore::new(0),
        delayed_path: delayed_path.into(),
        delayed_permit: Semaphore::new(0),
        errors: StdMutex::new(HashSet::new()),
        started: AtomicUsize::new(0),
        finished: AtomicUsize::new(0),
        active: AtomicUsize::new(0),
        peak: AtomicUsize::new(0),
        creates: AtomicUsize::new(0),
        changed: Notify::new(),
    });
    let manager = Arc::new(PathLockManager::new(
        fs,
        provider.clone(),
        PathLockConfig::default(),
    ));
    (manager, provider)
}

async fn wait_for(provider: &GatedProvider, counter: &AtomicUsize, count: usize) {
    tokio::time::timeout(Duration::from_secs(2), async {
        while counter.load(AtomicOrdering::SeqCst) < count {
            provider.changed.notified().await;
        }
    })
    .await
    .expect("independent reads must overlap without serial waiting");
}

fn foreign_token() -> LockToken {
    LockToken {
        owner_id: "other-owner".into(),
        time_ns: PathLockManager::now_ns(),
        lock_type: PathLockKind::Exact,
    }
}

#[tokio::test]
async fn ancestor_reads_overlap_and_finish_before_token_publication() {
    let (manager, provider) = fixture("").await;
    let task_manager = manager.clone();
    let task = tokio::spawn(async move {
        task_manager
            .acquire_exact("/data/user/file", Duration::ZERO, None)
            .await
    });
    wait_for(&provider, &provider.started, LOCK_CHECK_READ_CONCURRENCY).await;
    assert_eq!(
        provider.active.load(AtomicOrdering::SeqCst),
        LOCK_CHECK_READ_CONCURRENCY
    );
    assert_eq!(provider.creates.load(AtomicOrdering::SeqCst), 0);
    provider.permits.add_permits(100);
    let lease = task.await.unwrap().unwrap();
    assert_eq!(
        provider.peak.load(AtomicOrdering::SeqCst),
        LOCK_CHECK_READ_CONCURRENCY
    );
    assert_eq!(provider.creates.load(AtomicOrdering::SeqCst), 1);
    assert_eq!(provider.active.load(AtomicOrdering::SeqCst), 0);
    manager.release(&lease).await.unwrap();
}

#[tokio::test]
async fn failed_ancestor_check_drains_reads_without_caching_successful_parents() {
    let (manager, provider) = fixture("").await;
    provider
        .errors
        .lock()
        .unwrap()
        .insert("/data/user/.path.ovlock".into());
    let task = tokio::spawn(async move {
        let mut checked = HashSet::new();
        let result = manager
            .check_ancestor_tree_locks_once("/data/user/file", "owner", &mut checked)
            .await;
        (result, checked)
    });
    wait_for(&provider, &provider.started, LOCK_CHECK_READ_CONCURRENCY).await;
    provider.permits.add_permits(100);
    let (result, checked) = task.await.unwrap();
    assert!(matches!(result, Err(PathLockError::Io(_))));
    assert!(checked.is_empty());
    assert_eq!(provider.started.load(AtomicOrdering::SeqCst), 6);
    assert_eq!(provider.finished.load(AtomicOrdering::SeqCst), 6);
    assert_eq!(provider.active.load(AtomicOrdering::SeqCst), 0);
}

#[tokio::test]
async fn target_conflicts_and_io_errors_preserve_input_order_and_drain_the_check() {
    for first_is_io in [false, true] {
        let paths: Vec<_> = (0..6).map(|id| format!("/data/user/f{id}")).collect();
        let (manager, provider) = fixture(&paths[0]).await;
        let error_index = usize::from(!first_is_io);
        provider
            .errors
            .lock()
            .unwrap()
            .insert(paths[error_index].clone());
        let conflict_index = usize::from(first_is_io);
        provider
            .inner
            .try_create_token(&paths[conflict_index], &foreign_token())
            .await
            .unwrap();
        let first_path = paths[0].clone();
        let task = tokio::spawn(async move { manager.check_lock_paths(&paths, "owner").await });
        wait_for(&provider, &provider.started, LOCK_CHECK_READ_CONCURRENCY).await;
        provider.permits.add_permits(100);
        // Later reads finish first; the first path still controls error selection.
        wait_for(
            &provider,
            &provider.finished,
            LOCK_CHECK_READ_CONCURRENCY - 1,
        )
        .await;
        assert!(!task.is_finished());
        provider.delayed_permit.add_permits(1);
        let result = task.await.unwrap();
        if first_is_io {
            assert!(matches!(result, Err(PathLockError::Io(_))));
        } else {
            assert!(
                matches!(result, Err(PathLockError::Conflict { lock_path, .. }) if lock_path == first_path)
            );
        }
        assert_eq!(provider.started.load(AtomicOrdering::SeqCst), 6);
        assert_eq!(provider.finished.load(AtomicOrdering::SeqCst), 6);
        assert_eq!(provider.active.load(AtomicOrdering::SeqCst), 0);
    }
}

#[tokio::test]
async fn descendant_reads_are_bounded_and_exact_tokens_still_conflict() {
    let (manager, provider) = fixture("").await;
    for id in 0..8 {
        provider
            .inner
            .try_create_token(&format!("/data/user/f{id}"), &foreign_token())
            .await
            .unwrap();
    }
    let task = tokio::spawn(async move { manager.check_descendant_locks("/data", "owner").await });
    wait_for(&provider, &provider.started, LOCK_CHECK_READ_CONCURRENCY).await;
    provider.permits.add_permits(100);
    assert!(matches!(
        task.await.unwrap(),
        Err(PathLockError::Conflict { .. })
    ));
    assert_eq!(
        provider.peak.load(AtomicOrdering::SeqCst),
        LOCK_CHECK_READ_CONCURRENCY
    );
    assert_eq!(provider.finished.load(AtomicOrdering::SeqCst), 8);
}

#[tokio::test]
async fn cancelled_check_drops_inflight_reads_without_detached_work() {
    let (manager, provider) = fixture("").await;
    let paths: Vec<_> = (0..8).map(|id| format!("/data/user/f{id}")).collect();
    let task = tokio::spawn(async move { manager.check_lock_paths(&paths, "owner").await });
    wait_for(&provider, &provider.started, LOCK_CHECK_READ_CONCURRENCY).await;
    task.abort();
    assert!(task.await.unwrap_err().is_cancelled());
    assert_eq!(provider.active.load(AtomicOrdering::SeqCst), 0);
    assert_eq!(
        provider.started.load(AtomicOrdering::SeqCst),
        LOCK_CHECK_READ_CONCURRENCY
    );
    assert_eq!(
        provider.finished.load(AtomicOrdering::SeqCst),
        LOCK_CHECK_READ_CONCURRENCY
    );
    assert_eq!(provider.creates.load(AtomicOrdering::SeqCst), 0);
}
