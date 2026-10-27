//! Regression coverage for batched Redis lease refresh.

use super::*;
use crate::cache_runtime::{CacheResult, MemoryMockProvider, ScriptResult};
use crate::lock::manager::{PathLockConfig, PathLockManager};
use crate::plugins::memfs::MemFileSystem;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Mutex;
use std::time::Duration;

fn mock_provider<F>(handler: F) -> (Arc<CacheRuntime>, RedisPathLockProvider)
where
    F: Fn(ScriptRequest) -> CacheResult<ScriptValue> + Send + Sync + 'static,
{
    let mock = MemoryMockProvider::new()
        .with_script_handler(move |request| ScriptResult::encode(&handler(request)?));
    let runtime = CacheRuntime::memory_with_provider(Arc::new(mock));
    let provider = RedisPathLockProvider::new(runtime.clone(), "batch-refresh-test", 30.0).unwrap();
    (runtime, provider)
}

fn status(value: &str) -> ScriptValue {
    ScriptValue::Array(vec![ScriptValue::Bytes(value.as_bytes().to_vec())])
}

#[tokio::test]
async fn same_scope_refresh_uses_one_script_for_twenty_paths() {
    let calls = Arc::new(AtomicUsize::new(0));
    let observed = calls.clone();
    let (runtime, provider) = mock_provider(move |request| {
        assert_eq!(request.script_id, REFRESH_BATCH_ID);
        assert_eq!(request.keys.len(), 1);
        assert_eq!(request.args[0], Bytes::from("owner"));
        assert_eq!(request.args[1], Bytes::from("200"));
        assert_eq!(request.args[2], Bytes::from("60000"));
        assert_eq!(request.args.len(), 23);
        observed.fetch_add(1, Ordering::SeqCst);
        Ok(ScriptValue::Array(vec![status("ok"); 20]))
    });
    assert!(provider.refresh_tokens(&[], "owner", 200).await.is_empty());
    assert_eq!(calls.load(Ordering::SeqCst), 0);
    let paths: Vec<_> = (0..20).map(|i| format!("/local/account-a/{i}")).collect();
    let results = provider.refresh_tokens(&paths, "owner", 200).await;
    assert_eq!(results.len(), 20);
    assert!(results.iter().all(|result| matches!(result, Ok(true))));
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    runtime.close().await.unwrap();
}

#[tokio::test]
async fn refresh_groups_scopes_and_preserves_mixed_result_order() {
    let calls = Arc::new(AtomicUsize::new(0));
    let observed = calls.clone();
    let (runtime, provider) = mock_provider(move |request| {
        assert_eq!(request.script_id, REFRESH_BATCH_ID);
        observed.fetch_add(1, Ordering::SeqCst);
        let results = request.args[3..]
            .iter()
            .map(|path| {
                let path = std::str::from_utf8(path).unwrap();
                if path.ends_with("/missing") {
                    status("missing")
                } else if path.ends_with("/foreign") {
                    status("owner_mismatch")
                } else if path.ends_with("/bad") {
                    ScriptValue::Array(vec![
                        ScriptValue::Bytes(b"invalid_token".to_vec()),
                        ScriptValue::Bytes(b"broken".to_vec()),
                    ])
                } else {
                    status("ok")
                }
            })
            .collect();
        Ok(ScriptValue::Array(results))
    });
    let paths = [
        "/local/account-b/good",
        "/local/account-a/missing",
        "/local/account-b/bad",
        "/local/account-a/foreign",
        "/local/account-a/good",
        "/unsupported",
    ]
    .map(String::from);
    let results = provider.refresh_tokens(&paths, "owner", 200).await;
    assert_eq!(results.len(), paths.len());
    assert!(matches!(results[0], Ok(true)));
    assert!(matches!(results[1], Ok(false)));
    assert!(
        matches!(&results[2], Err(PathLockError::InvalidToken(message)) if message.contains(&paths[2]))
    );
    assert!(matches!(results[3], Ok(false)));
    assert!(matches!(results[4], Ok(true)));
    assert!(matches!(results[5], Err(PathLockError::InvalidRequest(_))));
    assert_eq!(calls.load(Ordering::SeqCst), 2);
    runtime.close().await.unwrap();
}

#[tokio::test]
async fn failed_scope_does_not_prevent_other_scopes_refreshing() {
    let (runtime, provider) = mock_provider(|request| {
        if request.keys[0].contains("account-a") {
            return Err(CacheError::Unavailable(
                "injected refresh failure".to_string(),
            ));
        }
        Ok(ScriptValue::Array(vec![
            status("ok");
            request.args.len() - 3
        ]))
    });
    let paths = [
        "/local/account-a/a",
        "/local/account-b/b",
        "/local/account-a/c",
    ]
    .map(String::from);
    let results = provider.refresh_tokens(&paths, "owner", 200).await;
    assert!(matches!(results[0], Err(PathLockError::Io(_))));
    assert!(matches!(results[1], Ok(true)));
    assert!(matches!(results[2], Err(PathLockError::Io(_))));
    runtime.close().await.unwrap();
}

#[tokio::test]
async fn large_refresh_is_split_into_bounded_scripts() {
    let sizes = Arc::new(Mutex::new(Vec::new()));
    let observed = sizes.clone();
    let (runtime, provider) = mock_provider(move |request| {
        let size = request.args.len() - 3;
        observed.lock().unwrap().push(size);
        Ok(ScriptValue::Array(vec![status("ok"); size]))
    });
    let paths: Vec<_> = (0..REFRESH_BATCH_SIZE + 1)
        .map(|i| format!("/local/account-a/{i}"))
        .collect();
    let results = provider.refresh_tokens(&paths, "owner", 200).await;
    assert_eq!(results.len(), paths.len());
    assert!(results.iter().all(|result| matches!(result, Ok(true))));
    assert_eq!(*sizes.lock().unwrap(), vec![REFRESH_BATCH_SIZE, 1]);
    runtime.close().await.unwrap();
}

#[tokio::test]
async fn malformed_batch_response_never_reports_refresh_success() {
    let (runtime, provider) = mock_provider(|_| Ok(ScriptValue::Array(vec![status("ok")])));
    let paths = ["/local/account-a/a", "/local/account-a/b"].map(String::from);
    let results = provider.refresh_tokens(&paths, "owner", 200).await;
    assert_eq!(results.len(), 2);
    assert!(results
        .iter()
        .all(|result| matches!(result, Err(PathLockError::Internal(_)))));
    runtime.close().await.unwrap();
}

#[tokio::test]
async fn batch_invalid_request_is_preserved_for_every_path() {
    // Inject Lua's early-return shape; every u128 supplied by Rust is a valid time_ns.
    let (runtime, provider) = mock_provider(|request| {
        assert_eq!(request.script_id, REFRESH_BATCH_ID);
        Ok(ScriptValue::Array(vec![
            ScriptValue::Bytes(b"invalid_request".to_vec()),
            ScriptValue::Bytes(b"invalid time_ns".to_vec()),
        ]))
    });
    for size in [2, 1, 3, 20, REFRESH_BATCH_SIZE + 1] {
        let paths: Vec<_> = (0..size).map(|i| format!("/local/account-a/{i}")).collect();
        let results = provider.refresh_tokens(&paths, "owner", u128::MAX).await;
        assert_eq!(results.len(), size);
        assert!(
            results.iter().all(|result| matches!(
                result,
                Err(PathLockError::InvalidRequest(message)) if message == "invalid time_ns"
            )),
            "batch size {size}: {results:?}"
        );
    }
    runtime.close().await.unwrap();
}

#[tokio::test]
async fn manager_uses_batch_for_manual_and_background_refresh() {
    let calls = Arc::new(AtomicUsize::new(0));
    let observed = calls.clone();
    let (runtime, provider) = mock_provider(move |request| match request.script_id.as_str() {
        ACQUIRE_BATCH_ID => {
            let owner = std::str::from_utf8(&request.args[0]).unwrap();
            let time = std::str::from_utf8(&request.args[1]).unwrap();
            let mut results = vec![ScriptValue::Bytes(b"ok".to_vec())];
            for pair in request.args[4..].chunks_exact(2) {
                let kind = std::str::from_utf8(&pair[1]).unwrap();
                results.extend([
                    ScriptValue::Bytes(pair[0].to_vec()),
                    ScriptValue::Bytes(b"created".to_vec()),
                    ScriptValue::Bytes(Vec::new()),
                    ScriptValue::Bytes(format!("{owner}:{time}:{kind}").into_bytes()),
                ]);
            }
            Ok(ScriptValue::Array(results))
        }
        REFRESH_BATCH_ID => {
            assert_eq!(request.args.len(), 23);
            observed.fetch_add(1, Ordering::SeqCst);
            Ok(ScriptValue::Array(vec![status("ok"); 20]))
        }
        other => panic!("manager should use batch refresh, got {other}"),
    });
    let manager = PathLockManager::new(
        Arc::new(MemFileSystem::new()),
        Arc::new(provider),
        PathLockConfig {
            lock_expire_secs: 1.0,
            ..PathLockConfig::default()
        },
    );
    let paths: Vec<_> = (0..20).map(|i| format!("/local/account-a/{i}")).collect();
    let lease = manager
        .acquire_exact_batch(&paths, Duration::ZERO, None)
        .await
        .unwrap();
    assert_eq!(manager.refresh(&lease).await.unwrap(), "refreshed");
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    tokio::time::timeout(Duration::from_secs(3), async {
        while calls.load(Ordering::SeqCst) < 2 {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("background refresh must use the batch provider");
    runtime.close().await.unwrap();
}

#[tokio::test]
async fn redis_batch_preserves_kind_ownership_errors_and_hash_ttl() {
    let Ok(endpoint) = std::env::var("REDIS_URL") else {
        return;
    };
    let runtime = CacheRuntime::redis(crate::cache_runtime::RedisProviderConfig {
        endpoints: vec![endpoint],
        command_timeout_ms: 1_000,
        ..crate::cache_runtime::RedisProviderConfig::default()
    })
    .await
    .unwrap();
    let service = format!("batch-refresh-real-{}", uuid::Uuid::new_v4());
    let provider = RedisPathLockProvider::new(runtime.clone(), &service, 1.0).unwrap();
    let paths = [
        "/local/account-a/bad",
        "/local/account-a/exact",
        "/local/account-a/missing",
        "/local/account-a/foreign",
        "/local/account-b/tree",
        "/local/account-a/exact",
        "/local/account-c/foreign",
    ]
    .map(String::from);
    for (index, owner, kind) in [
        (1, "owner", PathLockKind::Exact),
        (3, "other", PathLockKind::Exact),
        (4, "owner", PathLockKind::Tree),
        (6, "other", PathLockKind::Tree),
    ] {
        provider
            .try_create_token(
                &paths[index],
                &LockToken {
                    owner_id: owner.to_string(),
                    time_ns: 100,
                    lock_type: kind,
                },
            )
            .await
            .unwrap();
    }
    runtime
        .register_script(ScriptDefinition {
            id: "pathlock.test.batch_corrupt.v1",
            redis_lua: "return redis.call('HSET', KEYS[1], ARGV[1], 'broken')",
        })
        .unwrap();
    runtime
        .execute_script(ScriptRequest {
            script_id: "pathlock.test.batch_corrupt.v1".to_string(),
            keys: vec![provider.tokens_key_for_path(&paths[0]).unwrap()],
            args: vec![Bytes::from(paths[0].clone())],
        })
        .await
        .unwrap();
    runtime
        .register_script(ScriptDefinition {
            id: "pathlock.test.batch_long_ttl.v1",
            redis_lua: "return redis.call('PEXPIRE', KEYS[1], 60000)",
        })
        .unwrap();
    for path in [&paths[1], &paths[4], &paths[6]] {
        runtime
            .execute_script(ScriptRequest {
                script_id: "pathlock.test.batch_long_ttl.v1".to_string(),
                keys: vec![provider.tokens_key_for_path(path).unwrap()],
                args: vec![],
            })
            .await
            .unwrap();
    }
    let results = provider.refresh_tokens(&paths, "owner", u128::MAX).await;
    assert_eq!(results.len(), paths.len());
    assert!(matches!(results[0], Err(PathLockError::InvalidToken(_))));
    assert!(matches!(results[1], Ok(true)));
    assert!(matches!(results[2], Ok(false)));
    assert!(matches!(results[3], Ok(false)));
    assert!(matches!(results[4], Ok(true)));
    assert!(matches!(results[5], Ok(true)));
    assert!(matches!(results[6], Ok(false)));
    for (index, kind) in [(1, PathLockKind::Exact), (4, PathLockKind::Tree)] {
        let token = provider.read_token(&paths[index]).await.unwrap().unwrap();
        assert_eq!(token.owner_id, "owner");
        assert_eq!(token.time_ns, u128::MAX);
        assert_eq!(token.lock_type, kind);
    }
    let foreign = provider.read_token(&paths[3]).await.unwrap().unwrap();
    assert_eq!(foreign.owner_id, "other");
    assert_eq!(foreign.time_ns, 100);
    runtime
        .register_script(ScriptDefinition {
            id: "pathlock.test.batch_ttl.v1",
            redis_lua: "return redis.call('PTTL', KEYS[1])",
        })
        .unwrap();
    for (path, refreshed) in [(&paths[1], true), (&paths[4], true), (&paths[6], false)] {
        let ttl = runtime
            .execute_script(ScriptRequest {
                script_id: "pathlock.test.batch_ttl.v1".to_string(),
                keys: vec![provider.tokens_key_for_path(path).unwrap()],
                args: vec![],
            })
            .await
            .unwrap()
            .decode()
            .unwrap();
        if refreshed {
            assert!(matches!(ttl, ScriptValue::Integer(value) if value > 0 && value <= 2_000));
        } else {
            assert!(matches!(ttl, ScriptValue::Integer(value) if value > 2_000));
        }
    }
    runtime.close().await.unwrap();
}
