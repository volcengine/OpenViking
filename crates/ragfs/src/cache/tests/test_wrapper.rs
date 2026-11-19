use super::super::envelope::QueryCacheEnvelope;
use super::*;
use crate::cache_runtime::{CacheRuntime, MemoryMockProvider};
use crate::core::{FileInfo, FileSystem, WriteFlag};
use crate::plugins::memfs::MemFileSystem;
use serde_json::json;
use std::sync::Arc;
use std::time::Duration;

const SERVICE_NAME: &str = "query-cache-test";

/// Return a sorted list of entry names for stable assertions.
fn sorted_names(entries: Vec<FileInfo>) -> Vec<String> {
    let mut names = entries
        .into_iter()
        .map(|entry| entry.name)
        .collect::<Vec<_>>();
    names.sort();
    names
}

/// Wait until asynchronous invalidation reaches the expected unlink count.
async fn wait_for_unlink_count(provider: &MemoryMockProvider, expected: u64) {
    for _ in 0..50 {
        if provider.unlink_count() >= expected {
            return;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    panic!(
        "expected at least {expected} unlink calls, got {}",
        provider.unlink_count()
    );
}

/// Wait until one delayed cache fill reaches the provider.
async fn wait_for_set_start(provider: &MemoryMockProvider) {
    for _ in 0..50 {
        if provider.max_concurrent_sets() > 0 {
            return;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    panic!("expected a cache fill to reach the provider");
}

/// Wait until one query index member disappears.
async fn wait_for_index_member_removed(
    runtime: &Arc<CacheRuntime>,
    index_key: &str,
    member: &str,
) {
    for _ in 0..50 {
        if runtime
            .zscan_match(index_key, member, 100)
            .await
            .unwrap()
            .is_empty()
        {
            return;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    panic!("expected query index member {member} to be removed");
}

/// Verifies read_dir hits the query cache and write invalidates the parent bucket.
#[tokio::test]
async fn query_cache_hits_and_invalidates_after_write() {
    let backend = MemFileSystem::new();
    backend.mkdir("/docs", 0o755).await.unwrap();
    backend
        .write("/docs/a.txt", b"a", 0, WriteFlag::Create)
        .await
        .unwrap();
    let provider = Arc::new(MemoryMockProvider::new());
    let runtime = CacheRuntime::memory_with_provider(Arc::clone(&provider));
    let cached = CachedFileSystem::with_runtime(Box::new(backend), runtime, SERVICE_NAME);

    assert_eq!(
        sorted_names(cached.read_dir("/docs", None, None, None, None).await.unwrap()),
        vec!["a.txt"]
    );
    assert_eq!(
        sorted_names(cached.read_dir("/docs", None, None, None, None).await.unwrap()),
        vec!["a.txt"]
    );
    let snapshot = cached.metrics().snapshot();
    assert_eq!(snapshot.query_misses, 1);
    assert_eq!(snapshot.query_hits, 1);
    assert_eq!(snapshot.backend_fallbacks, 1);
    assert_eq!(snapshot.puts, 1);

    let expected_unlinks = provider.unlink_count() + 1;
    cached
        .write("/docs/b.txt", b"b", 0, WriteFlag::Create)
        .await
        .unwrap();
    wait_for_unlink_count(&provider, expected_unlinks).await;

    assert_eq!(
        sorted_names(cached.read_dir("/docs", None, None, None, None).await.unwrap()),
        vec!["a.txt", "b.txt"]
    );
    assert_eq!(cached.metrics().snapshot().query_misses, 2);
}

/// Verifies invalidation prevents an older backend result from restoring stale cache data.
#[tokio::test]
async fn invalidation_rejects_an_older_query_fill() {
    let backend = MemFileSystem::new();
    backend.mkdir("/docs", 0o755).await.unwrap();
    backend
        .write("/docs/a.txt", b"a", 0, WriteFlag::Create)
        .await
        .unwrap();
    let provider = Arc::new(MemoryMockProvider::new().with_set_delay(Duration::from_millis(200)));
    let runtime = CacheRuntime::memory_with_provider(Arc::clone(&provider));
    let cached = Arc::new(CachedFileSystem::with_runtime(
        Box::new(backend),
        runtime,
        SERVICE_NAME,
    ));

    let stale_reader = {
        let cached = Arc::clone(&cached);
        tokio::spawn(async move { cached.read_dir("/docs", None, None, None, None).await })
    };
    wait_for_set_start(&provider).await;

    let mut buckets = Vec::new();
    cached.collect_path_change_buckets("/docs/b.txt", &mut buckets);
    buckets.sort();
    buckets.dedup();
    let expected_unlinks = provider.unlink_count() + buckets.len() as u64;
    cached
        .write("/docs/b.txt", b"b", 0, WriteFlag::Create)
        .await
        .unwrap();
    wait_for_unlink_count(&provider, expected_unlinks).await;

    assert_eq!(
        sorted_names(stale_reader.await.unwrap().unwrap()),
        vec!["a.txt"]
    );
    assert_eq!(
        sorted_names(
            cached
                .read_dir("/docs", None, None, None, None)
                .await
                .unwrap()
        ),
        vec!["a.txt", "b.txt"]
    );
}

/// Verifies PathLock sidecar files are filtered before query results are cached.
#[tokio::test]
async fn pathlock_files_are_not_stored_in_query_cache() {
    let backend = MemFileSystem::new();
    backend.mkdir("/docs", 0o755).await.unwrap();
    backend
        .write("/docs/visible.txt", b"visible", 0, WriteFlag::Create)
        .await
        .unwrap();
    backend
        .write("/docs/.path.ovlock", b"lock", 0, WriteFlag::Create)
        .await
        .unwrap();
    backend
        .write(
            "/docs/.exact.ovlock.visible.txt.1234",
            b"lock",
            0,
            WriteFlag::Create,
        )
        .await
        .unwrap();
    let runtime = CacheRuntime::memory_with_provider(Arc::new(MemoryMockProvider::new()));
    let cached =
        CachedFileSystem::with_runtime(Box::new(backend), Arc::clone(&runtime), SERVICE_NAME);

    assert_eq!(
        sorted_names(
            cached
                .read_dir("/docs", None, None, None, None)
                .await
                .unwrap()
        ),
        vec!["visible.txt"]
    );
    assert_eq!(
        sorted_names(cached.read_internal_dir("/docs").await.unwrap()),
        vec![
            ".exact.ovlock.visible.txt.1234",
            ".path.ovlock",
            "visible.txt",
        ]
    );

    let (request, query_hash) = canonical_query_request(json!({
        "path": "/docs",
        "offset": null,
        "limit": null,
        "sort_by": null,
        "sort_order": null,
    }))
    .unwrap();
    let bucket_key = query_bucket_key(SERVICE_NAME, "/docs", "read_dir");
    let cached_value = runtime
        .hget(&bucket_key, &query_hash)
        .await
        .unwrap()
        .expect("read_dir result should be cached");
    let envelope = QueryCacheEnvelope::<Vec<FileInfo>>::decode(&cached_value).unwrap();
    assert!(envelope.request_matches(&request));
    assert_eq!(sorted_names(envelope.into_payload()), vec!["visible.txt"]);

    cached.stat("/docs/.path.ovlock").await.unwrap();
    let (_, internal_hash) = canonical_query_request(json!({
        "path": "/docs/.path.ovlock",
    }))
    .unwrap();
    let internal_bucket = query_bucket_key(SERVICE_NAME, "/docs/.path.ovlock", "stat");
    assert_eq!(
        runtime
            .hget(&internal_bucket, &internal_hash)
            .await
            .unwrap(),
        None
    );
}

/// Verifies repeated changes trigger bounded cleanup for expired query index members.
#[tokio::test]
async fn query_index_gc_removes_expired_members_after_change_threshold() {
    let backend = MemFileSystem::new();
    backend.mkdir("/docs", 0o755).await.unwrap();
    let runtime = CacheRuntime::memory_with_provider(Arc::new(MemoryMockProvider::new()));
    let cached =
        CachedFileSystem::with_runtime(Box::new(backend), Arc::clone(&runtime), SERVICE_NAME);
    let index_key = cached.query_index_key();
    let expired_bucket = query_bucket_key(SERVICE_NAME, "/expired", "read_dir");
    let fresh_bucket = query_bucket_key(SERVICE_NAME, "/fresh", "read_dir");

    runtime
        .zadd(&index_key, 0.0, &expired_bucket)
        .await
        .unwrap();
    runtime
        .zadd(&index_key, u64::MAX as f64, &fresh_bucket)
        .await
        .unwrap();

    for index in 0..QUERY_INDEX_GC_TRIGGER_THRESHOLD {
        cached
            .write(&format!("/docs/{index}.txt"), b"x", 0, WriteFlag::Create)
            .await
            .unwrap();
    }

    wait_for_index_member_removed(&runtime, &index_key, &expired_bucket).await;
    assert_eq!(
        runtime
            .zscan_match(&index_key, &fresh_bucket, 100)
            .await
            .unwrap(),
        vec![fresh_bucket]
    );
}

/// Verifies the shared-Redis TTL uniformly overrides query bucket retention.
#[test]
fn shared_redis_ttl_applies_to_all_query_buckets() {
    let cached = CachedFileSystem::with_runtime(
        Box::new(MemFileSystem::new()),
        CacheRuntime::memory_with_provider(Arc::new(MemoryMockProvider::new())),
        SERVICE_NAME,
    )
    .with_shared_redis_ttl(Some(Duration::from_secs(60)));

    assert_eq!(cached.query_ttl_ms(), 60_000);

    let default = CachedFileSystem::with_runtime(
        Box::new(MemFileSystem::new()),
        CacheRuntime::memory_with_provider(Arc::new(MemoryMockProvider::new())),
        SERVICE_NAME,
    );

    assert_eq!(default.query_ttl_ms(), 300_000);
}

/// Verifies the shared-Redis TTL reaches the provider as the bucket expiration.
#[tokio::test]
async fn shared_redis_ttl_reaches_the_query_bucket_expiration() {
    let backend = MemFileSystem::new();
    backend.mkdir("/docs", 0o755).await.unwrap();
    backend
        .write("/docs/a.txt", b"a", 0, WriteFlag::Create)
        .await
        .unwrap();
    let provider = Arc::new(MemoryMockProvider::new());
    let runtime = CacheRuntime::memory_with_provider(Arc::clone(&provider));
    let cached = CachedFileSystem::with_runtime(Box::new(backend), runtime, SERVICE_NAME)
        .with_shared_redis_ttl(Some(Duration::from_secs(60)));

    cached
        .read_dir("/docs", None, None, None, None)
        .await
        .unwrap();

    let bucket_key = query_bucket_key(SERVICE_NAME, "/docs", "read_dir");
    let remaining = provider
        .expiration_remaining(&bucket_key)
        .await
        .expect("the read_dir bucket should carry an expiration");
    assert!(
        remaining <= Duration::from_secs(60),
        "the shared TTL must not exceed 60s, got {remaining:?}"
    );
    assert!(
        remaining >= Duration::from_secs(55),
        "the shared TTL should be close to 60s, got {remaining:?}"
    );
}

/// Verifies query buckets keep the provider TTL when no shared override is configured.
#[tokio::test]
async fn query_bucket_ttl_stays_on_the_provider_default() {
    let backend = MemFileSystem::new();
    backend.mkdir("/docs", 0o755).await.unwrap();
    backend
        .write("/docs/a.txt", b"a", 0, WriteFlag::Create)
        .await
        .unwrap();
    let provider = Arc::new(MemoryMockProvider::new());
    let runtime = CacheRuntime::memory_with_provider(Arc::clone(&provider));
    let cached = CachedFileSystem::with_runtime(Box::new(backend), runtime, SERVICE_NAME);

    cached
        .read_dir("/docs", None, None, None, None)
        .await
        .unwrap();

    let bucket_key = query_bucket_key(SERVICE_NAME, "/docs", "read_dir");
    let remaining = provider
        .expiration_remaining(&bucket_key)
        .await
        .expect("the read_dir bucket should carry an expiration");
    assert!(
        remaining <= Duration::from_secs(300),
        "the provider default TTL must stay at 300s, got {remaining:?}"
    );
    assert!(
        remaining >= Duration::from_secs(295),
        "the provider default TTL should be close to 300s, got {remaining:?}"
    );
}
