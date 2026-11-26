use super::*;
use bytes::Bytes;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;

/// Build one scored member for CacheRuntime assertions.
fn scored(score: f64, member: &str) -> ScoredMember {
    ScoredMember {
        member: member.to_string(),
        score,
    }
}

/// Build one inclusive score range without pagination offset.
fn inclusive_range(min: f64, max: f64, limit: u32) -> ScoreRange {
    ScoreRange {
        min,
        min_exclusive: false,
        max,
        max_exclusive: false,
        offset: 0,
        limit,
    }
}

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

/// Verifies batch Hash operations preserve all fields and count only new fields.
#[tokio::test]
async fn hgetall_and_hset_round_trip_batch_fields() {
    let runtime = CacheRuntime::memory();

    assert_eq!(
        runtime
            .hset(
                "metadata",
                HashMap::from([
                    ("state".to_string(), Bytes::from_static(b"READY")),
                    ("version".to_string(), Bytes::from_static(b"1")),
                ]),
            )
            .await
            .unwrap(),
        2
    );
    assert_eq!(
        runtime
            .hset(
                "metadata",
                HashMap::from([
                    ("state".to_string(), Bytes::from_static(b"CLOSED")),
                    ("owner".to_string(), Bytes::from_static(b"worker")),
                ]),
            )
            .await
            .unwrap(),
        1
    );
    assert_eq!(
        runtime.hgetall("metadata").await.unwrap(),
        HashMap::from([
            ("state".to_string(), Bytes::from_static(b"CLOSED")),
            ("version".to_string(), Bytes::from_static(b"1")),
            ("owner".to_string(), Bytes::from_static(b"worker")),
        ])
    );
}

/// Verifies score range results retain scores and honor exclusive bounds and offsets.
#[tokio::test]
async fn zrangebyscore_returns_scored_members_with_boundaries_and_offsets() {
    let runtime = CacheRuntime::memory();

    runtime
        .zadd(
            "source",
            vec![
                ScoredMember {
                    member: "first".to_string(),
                    score: 10.0,
                },
                ScoredMember {
                    member: "second".to_string(),
                    score: 10.0,
                },
                ScoredMember {
                    member: "last".to_string(),
                    score: 20.0,
                },
            ],
        )
        .await
        .unwrap();

    assert_eq!(
        runtime
            .zrangebyscore(
                "source",
                ScoreRange {
                    min: 10.0,
                    min_exclusive: false,
                    max: 20.0,
                    max_exclusive: true,
                    offset: 1,
                    limit: 10,
                },
            )
            .await
            .unwrap(),
        vec![ScoredMember {
            member: "second".to_string(),
            score: 10.0,
        }]
    );
    assert_eq!(runtime.zcard("source").await.unwrap(), 3);
}

/// Verifies a key-routed server clock reports a current Unix millisecond value.
#[tokio::test]
async fn server_time_for_key_returns_unix_milliseconds() {
    let runtime = CacheRuntime::memory();
    let before = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as u64;

    let actual = runtime.server_time_for_key("source:{0}").await.unwrap();

    let after = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_millis() as u64;
    assert!((before..=after).contains(&actual));
}

/// Verifies closed runtimes reject script registration as well as provider operations.
#[tokio::test]
async fn close_rejects_script_registration() {
    let runtime = CacheRuntime::memory();
    runtime.close().await.unwrap();

    assert!(matches!(
        runtime.register_script(
            "ragfs.test.closed.v1".to_string(),
            "return 1".to_string(),
        ),
        Err(CacheError::Closed)
    ));
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
            .hset(
                bucket,
                HashMap::from([("query".to_string(), Bytes::from_static(b"first"))]),
            )
            .await
            .unwrap(),
        1
    );
    assert_eq!(
        runtime
            .hset(
                bucket,
                HashMap::from([("query".to_string(), Bytes::from_static(b"second"))]),
            )
            .await
            .unwrap(),
        0
    );
    assert_eq!(
        runtime.hget(bucket, "query").await.unwrap(),
        Some(Bytes::from_static(b"second"))
    );

    assert_eq!(runtime.zadd("idx", vec![scored(10.0, exact)]).await.unwrap(), 1);
    assert_eq!(runtime.zadd("idx", vec![scored(11.0, bucket)]).await.unwrap(), 1);
    assert_eq!(runtime.zadd("idx", vec![scored(12.0, other)]).await.unwrap(), 1);

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

    assert_eq!(runtime.zadd("idx", vec![scored(10.0, "expired-a")]).await.unwrap(), 1);
    assert_eq!(runtime.zadd("idx", vec![scored(30.0, "fresh")]).await.unwrap(), 1);
    assert_eq!(runtime.zadd("idx", vec![scored(20.0, "expired-b")]).await.unwrap(), 1);

    assert_eq!(
        runtime
            .zrangebyscore("idx", inclusive_range(f64::NEG_INFINITY, 20.0, 1))
            .await
            .unwrap(),
        vec![scored(10.0, "expired-a")]
    );
}

/// Verifies bounded GC preserves live buckets even when their index scores are expired.
#[tokio::test]
async fn delete_bucket_script_cleans_expired_index_members_bounded() {
    let provider = Arc::new(MemoryMockProvider::new());
    let runtime = CacheRuntime::memory_with_provider(provider);

    assert_eq!(runtime.zadd("idx", vec![scored(10.0, "expired-a")]).await.unwrap(), 1);
    assert_eq!(runtime.zadd("idx", vec![scored(20.0, "expired-b")]).await.unwrap(), 1);
    assert_eq!(runtime.zadd("idx", vec![scored(30.0, "fresh")]).await.unwrap(), 1);
    runtime
        .hset(
            "live",
            HashMap::from([("query".to_string(), Bytes::from_static(b"value"))]),
        )
        .await
        .unwrap();
    runtime
        .expire("live", Duration::from_secs(60))
        .await
        .unwrap();
    assert_eq!(runtime.zadd("idx", vec![scored(0.0, "live")]).await.unwrap(), 1);

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
            .zrangebyscore(
                "idx",
                inclusive_range(f64::NEG_INFINITY, f64::INFINITY, 10),
            )
            .await
            .unwrap(),
        vec![
            scored(0.0, "live"),
            scored(20.0, "expired-b"),
            scored(30.0, "fresh"),
        ]
    );
}
