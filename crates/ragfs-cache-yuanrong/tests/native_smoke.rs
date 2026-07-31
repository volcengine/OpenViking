#![cfg(feature = "yuanrong-native")]

use bytes::Bytes;
use ragfs::cache::CacheProvider;
use ragfs_cache_yuanrong::{YuanrongConfig, YuanrongProvider};

fn integration_enabled() -> bool {
    std::env::var("OPENVIKING_RUN_YUANRONG_INTEGRATION").as_deref() == Ok("true")
}

fn config() -> YuanrongConfig {
    YuanrongConfig {
        host: std::env::var("YUANRONG_WORKER_HOST").unwrap_or_else(|_| "127.0.0.1".into()),
        port: std::env::var("YUANRONG_WORKER_PORT")
            .ok()
            .and_then(|value| value.parse().ok())
            .unwrap_or(31501),
        connect_timeout_ms: 5_000,
        request_timeout_ms: 5_000,
        sdk_concurrency: 4,
    }
}

#[tokio::test]
async fn native_yuanrong_provider_round_trips_kv_and_batch_operations() {
    if !integration_enabled() {
        return;
    }

    let provider = YuanrongProvider::connect(config()).await.unwrap();
    provider.health_check().await.unwrap();
    let prefix = format!("openviking_native_{}", std::process::id());
    let first = format!("{prefix}_first");
    let second = format!("{prefix}_second");

    assert_eq!(provider.get(&first).await.unwrap(), None);
    provider
        .put(&first, Bytes::from_static(b"first-value"))
        .await
        .unwrap();
    assert_eq!(
        provider.get(&first).await.unwrap(),
        Some(Bytes::from_static(b"first-value"))
    );
    let empty = format!("{prefix}_empty");
    provider.put(&empty, Bytes::new()).await.unwrap();
    assert_eq!(provider.get(&empty).await.unwrap(), Some(Bytes::new()));

    provider
        .batch_put(vec![
            (first.clone(), Bytes::from_static(b"updated")),
            (second.clone(), Bytes::from_static(b"second-value")),
        ])
        .await
        .unwrap();
    assert_eq!(
        provider
            .batch_get(&[second.clone(), format!("{prefix}_missing"), first.clone()])
            .await
            .unwrap(),
        vec![
            Some(Bytes::from_static(b"second-value")),
            None,
            Some(Bytes::from_static(b"updated")),
        ]
    );

    provider
        .invalidate(&[first.clone(), second.clone(), empty.clone()])
        .await
        .unwrap();
    assert_eq!(provider.get(&first).await.unwrap(), None);
    assert_eq!(provider.get(&second).await.unwrap(), None);
    assert_eq!(provider.get(&empty).await.unwrap(), None);
    provider.close().await.unwrap();
}
