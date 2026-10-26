use bytes::Bytes;
use ragfs::cache_runtime::{
    CacheError, CacheRuntime, Expiration, ListDirection, ListInsertPosition, ListInsertRequest,
    ListMoveRequest, MemoryMockProvider, ScriptRequest, SetCondition, SetOptions, SetResult,
};
use std::sync::Arc;
use std::time::Duration;

#[tokio::test]
async fn async_runtime_supports_the_primitive_contract() {
    let runtime = CacheRuntime::memory();

    assert_eq!(runtime.get("missing").await.unwrap(), None);

    assert_eq!(
        runtime
            .set("a", Bytes::from_static(b"one"), SetOptions::default())
            .await
            .unwrap(),
        SetResult::Applied
    );
    assert_eq!(
        runtime.get("a").await.unwrap(),
        Some(Bytes::from_static(b"one"))
    );

    runtime
        .mset(vec![
            ("b".to_string(), Bytes::from_static(b"two")),
            ("c".to_string(), Bytes::from_static(b"three")),
        ])
        .await
        .unwrap();
    assert_eq!(
        runtime
            .mget(&["c".to_string(), "missing".to_string(), "b".to_string()])
            .await
            .unwrap(),
        vec![
            Some(Bytes::from_static(b"three")),
            None,
            Some(Bytes::from_static(b"two")),
        ]
    );

    assert_eq!(
        runtime
            .del(&["a".to_string(), "c".to_string(), "missing".to_string()])
            .await
            .unwrap(),
        2
    );
    assert_eq!(runtime.del(&["b".to_string()]).await.unwrap(), 1);
    assert_eq!(
        runtime
            .mget(&["a".to_string(), "b".to_string(), "c".to_string()])
            .await
            .unwrap(),
        vec![None, None, None]
    );
}

#[tokio::test]
async fn set_reports_nx_and_xx_outcomes() {
    let runtime = CacheRuntime::memory();
    let nx = SetOptions {
        condition: SetCondition::Nx,
        ..SetOptions::default()
    };
    let xx = SetOptions {
        condition: SetCondition::Xx,
        ..SetOptions::default()
    };

    assert_eq!(
        runtime
            .set("lock", Bytes::from_static(b"owner-1"), nx)
            .await
            .unwrap(),
        SetResult::Applied
    );
    assert_eq!(
        runtime
            .set("lock", Bytes::from_static(b"owner-2"), nx)
            .await
            .unwrap(),
        SetResult::ConditionNotMet
    );
    assert_eq!(
        runtime
            .set("missing", Bytes::from_static(b"owner"), xx)
            .await
            .unwrap(),
        SetResult::ConditionNotMet
    );
    assert_eq!(
        runtime
            .set("lock", Bytes::from_static(b"owner-2"), xx)
            .await
            .unwrap(),
        SetResult::Applied
    );
}

#[tokio::test]
async fn set_expiration_and_keep_ttl_match_redis_semantics() {
    let runtime = CacheRuntime::memory();
    runtime
        .set(
            "ttl",
            Bytes::from_static(b"first"),
            SetOptions {
                expiration: Some(Expiration::After(Duration::from_millis(40))),
                ..SetOptions::default()
            },
        )
        .await
        .unwrap();
    tokio::time::sleep(Duration::from_millis(20)).await;
    runtime
        .set(
            "ttl",
            Bytes::from_static(b"second"),
            SetOptions {
                keep_ttl: true,
                ..SetOptions::default()
            },
        )
        .await
        .unwrap();
    tokio::time::sleep(Duration::from_millis(30)).await;

    assert_eq!(runtime.get("ttl").await.unwrap(), None);
}

#[tokio::test]
async fn atomic_integer_commands_follow_redis_string_semantics() {
    let runtime = CacheRuntime::memory();

    assert_eq!(runtime.incr("sequence").await.unwrap(), 1);
    assert_eq!(runtime.incr_by("sequence", 4).await.unwrap(), 5);
    assert_eq!(runtime.decr("sequence").await.unwrap(), 4);
    assert_eq!(runtime.decr_by("sequence", 6).await.unwrap(), -2);

    runtime
        .set(
            "not-an-integer",
            Bytes::from_static(b"value"),
            SetOptions::default(),
        )
        .await
        .unwrap();
    assert!(matches!(
        runtime.incr("not-an-integer").await,
        Err(CacheError::InvalidData(_))
    ));
}

#[tokio::test]
async fn set_queries_return_membership_members_and_cardinality() {
    let provider = Arc::new(MemoryMockProvider::new());
    provider
        .insert_set_members(
            "queues",
            vec![Bytes::from_static(b"beta"), Bytes::from_static(b"alpha")],
        )
        .await;
    let runtime = CacheRuntime::memory_with_provider(provider);

    assert!(runtime.sismember("queues", b"alpha").await.unwrap());
    assert!(!runtime.sismember("queues", b"missing").await.unwrap());
    assert_eq!(runtime.scard("queues").await.unwrap(), 2);
    assert_eq!(runtime.scard("missing").await.unwrap(), 0);

    let mut members = runtime.smembers("queues").await.unwrap();
    members.sort();
    assert_eq!(
        members,
        vec![Bytes::from_static(b"alpha"), Bytes::from_static(b"beta")]
    );
}

#[tokio::test]
async fn non_blocking_list_commands_preserve_redis_order() {
    let runtime = CacheRuntime::memory();

    assert_eq!(
        runtime
            .rpush(
                "pending",
                vec![Bytes::from_static(b"a"), Bytes::from_static(b"b")],
            )
            .await
            .unwrap(),
        2
    );
    assert_eq!(
        runtime
            .lpush("pending", vec![Bytes::from_static(b"zero")])
            .await
            .unwrap(),
        3
    );
    assert_eq!(
        runtime.lrange("pending", 0, -1).await.unwrap(),
        vec![
            Bytes::from_static(b"zero"),
            Bytes::from_static(b"a"),
            Bytes::from_static(b"b"),
        ]
    );
    assert_eq!(runtime.llen("pending").await.unwrap(), 3);
    assert_eq!(
        runtime.lindex("pending", -1).await.unwrap(),
        Some(Bytes::from_static(b"b"))
    );
    assert_eq!(
        runtime.lpop("pending", None).await.unwrap(),
        vec![Bytes::from_static(b"zero")]
    );
    assert_eq!(
        runtime.rpop("pending", Some(1)).await.unwrap(),
        vec![Bytes::from_static(b"b")]
    );
    assert_eq!(runtime.llen("pending").await.unwrap(), 1);
}

#[tokio::test]
async fn non_blocking_list_mutations_match_redis_semantics() {
    let runtime = CacheRuntime::memory();
    runtime
        .rpush(
            "source",
            vec![
                Bytes::from_static(b"a"),
                Bytes::from_static(b"b"),
                Bytes::from_static(b"a"),
                Bytes::from_static(b"c"),
            ],
        )
        .await
        .unwrap();

    runtime
        .lset("source", 1, Bytes::from_static(b"B"))
        .await
        .unwrap();
    assert_eq!(
        runtime
            .linsert(ListInsertRequest {
                key: "source".to_string(),
                position: ListInsertPosition::Before,
                pivot: Bytes::from_static(b"B"),
                value: Bytes::from_static(b"x"),
            })
            .await
            .unwrap(),
        5
    );
    assert_eq!(
        runtime
            .lrem("source", 0, Bytes::from_static(b"a"))
            .await
            .unwrap(),
        2
    );
    runtime.ltrim("source", 0, 1).await.unwrap();
    runtime
        .rpush("destination", vec![Bytes::from_static(b"d")])
        .await
        .unwrap();
    assert_eq!(
        runtime
            .lmove(ListMoveRequest {
                source: "source".to_string(),
                destination: "destination".to_string(),
                source_direction: ListDirection::Right,
                destination_direction: ListDirection::Left,
            })
            .await
            .unwrap(),
        Some(Bytes::from_static(b"B"))
    );
    assert_eq!(
        runtime.lrange("source", 0, -1).await.unwrap(),
        vec![Bytes::from_static(b"x")]
    );
    assert_eq!(
        runtime.lrange("destination", 0, -1).await.unwrap(),
        vec![Bytes::from_static(b"B"), Bytes::from_static(b"d")]
    );
}

#[tokio::test]
async fn unknown_script_returns_an_explicit_error() {
    let runtime = CacheRuntime::memory();
    let error = runtime
        .execute_script(ScriptRequest {
            script_id: "queuefs.unknown.v1".to_string(),
            keys: Vec::new(),
            args: Vec::new(),
        })
        .await
        .unwrap_err();

    assert!(matches!(error, CacheError::UnsupportedScript(_)));
}

#[test]
fn sync_and_async_facades_share_one_provider_instance() {
    let runtime = CacheRuntime::memory();
    let sync = runtime.sync_facade();

    assert_eq!(
        sync.set(
            "shared",
            Bytes::from_static(b"value"),
            SetOptions::default(),
        )
        .unwrap(),
        SetResult::Applied
    );

    let async_runtime = runtime.clone();
    let value = std::thread::spawn(move || {
        tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap()
            .block_on(async move { async_runtime.get("shared").await.unwrap() })
    })
    .join()
    .unwrap();

    assert_eq!(value, Some(Bytes::from_static(b"value")));
}

#[test]
fn sync_facade_exposes_lifecycle_operations() {
    let runtime = CacheRuntime::memory();
    let sync = runtime.sync_facade();

    sync.ping().unwrap();
    sync.close().unwrap();
    assert!(matches!(sync.get("closed"), Err(CacheError::Closed)));
}

#[tokio::test]
async fn sync_facade_rejects_calls_from_tokio_runtime() {
    let runtime = CacheRuntime::memory();
    let sync = runtime.sync_facade();

    assert!(matches!(
        sync.incr("counter"),
        Err(CacheError::InvalidExecutionContext)
    ));
    assert_eq!(runtime.get("counter").await.unwrap(), None);
}

#[tokio::test]
async fn close_rejects_new_operations() {
    let runtime = CacheRuntime::memory();
    runtime.close().await.unwrap();

    assert!(matches!(runtime.get("key").await, Err(CacheError::Closed)));
}

#[tokio::test]
async fn controlled_memory_provider_is_only_accessed_through_runtime() {
    let provider = Arc::new(MemoryMockProvider::new());
    let runtime = CacheRuntime::memory_with_provider(Arc::clone(&provider));

    runtime
        .set(
            "observed",
            Bytes::from_static(b"value"),
            SetOptions::default(),
        )
        .await
        .unwrap();
    assert_eq!(provider.keys().await, vec!["observed".to_string()]);

    provider.set_unavailable(true);
    assert!(matches!(
        runtime.get("observed").await,
        Err(CacheError::Unavailable(_))
    ));
}
