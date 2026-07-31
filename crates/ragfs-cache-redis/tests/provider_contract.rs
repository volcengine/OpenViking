use bytes::Bytes;
use ragfs::cache::CacheProvider;
use ragfs_cache_redis::{RedisConfig, RedisProvider};

fn config(test_name: &str) -> Option<RedisConfig> {
    let endpoint = std::env::var("REDIS_URL").ok()?;
    Some(RedisConfig {
        endpoints: vec![endpoint],
        key_prefix: format!("ragfs-cache-test:{}:{}", std::process::id(), test_name),
        connect_timeout_ms: 30_000,
        command_timeout_ms: 1_000,
        default_ttl_seconds: 60,
        ..RedisConfig::default()
    })
}

async fn provider(test_name: &str) -> Option<RedisProvider> {
    Some(RedisProvider::connect(config(test_name)?).await.unwrap())
}

#[tokio::test]
async fn hit_miss_write_delete_and_exists_map_to_redis_operations() {
    let Some(provider) = provider("contract-basic").await else {
        return;
    };

    assert_eq!(provider.get("missing").await.unwrap(), None);
    provider
        .put("hit", Bytes::from_static(b"value"))
        .await
        .unwrap();
    assert!(provider.exists("hit").await.unwrap());
    assert_eq!(
        provider.get("hit").await.unwrap(),
        Some(Bytes::from_static(b"value"))
    );

    provider.delete("hit").await.unwrap();
    provider.delete("hit").await.unwrap();
    assert!(!provider.exists("hit").await.unwrap());
    assert_eq!(provider.get("hit").await.unwrap(), None);
    provider.flush().await.unwrap();
    provider.close().await.unwrap();
}

#[tokio::test]
async fn batch_operations_preserve_order_and_flush_only_known_keys() {
    let Some(provider) = provider("contract-batch").await else {
        return;
    };

    provider
        .batch_put(vec![
            ("one".into(), Bytes::from_static(b"1")),
            ("two".into(), Bytes::from_static(b"2")),
        ])
        .await
        .unwrap();

    assert_eq!(
        provider
            .batch_get(&["two".into(), "missing".into(), "one".into()])
            .await
            .unwrap(),
        vec![
            Some(Bytes::from_static(b"2")),
            None,
            Some(Bytes::from_static(b"1"))
        ]
    );
    assert!(provider.capabilities().batch_get);
    assert!(provider.capabilities().batch_put);
    assert!(provider.capabilities().native_ttl);

    provider.flush().await.unwrap();
    assert_eq!(provider.get("one").await.unwrap(), None);
    assert_eq!(provider.get("two").await.unwrap(), None);
    provider.close().await.unwrap();
}

#[tokio::test]
async fn set_px_ttl_expires_to_cache_miss() {
    let Some(mut config) = config("contract-ttl") else {
        return;
    };
    config.default_ttl_seconds = 1;
    let provider = RedisProvider::connect(config).await.unwrap();

    provider
        .put("ttl", Bytes::from_static(b"short"))
        .await
        .unwrap();
    assert_eq!(
        provider.get("ttl").await.unwrap(),
        Some(Bytes::from_static(b"short"))
    );

    tokio::time::sleep(std::time::Duration::from_millis(1_200)).await;
    assert_eq!(provider.get("ttl").await.unwrap(), None);
    provider.close().await.unwrap();
}
