#![cfg(feature = "mooncake-native")]

use bytes::Bytes;
use ragfs::cache::CacheProvider;
use ragfs_cache_mooncake::{MooncakeConfig, MooncakeProvider};

fn required_env(name: &str) -> String {
    std::env::var(name).unwrap_or_else(|_| panic!("{name} must be set for native smoke test"))
}

#[tokio::test]
async fn native_mooncake_round_trips_complete_objects_over_tcp() {
    if std::env::var("OPENVIKING_RUN_MOONCAKE_INTEGRATION").as_deref() != Ok("true") {
        return;
    }

    let config = MooncakeConfig {
        local_hostname: required_env("MOONCAKE_LOCAL_HOSTNAME"),
        metadata_server: required_env("MOONCAKE_METADATA_SERVER"),
        master_server_addr: required_env("MOONCAKE_MASTER_SERVER_ADDR"),
        protocol: std::env::var("MOONCAKE_PROTOCOL").unwrap_or_else(|_| "tcp".into()),
        device_name: std::env::var("MOONCAKE_DEVICE_NAME").unwrap_or_default(),
        global_segment_size: 512 << 20,
        local_buffer_size: 128 << 20,
        replica_num: 1,
        sdk_concurrency: 4,
        operation_timeout_ms: 5_000,
    };
    let provider = MooncakeProvider::connect(config).await.unwrap();
    let key = format!("openviking:smoke:{}", std::process::id());

    provider
        .put(&key, Bytes::from_static(b"mooncake-object"))
        .await
        .unwrap();
    assert_eq!(
        provider.get(&key).await.unwrap(),
        Some(Bytes::from_static(b"mooncake-object"))
    );
    provider.delete(&key).await.unwrap();
    assert_eq!(provider.get(&key).await.unwrap(), None);
    provider.close().await.unwrap();
}
