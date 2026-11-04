use super::*;
use bytes::Bytes;
use std::sync::Arc;
use std::time::Duration;

/// Verifies runtime default TTL follows the built-in Redis config defaults.
#[test]
fn default_ttl_comes_from_redis_provider_config() {
    let runtime = CacheRuntime::memory();
    assert_eq!(
        runtime.default_ttl(),
        Some(Duration::from_secs(
            RedisProviderConfig::default().default_ttl_seconds
        ))
    );

    let provider = Arc::new(MemoryMockProvider::new());
    let runtime = CacheRuntime::memory_with_provider_and_default_ttl(provider, None);
    assert_eq!(runtime.default_ttl(), None);
}

/// Verifies the HASH, ZSET, and UNLINK primitives needed by query cache.
#[tokio::test]
async fn query_cache_primitives_store_scan_and_unlink() {
    let provider = Arc::new(MemoryMockProvider::new());
    let runtime = CacheRuntime::memory_with_provider(Arc::clone(&provider));
    let bucket = "svc:ov:cache:v1:/docs/a:read_dir";
    let exact = "svc:ov:cache:v1:/docs:read_dir";
    let other = "svc:ov:cache:v1:/other:read_dir";

    assert_eq!(runtime.hget(bucket, "query").await.unwrap(), None);
    assert_eq!(
        runtime
            .hset(bucket, "query", Bytes::from_static(b"first"))
            .await
            .unwrap(),
        1
    );
    assert_eq!(
        runtime
            .hset(bucket, "query", Bytes::from_static(b"second"))
            .await
            .unwrap(),
        0
    );
    assert_eq!(
        runtime.hget(bucket, "query").await.unwrap(),
        Some(Bytes::from_static(b"second"))
    );

    assert_eq!(runtime.zadd("idx", 10.0, exact).await.unwrap(), 1);
    assert_eq!(runtime.zadd("idx", 11.0, bucket).await.unwrap(), 1);
    assert_eq!(runtime.zadd("idx", 12.0, other).await.unwrap(), 1);

    let mut descendants = runtime
        .zscan_match("idx", "svc:ov:cache:v1:/docs/*:*", 100)
        .await
        .unwrap();
    descendants.sort();
    assert_eq!(descendants, vec![bucket.to_string()]);

    assert_eq!(runtime.zrem("idx", &descendants).await.unwrap(), 1);
    assert_eq!(
        runtime
            .zscan_match("idx", "svc:ov:cache:v1:/docs/*:*", 100)
            .await
            .unwrap(),
        Vec::<String>::new()
    );

    assert_eq!(runtime.unlink(&[bucket.to_string()]).await.unwrap(), 1);
    assert_eq!(provider.unlink_count(), 1);
    assert_eq!(runtime.hget(bucket, "query").await.unwrap(), None);
}

/// Verifies sorted-set score range reads are ordered and bounded.
#[tokio::test]
async fn zrangebyscore_returns_bounded_expired_members() {
    let provider = Arc::new(MemoryMockProvider::new());
    let runtime = CacheRuntime::memory_with_provider(provider);

    assert_eq!(runtime.zadd("idx", 10.0, "expired-a").await.unwrap(), 1);
    assert_eq!(runtime.zadd("idx", 30.0, "fresh").await.unwrap(), 1);
    assert_eq!(runtime.zadd("idx", 20.0, "expired-b").await.unwrap(), 1);

    assert_eq!(
        runtime
            .zrangebyscore("idx", f64::NEG_INFINITY, 20.0, 1)
            .await
            .unwrap(),
        vec!["expired-a".to_string()]
    );
}

/// Verifies bounded GC preserves live buckets even when their index scores are expired.
#[tokio::test]
async fn delete_bucket_script_cleans_expired_index_members_bounded() {
    let provider = Arc::new(MemoryMockProvider::new());
    let runtime = CacheRuntime::memory_with_provider(provider);

    assert_eq!(runtime.zadd("idx", 10.0, "expired-a").await.unwrap(), 1);
    assert_eq!(runtime.zadd("idx", 20.0, "expired-b").await.unwrap(), 1);
    assert_eq!(runtime.zadd("idx", 30.0, "fresh").await.unwrap(), 1);
    runtime
        .hset("live", "query", Bytes::from_static(b"value"))
        .await
        .unwrap();
    runtime
        .expire("live", Duration::from_secs(60))
        .await
        .unwrap();
    assert_eq!(runtime.zadd("idx", 0.0, "live").await.unwrap(), 1);

    runtime
        .execute_script(ScriptRequest {
            script_id: "ragfs.cache.query.delete_bucket.v1".to_string(),
            keys: vec!["idx".to_string()],
            args: vec![
                Bytes::from_static(b"cleanup_expired"),
                Bytes::from(f64::NEG_INFINITY.to_string()),
                Bytes::from(20.0_f64.to_string()),
                Bytes::from_static(b"2"),
            ],
        })
        .await
        .unwrap();

    assert_eq!(
        runtime
            .zrangebyscore("idx", f64::NEG_INFINITY, f64::INFINITY, 10)
            .await
            .unwrap(),
        vec![
            "live".to_string(),
            "expired-b".to_string(),
            "fresh".to_string(),
        ]
    );
}
