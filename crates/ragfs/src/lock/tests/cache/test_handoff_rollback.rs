//! Failed handoff rollback must use Redis time and preserve other live holders.

use super::*;
use crate::cache_runtime::RedisProviderConfig;
use crate::core::{FileSystem, FsContextInner, PathLockContext, WriteFlag, FS_CTX};
use crate::lock::manager::{PathLockConfig, PathLockManager};
use crate::lock::wrapper::PathLockWrappedFS;
use crate::plugins::memfs::MemFileSystem;
use std::sync::atomic::AtomicUsize;

const PATH: &str = "/local/account/handoff";
const CLOCK_SKEW_NS: u128 = 31_000_000_000;

async fn fixture() -> Option<(Arc<CacheRuntime>, Arc<RedisPathLockProvider>)> {
    let endpoint = std::env::var("REDIS_URL").ok()?;
    let runtime = CacheRuntime::redis(RedisProviderConfig {
        endpoints: vec![endpoint],
        command_timeout_ms: 2_000,
        ..RedisProviderConfig::default()
    })
    .await
    .unwrap();
    let provider = Arc::new(
        RedisPathLockProvider::new(
            runtime.clone(),
            &format!("pathlock-handoff-{}", uuid::Uuid::new_v4()),
            1.0,
        )
        .unwrap(),
    );
    Some((runtime, provider))
}

fn request() -> PathLockRequest {
    PathLockRequest {
        path: PATH.into(),
        kind: PathLockKind::Tree,
    }
}

async fn cleanup(runtime: &CacheRuntime, provider: &RedisPathLockProvider) {
    runtime
        .del(&[provider.tokens_key_for_path(PATH).unwrap()])
        .await
        .unwrap();
    runtime.close().await.unwrap();
}

#[tokio::test]
async fn rollback_ignores_client_clock_and_preserves_renewed_sibling() {
    let Some((runtime, provider)) = fixture().await else {
        return;
    };
    // The same owner has a sibling and a transferable source at one directory.
    for holder in ["sibling", "source"] {
        provider
            .try_acquire_holders_atomic(&[request()], "a", holder, 0, 0, None)
            .await
            .unwrap();
    }
    let source = provider
        .read_token(PATH)
        .await
        .unwrap()
        .unwrap()
        .holders
        .into_iter()
        .find(|h| h.id == "source")
        .unwrap();
    provider
        .try_acquire_holders_atomic(&[request()], "a", "consumer", 0, 0, Some("source"))
        .await
        .unwrap();
    assert!(provider
        .mutate_holder(PATH, "a", "sibling", 0, 0, false)
        .await
        .unwrap());
    let renewed = provider
        .read_token(PATH)
        .await
        .unwrap()
        .unwrap()
        .holders
        .into_iter()
        .find(|h| h.id == "sibling")
        .unwrap();

    // A client clock 31 seconds ahead would consider both holders expired.
    let server_now = time_tests::now(&provider).await;
    provider
        .replace_holder(
            PATH,
            "a",
            "consumer",
            source.clone(),
            server_now + CLOCK_SKEW_NS - 1_000_000_000,
        )
        .await
        .unwrap();
    let token = provider.read_token(PATH).await.unwrap().unwrap();
    assert_eq!(token.holders.len(), 2);
    assert!(token.holders.contains(&renewed));
    assert!(token.holders.contains(&source));
    assert!(matches!(
        provider
            .try_acquire_holders_atomic(&[request()], "c", "contender", 0, 0, None)
            .await,
        Err(PathLockError::Conflict { .. })
    ));
    // Repeating rollback after the consumer disappeared must be a no-op.
    provider
        .replace_holder(PATH, "a", "consumer", source, u128::MAX)
        .await
        .unwrap();
    assert_eq!(provider.read_token(PATH).await.unwrap(), Some(token));
    cleanup(&runtime, &provider).await;
}

#[tokio::test]
async fn rollback_does_not_revive_expired_source_or_touch_new_owner() {
    let Some((runtime, provider)) = fixture().await else {
        return;
    };
    let now = time_tests::now(&provider).await;
    let expired_source = LockHolder {
        id: "source".into(),
        kind: PathLockKind::Tree,
        time_ns: now - CLOCK_SKEW_NS,
    };
    for sibling in [true, false] {
        let mut current = LockToken::held("a", "consumer", PathLockKind::Exact, now);
        if sibling {
            current.holders.push(
                LockToken::held("a", "sibling", PathLockKind::Exact, now)
                    .holders
                    .remove(0),
            );
        }
        current.recompute();
        time_tests::seed(&provider, PATH, &current).await;
        // A slow client clock must not revive an expired Redis source.
        provider
            .replace_holder(PATH, "a", "consumer", expired_source.clone(), 0)
            .await
            .unwrap();
        let remaining = provider.read_token(PATH).await.unwrap();
        if sibling {
            let remaining = remaining.unwrap();
            assert_eq!(remaining.holders.len(), 1);
            assert_eq!(remaining.holders[0].id, "sibling");
            assert_eq!(remaining.lock_type, PathLockKind::Exact);
        } else {
            assert!(remaining.is_none());
        }
    }
    let foreign = LockToken::held("c", "consumer", PathLockKind::Tree, now);
    time_tests::seed(&provider, PATH, &foreign).await;
    provider
        .replace_holder(PATH, "a", "consumer", expired_source, u128::MAX)
        .await
        .unwrap();
    assert_eq!(provider.read_token(PATH).await.unwrap(), Some(foreign));
    cleanup(&runtime, &provider).await;
}

#[tokio::test]
async fn rollback_preserves_source_that_was_concurrently_restored_and_renewed() {
    let Some((runtime, provider)) = fixture().await else {
        return;
    };
    let now = time_tests::now(&provider).await;
    let renewed = LockHolder {
        id: "source".into(),
        kind: PathLockKind::Tree,
        time_ns: now,
    };
    let mut current = LockToken::held("a", "consumer", PathLockKind::Exact, now);
    current.holders.push(renewed.clone());
    current.recompute();
    time_tests::seed(&provider, PATH, &current).await;
    provider
        .replace_holder(
            PATH,
            "a",
            "consumer",
            LockHolder {
                time_ns: now - CLOCK_SKEW_NS,
                ..renewed.clone()
            },
            u128::MAX,
        )
        .await
        .unwrap();
    let remaining = provider.read_token(PATH).await.unwrap().unwrap();
    assert_eq!(remaining.holders, vec![renewed]);
    cleanup(&runtime, &provider).await;
}

/// Delay only a successfully committed handoff response. Inject client clock
/// skew at the rollback boundary without changing Redis TIME or global clocks.
struct DelayedAdoptProvider {
    inner: Arc<RedisPathLockProvider>,
    rollback_calls: AtomicUsize,
}

#[async_trait]
impl PathLockProvider for DelayedAdoptProvider {
    fn name(&self) -> &'static str {
        "delayed-redis"
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
        stale_before: u128,
        source: Option<&str>,
    ) -> PathLockResult<Option<AtomicAcquisitionResult>> {
        let result = self
            .inner
            .try_acquire_holders_atomic(requests, owner, holder, now, stale_before, source)
            .await?;
        if source.is_some() {
            tokio::time::sleep(Duration::from_millis(850)).await;
        }
        Ok(result)
    }

    async fn replace_holder(
        &self,
        path: &str,
        owner: &str,
        holder: &str,
        restored: LockHolder,
        stale_before: u128,
    ) -> PathLockResult<()> {
        self.rollback_calls.fetch_add(1, Ordering::SeqCst);
        self.inner
            .replace_holder(
                path,
                owner,
                holder,
                restored,
                stale_before.saturating_add(CLOCK_SKEW_NS),
            )
            .await
    }

    async fn mutate_holder(
        &self,
        path: &str,
        owner: &str,
        holder: &str,
        now: u128,
        stale_before: u128,
        release: bool,
    ) -> PathLockResult<bool> {
        self.inner
            .mutate_holder(path, owner, holder, now, stale_before, release)
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
        self.inner.refresh_token(path, owner, now).await
    }

    async fn remove_token(&self, path: &str, owner: &str, force: bool) -> PathLockResult<bool> {
        self.inner.remove_token(path, owner, force).await
    }

    async fn scan_descendant_locks(&self, root: &str) -> PathLockResult<Vec<String>> {
        self.inner.scan_descendant_locks(root).await
    }
}

#[tokio::test]
async fn late_adopt_rollback_keeps_producer_authorized_and_contender_blocked() {
    let Some((runtime, provider)) = fixture().await else {
        return;
    };
    let config = PathLockConfig {
        provider: "cache".into(),
        service_name: Some(provider.service_name.clone()),
        sqlite_path: None,
        postgres_dsn_env: None,
        lock_timeout_secs: 0.0,
        lock_expire_secs: 1.0,
    };
    let fs = Arc::new(MemFileSystem::new());
    for directory in ["/local", "/local/account", PATH] {
        fs.mkdir(directory, 0o755).await.unwrap();
    }
    let first = Arc::new(PathLockManager::new(
        fs.clone(),
        provider.clone(),
        config.clone(),
    ));
    let delayed = Arc::new(DelayedAdoptProvider {
        inner: provider.clone(),
        rollback_calls: AtomicUsize::new(0),
    });
    let second = PathLockManager::new(
        Arc::new(MemFileSystem::new()),
        delayed.clone(),
        config.clone(),
    );
    let third = PathLockManager::new(Arc::new(MemFileSystem::new()), provider.clone(), config);
    let sibling = first
        .acquire_tree(PATH, Duration::ZERO, None)
        .await
        .unwrap();
    let source = first
        .acquire_tree(
            PATH,
            Duration::ZERO,
            Some((&sibling.lease.lease_ref, &sibling.ownership_ref)),
        )
        .await
        .unwrap();
    let handoff = first.to_handoff(&source);
    first.handoff(&source).await.unwrap();
    let before = provider.read_token(PATH).await.unwrap().unwrap();
    let sibling_before = before
        .holders
        .iter()
        .find(|h| h.id == sibling.lease.lease_ref)
        .unwrap()
        .time_ns;
    // Keep A's other holder confirmed while B waits for the committed response.
    let result = second.adopt(&handoff);
    let renew = async {
        loop {
            tokio::time::sleep(Duration::from_millis(100)).await;
            assert_eq!(first.refresh(&sibling).await.unwrap(), "refreshed");
        }
    };
    tokio::pin!(result, renew);
    let rejected = tokio::select! {
        result = &mut result => result,
        _ = &mut renew => unreachable!(),
    };
    assert!(matches!(
        rejected,
        Err(PathLockError::InvalidRequest(ref reason))
            if reason == "pathlock lease expired during acquisition"
    ));
    assert_eq!(delayed.rollback_calls.load(Ordering::SeqCst), 1);
    let remaining = provider.read_token(PATH).await.unwrap().unwrap();
    let sibling_after = remaining
        .holders
        .iter()
        .find(|h| h.id == sibling.lease.lease_ref)
        .unwrap();
    assert!(sibling_after.time_ns > sibling_before);
    assert!(remaining
        .holders
        .iter()
        .all(|h| h.id == sibling.lease.lease_ref || h.id == source.lease.lease_ref));
    assert!(matches!(
        third.acquire_tree(PATH, Duration::ZERO, None).await,
        Err(PathLockError::Timeout { .. })
    ));
    // A still has valid local authorization, so C must remain excluded.
    first
        .require_covered_lease_ref(&sibling.lease.lease_ref, &[request()])
        .await
        .unwrap();
    let wrapped = PathLockWrappedFS::new(first.clone(), fs.clone());
    let context = Arc::new(FsContextInner::with_pathlock(
        "account",
        PathLockContext {
            lease_ref: Some(sibling.lease.lease_ref.clone()),
            ..PathLockContext::default()
        },
    ));
    let file = format!("{PATH}/file");
    FS_CTX
        .scope(context, async {
            wrapped
                .write(&file, b"protected", 0, WriteFlag::Create)
                .await
        })
        .await
        .unwrap();
    assert_eq!(fs.read(&file, 0, 100).await.unwrap(), b"protected");
    assert_eq!(first.refresh(&sibling).await.unwrap(), "refreshed");
    first.release(&sibling).await.unwrap();
    cleanup(&runtime, &provider).await;
}
