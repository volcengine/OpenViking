use bytes::Bytes;
use ragfs::cache_runtime::{
    CacheError, CacheRuntime, Expiration, RedisProviderConfig, SetCondition, SetOptions, SetResult,
};
use std::time::Duration;

fn config() -> Option<RedisProviderConfig> {
    let endpoint = std::env::var("REDIS_URL").ok()?;
    Some(RedisProviderConfig {
        endpoints: vec![endpoint],
        connect_timeout_ms: 30_000,
        command_timeout_ms: 1_000,
        default_ttl_seconds: 60,
        ..RedisProviderConfig::default()
    })
}

fn key(test_name: &str, value: &str) -> String {
    format!(
        "ragfs-runtime-test:{}:{test_name}:{value}",
        std::process::id()
    )
}

fn topology_endpoints(variable: &str) -> Option<Vec<String>> {
    let endpoints = std::env::var(variable).ok()?;
    let endpoints = endpoints
        .split(',')
        .map(str::trim)
        .filter(|endpoint| !endpoint.is_empty())
        .map(str::to_string)
        .collect::<Vec<_>>();
    (!endpoints.is_empty()).then_some(endpoints)
}

#[tokio::test]
async fn redis_runtime_preserves_primitive_and_batch_semantics() {
    let Some(config) = config() else {
        return;
    };
    let runtime = CacheRuntime::redis(config).await.unwrap();
    let missing = key("contract", "missing");
    let one = key("contract", "one");
    let two = key("contract", "two");

    assert_eq!(runtime.get(&missing).await.unwrap(), None);
    runtime
        .mset(vec![
            (one.clone(), Bytes::from_static(b"1")),
            (two.clone(), Bytes::from_static(b"2")),
        ])
        .await
        .unwrap();
    assert_eq!(
        runtime
            .mget(&[two.clone(), missing, one.clone()])
            .await
            .unwrap(),
        vec![
            Some(Bytes::from_static(b"2")),
            None,
            Some(Bytes::from_static(b"1")),
        ]
    );
    assert_eq!(runtime.del(&[one.clone(), two]).await.unwrap(), 2);
    assert_eq!(runtime.get(&one).await.unwrap(), None);
    runtime.close().await.unwrap();
}

#[tokio::test]
async fn redis_runtime_preserves_default_ttl_and_per_write_override() {
    let Some(mut config) = config() else {
        return;
    };
    config.default_ttl_seconds = 30;
    let runtime = CacheRuntime::redis(config).await.unwrap();
    let ttl_key = key("ttl", "ttl");

    runtime
        .set(
            &ttl_key,
            Bytes::from_static(b"short"),
            SetOptions {
                expiration: Some(Expiration::After(Duration::from_millis(100))),
                ..SetOptions::default()
            },
        )
        .await
        .unwrap();
    tokio::time::sleep(Duration::from_millis(200)).await;
    assert_eq!(runtime.get(&ttl_key).await.unwrap(), None);
    runtime.close().await.unwrap();
}

#[tokio::test]
async fn redis_runtime_supports_conditions_counters_and_lists() {
    let Some(config) = config() else {
        return;
    };
    let runtime = CacheRuntime::redis(config).await.unwrap();
    let string_key = key("commands", "string");
    let counter_key = key("commands", "counter");
    let list_key = key("commands", "list");

    assert_eq!(
        runtime
            .set(
                &string_key,
                Bytes::from_static(b"first"),
                SetOptions {
                    condition: SetCondition::Nx,
                    ..SetOptions::default()
                },
            )
            .await
            .unwrap(),
        SetResult::Applied
    );
    assert_eq!(
        runtime
            .set(
                &string_key,
                Bytes::from_static(b"ignored"),
                SetOptions {
                    condition: SetCondition::Nx,
                    ..SetOptions::default()
                },
            )
            .await
            .unwrap(),
        SetResult::ConditionNotMet
    );

    assert_eq!(runtime.incr_by(&counter_key, 4).await.unwrap(), 4);
    assert_eq!(runtime.decr(&counter_key).await.unwrap(), 3);

    assert_eq!(
        runtime
            .rpush(
                &list_key,
                vec![Bytes::from_static(b"a"), Bytes::from_static(b"b")],
            )
            .await
            .unwrap(),
        2
    );
    assert_eq!(
        runtime.lpop(&list_key, Some(2)).await.unwrap(),
        vec![Bytes::from_static(b"a"), Bytes::from_static(b"b")]
    );

    runtime
        .del(&[string_key, counter_key, list_key])
        .await
        .unwrap();
    runtime.close().await.unwrap();
}

#[test]
fn sync_redis_runtime_is_initialized_on_the_runtime_executor() {
    let Some(config) = config() else {
        return;
    };
    let runtime = CacheRuntime::connect_sync(config).unwrap();
    let sync = runtime.sync_facade();
    let sync_key = key("sync", "value");

    sync.ping().unwrap();
    assert_eq!(
        sync.set(
            &sync_key,
            Bytes::from_static(b"sync"),
            SetOptions::default(),
        )
        .unwrap(),
        SetResult::Applied
    );
    assert_eq!(
        sync.get(&sync_key).unwrap(),
        Some(Bytes::from_static(b"sync"))
    );
    sync.del(&[sync_key]).unwrap();
    sync.close().unwrap();
}

#[tokio::test]
async fn redis_cluster_supports_same_slot_reads_and_cross_slot_errors() {
    let Some(endpoints) = topology_endpoints("REDIS_CLUSTER_TEST_URLS") else {
        return;
    };
    let runtime = CacheRuntime::redis(RedisProviderConfig {
        mode: "cluster".into(),
        endpoints,
        connect_timeout_ms: 10_000,
        command_timeout_ms: 3_000,
        default_ttl_seconds: 60,
        ..RedisProviderConfig::default()
    })
    .await
    .unwrap();
    let same_slot_one = key("cluster", "{runtime}:one");
    let same_slot_two = key("cluster", "{runtime}:two");

    runtime
        .mset(vec![
            (same_slot_one.clone(), Bytes::from_static(b"one")),
            (same_slot_two.clone(), Bytes::from_static(b"two")),
        ])
        .await
        .unwrap();

    assert_eq!(
        runtime
            .mget(&[same_slot_one.clone(), same_slot_two.clone()])
            .await
            .unwrap(),
        vec![
            Some(Bytes::from_static(b"one")),
            Some(Bytes::from_static(b"two")),
        ]
    );

    let cross_slot = runtime
        .mset(vec![
            (key("cluster", "{slot-a}:one"), Bytes::from_static(b"one")),
            (key("cluster", "{slot-b}:two"), Bytes::from_static(b"two")),
        ])
        .await;
    assert!(matches!(cross_slot, Err(CacheError::CrossSlot(_))));

    runtime.del(&[same_slot_one, same_slot_two]).await.unwrap();
    runtime.close().await.unwrap();
}

#[tokio::test]
async fn redis_sentinel_discovers_the_configured_master() {
    let Some(endpoints) = topology_endpoints("REDIS_SENTINEL_TEST_URLS") else {
        return;
    };
    let Ok(master_name) = std::env::var("REDIS_SENTINEL_TEST_MASTER") else {
        return;
    };
    let runtime = CacheRuntime::redis(RedisProviderConfig {
        mode: "sentinel".into(),
        endpoints,
        master_name: Some(master_name),
        connect_timeout_ms: 10_000,
        command_timeout_ms: 3_000,
        default_ttl_seconds: 60,
        ..RedisProviderConfig::default()
    })
    .await
    .unwrap();
    let sentinel_key = key("sentinel", "value");

    runtime
        .set(
            &sentinel_key,
            Bytes::from_static(b"sentinel"),
            SetOptions::default(),
        )
        .await
        .unwrap();
    assert_eq!(
        runtime.get(&sentinel_key).await.unwrap(),
        Some(Bytes::from_static(b"sentinel"))
    );

    runtime.del(&[sentinel_key]).await.unwrap();
    runtime.close().await.unwrap();
}

#[tokio::test]
async fn redis_runtime_accepts_the_legacy_plaintext_password() {
    let Ok(endpoint) = std::env::var("REDIS_PASSWORD_TEST_URL") else {
        return;
    };
    let Ok(password) = std::env::var("REDIS_PASSWORD_TEST_SECRET") else {
        return;
    };
    let runtime = CacheRuntime::redis(RedisProviderConfig {
        endpoints: vec![endpoint],
        password,
        connect_timeout_ms: 10_000,
        command_timeout_ms: 3_000,
        ..RedisProviderConfig::default()
    })
    .await
    .unwrap();
    let password_key = key("password", "value");

    runtime
        .set(
            &password_key,
            Bytes::from_static(b"authenticated"),
            SetOptions::default(),
        )
        .await
        .unwrap();
    assert_eq!(
        runtime.get(&password_key).await.unwrap(),
        Some(Bytes::from_static(b"authenticated"))
    );

    runtime.del(&[password_key]).await.unwrap();
    runtime.close().await.unwrap();
}
