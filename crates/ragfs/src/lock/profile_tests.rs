//! Batch-local read reuse must retain publication checks and rollback.
use super::*;
use crate::lock::provider::MemoryPathLockProvider;
use crate::metrics::{lock_metrics, RagfsMetricValue};
use crate::plugins::memfs::MemFileSystem;
use async_trait::async_trait;

struct ObservedProvider {
    inner: MemoryPathLockProvider,
    reads: StdMutex<HashMap<String, usize>>,
    creates: AtomicUsize,
    inject_after: usize,
    ancestor: String,
}

#[async_trait]
impl PathLockProvider for ObservedProvider {
    fn name(&self) -> &'static str {
        "observed"
    }
    async fn read_token(&self, path: &str) -> PathLockResult<Option<LockToken>> {
        *self.reads.lock().unwrap().entry(path.into()).or_default() += 1;
        self.inner.read_token(path).await
    }
    async fn try_create_token(&self, path: &str, token: &LockToken) -> PathLockResult<()> {
        self.inner.try_create_token(path, token).await?;
        if self.creates.fetch_add(1, AtomicOrdering::Relaxed) + 1 == self.inject_after {
            self.inner
                .try_create_token(
                    &self.ancestor,
                    &LockToken {
                        owner_id: "competitor".into(),
                        time_ns: PathLockManager::now_ns(),
                        lock_type: PathLockKind::Tree,
                    },
                )
                .await?;
        }
        Ok(())
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
        self.inner.remove_token(path, owner, force).await
    }
    async fn scan_descendant_locks(&self, path: &str) -> PathLockResult<Vec<String>> {
        self.inner.scan_descendant_locks(path).await
    }
}

async fn fixture(inject_after: usize) -> (PathLockManager, Arc<ObservedProvider>, Vec<String>) {
    let fs = Arc::new(MemFileSystem::new());
    fs.mkdir("/data", 0o755).await.unwrap();
    fs.mkdir("/data/user", 0o755).await.unwrap();
    let ancestor = LockPathResolver::new(fs.clone())
        .resolve_tree_lock_path("/data/user")
        .await
        .unwrap();
    let provider = Arc::new(ObservedProvider {
        inner: MemoryPathLockProvider::new(),
        reads: StdMutex::new(HashMap::new()),
        creates: AtomicUsize::new(0),
        inject_after,
        ancestor,
    });
    let manager = PathLockManager::new(fs, provider.clone(), PathLockConfig::default());
    let paths = (0..5).map(|id| format!("/data/user/f{id}")).collect();
    (manager, provider, paths)
}

#[tokio::test]
async fn shared_ancestors_are_fresh_before_each_token_and_each_batch() {
    let (manager, provider, paths) = fixture(usize::MAX).await;
    for round in 1..=2 {
        let lease = manager
            .acquire_exact_batch(&paths, Duration::ZERO, None)
            .await
            .unwrap();
        // Five fresh publication prechecks plus one fresh final-round read.
        assert_eq!(
            provider.reads.lock().unwrap()[&provider.ancestor],
            round * 6
        );
        manager.release(&lease).await.unwrap();
    }
    let metrics = lock_metrics(manager.metrics_snapshot().await);
    for phase in [
        "acquire_total",
        "release_total",
        "ancestor_postcheck",
        "token_acquire",
    ] {
        let metric = metrics
            .iter()
            .find(|metric| {
                metric.labels.get("phase").map(String::as_str) == Some(phase)
                    && metric.labels.get("outcome").map(String::as_str) == Some("success")
            })
            .unwrap();
        assert_eq!(metric.labels.len(), 3);
        assert_eq!(metric.labels["batch_size"], "2_5");
        assert!(
            matches!(&metric.value, RagfsMetricValue::Histogram { count, sum, .. } if *count > 0 && *sum > 0)
        );
    }
}

#[tokio::test]
async fn competitor_published_during_batch_is_detected_and_all_tokens_roll_back() {
    // A token published after the first creation must be caught by the next
    // precheck. One published after the last must be caught by the final check.
    for inject_after in [1, 5] {
        let (manager, provider, paths) = fixture(inject_after).await;
        assert!(matches!(
            manager
                .acquire_exact_batch(&paths, Duration::ZERO, None)
                .await,
            Err(PathLockError::Timeout { .. })
        ));
        assert_eq!(manager.metrics_snapshot().await.active_lock_count, 0);
        for path in &paths {
            let token_path = manager
                .resolver
                .resolve_exact_lock_path(path)
                .await
                .unwrap();
            assert_eq!(provider.inner.read_token(&token_path).await.unwrap(), None);
        }
        assert_eq!(
            provider
                .inner
                .read_token(&provider.ancestor)
                .await
                .unwrap()
                .unwrap()
                .owner_id,
            "competitor"
        );
        let metrics = lock_metrics(manager.metrics_snapshot().await);
        assert!(metrics
            .iter()
            .any(|metric| metric.labels.get("phase").map(String::as_str) == Some("rollback")));
        assert!(metrics
            .iter()
            .any(
                |metric| metric.labels.get("phase").map(String::as_str) == Some("acquire_total")
                    && metric.labels.get("outcome").map(String::as_str) == Some("timeout")
            ));
    }
}

#[tokio::test]
async fn interrupted_native_phase_records_cancellation() {
    let metrics = LockMetrics::default();
    let profile = LockProfile::new(metrics.phases.clone(), 13);
    let mut future = Box::pin(profile.measure::<()>("token_acquire", std::future::pending()));
    // Poll once so the phase timer exists, then drop the in-flight future.
    assert!(futures::poll!(&mut future).is_pending());
    drop(future);
    let exported = lock_metrics(metrics);
    assert!(exported
        .iter()
        .any(
            |metric| metric.labels.get("phase").map(String::as_str) == Some("token_acquire")
                && metric.labels.get("outcome").map(String::as_str) == Some("cancelled")
        ));
}
