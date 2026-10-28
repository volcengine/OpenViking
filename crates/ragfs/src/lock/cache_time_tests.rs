//! Redis server-clock, legacy-token and fixed-TTL regression coverage.

use super::*;
use crate::cache_runtime::RedisProviderConfig;

const TIME_ID: &str = "pathlock.test.server_time.v1";
const TTL_ID: &str = "pathlock.test.clock_ttl.v1";
const SEED_ID: &str = "pathlock.test.legacy_time.v1";
const MATH_ID: &str = "pathlock.test.decimal_time.v1";
const MATH_SCRIPT: &str = token_script!(
    r#"
local difference = decimal_subtract(ARGV[1], ARGV[2])
local lease_ns, ttl_ms = lease_values(ARGV[3])
return { difference or false, lease_ns or false, ttl_ms or false,
    decimal_less(ARGV[1], ARGV[2]) }
"#
);

async fn fixture() -> Option<(Arc<CacheRuntime>, RedisPathLockProvider)> {
    let endpoint = std::env::var("REDIS_URL").ok()?;
    let runtime = CacheRuntime::redis(RedisProviderConfig {
        endpoints: vec![endpoint],
        command_timeout_ms: 1_000,
        ..RedisProviderConfig::default()
    })
    .await
    .unwrap();
    for (id, redis_lua) in [
        (TIME_ID, "return redis.call('TIME')"),
        (TTL_ID, "return redis.call('PTTL', KEYS[1])"),
        (
            SEED_ID,
            "return redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])",
        ),
        (MATH_ID, MATH_SCRIPT),
    ] {
        runtime
            .register_script(ScriptDefinition { id, redis_lua })
            .unwrap();
    }
    let provider = RedisPathLockProvider::new(
        runtime.clone(),
        &format!("pathlock-time-{}", uuid::Uuid::new_v4()),
        30.0,
    )
    .unwrap();
    Some((runtime, provider))
}

fn request(path: &str, kind: PathLockKind) -> PathLockRequest {
    PathLockRequest {
        path: path.into(),
        kind,
    }
}

fn token(owner: &str, time_ns: u128, kind: PathLockKind) -> LockToken {
    LockToken {
        owner_id: owner.into(),
        time_ns,
        lock_type: kind,
    }
}

async fn now(provider: &RedisPathLockProvider) -> u128 {
    let time = provider
        .execute_array(TIME_ID, provider.tokens_key_for_path("/").unwrap(), vec![])
        .await
        .unwrap();
    let seconds: u128 = RedisPathLockProvider::result_string(&time[0], "TIME")
        .unwrap()
        .parse()
        .unwrap();
    let micros: u128 = RedisPathLockProvider::result_string(&time[1], "TIME")
        .unwrap()
        .parse()
        .unwrap();
    seconds * 1_000_000_000 + micros * 1_000
}

async fn seed(provider: &RedisPathLockProvider, path: &str, token: &LockToken) {
    provider
        .execute_integer(
            SEED_ID,
            provider.tokens_key_for_path(path).unwrap(),
            vec![
                RedisPathLockProvider::bytes(path),
                RedisPathLockProvider::bytes(LockTokenCodec::encode(token)),
            ],
        )
        .await
        .unwrap();
}

async fn ttl(provider: &RedisPathLockProvider, path: &str) -> i64 {
    provider
        .execute_integer(TTL_ID, provider.tokens_key_for_path(path).unwrap(), vec![])
        .await
        .unwrap()
}

fn assert_server_time(token: &LockToken, before: u128, after: u128) {
    assert!(
        (before..=after).contains(&token.time_ns),
        "token time {} outside Redis TIME [{before}, {after}]",
        token.time_ns
    );
    assert_eq!(token.time_ns % 1_000, 0);
}

// Bracket both mutation and PTTL using Redis TIME, accounting for TTL decay
// without requiring a scheduler-dependent sleep or identical PTTL values.
async fn assert_fixed_ttl(provider: &RedisPathLockProvider, path: &str, before: u128) {
    let remaining = ttl(provider, path).await;
    let after = now(provider).await;
    let target = 2 * provider.lease_ms as i64;
    let elapsed_ms = ((after - before).div_ceil(1_000_000)) as i64;
    assert!(
        remaining > 0 && remaining <= target && remaining >= target - elapsed_ms - 1,
        "TTL {remaining}, expected {target} minus at most {elapsed_ms}ms"
    );
}

#[test]
fn lease_rounding_and_lua_integer_bound_are_checked_before_access() {
    let mock = crate::cache_runtime::MemoryMockProvider::new()
        .with_script_handler(|_| panic!("configuration validation must not execute scripts"));
    let runtime = CacheRuntime::memory_with_provider(Arc::new(mock));
    let provider = RedisPathLockProvider::new(runtime.clone(), "rounding", 1.0001).unwrap();
    assert_eq!(provider.lease_ms, 1_001);
    for lease in [
        0.999,
        f64::NAN,
        f64::INFINITY,
        f64::MAX,
        (1_u64 << 52) as f64 / 1_000.0,
    ] {
        assert!(matches!(
            RedisPathLockProvider::new(runtime.clone(), "bounds", lease),
            Err(PathLockError::InvalidRequest(_))
        ));
    }
}

#[tokio::test]
async fn decimal_math_preserves_nanoseconds_and_expiry_boundary() {
    let Some((runtime, provider)) = fixture().await else {
        return;
    };
    for (left, right) in [
        (0, 1),
        (1_000_000_000, 1),
        (1_700_000_000_123_456_789, 30_000_000_000),
        (u128::MAX, 30_000_000_000),
        (u128::MAX, u128::MAX),
        (123, 123),
        (122, 123),
        (124, 123),
    ] {
        let result = provider
            .execute_array(
                MATH_ID,
                provider.tokens_key_for_path("/").unwrap(),
                [
                    left.to_string(),
                    right.to_string(),
                    MAX_LEASE_MS.to_string(),
                ]
                .iter()
                .map(RedisPathLockProvider::bytes)
                .collect(),
            )
            .await
            .unwrap();
        assert_eq!(
            RedisPathLockProvider::result_string(&result[0], "math").unwrap(),
            left.saturating_sub(right).to_string()
        );
        assert_eq!(
            RedisPathLockProvider::result_string(&result[1], "math").unwrap(),
            format!("{MAX_LEASE_MS}000000")
        );
        assert_eq!(
            RedisPathLockProvider::result_string(&result[2], "math").unwrap(),
            (2 * MAX_LEASE_MS).to_string()
        );
        // Lua false is encoded as null; equality remains live under strict <.
        assert_eq!(
            result[3],
            if left < right {
                ScriptValue::Integer(1)
            } else {
                ScriptValue::Null
            }
        );
    }
    for lease in [
        "0",
        "999",
        "1000.5",
        "nan",
        "4503599627370496",
        "18446744073709551616",
    ] {
        let result = provider
            .execute_array(
                MATH_ID,
                provider.tokens_key_for_path("/").unwrap(),
                ["10", "1", lease]
                    .iter()
                    .map(RedisPathLockProvider::bytes)
                    .collect(),
            )
            .await
            .unwrap();
        assert_eq!(result[1], ScriptValue::Null);
        assert_eq!(result[2], ScriptValue::Null);
    }
    runtime.close().await.unwrap();
}

#[tokio::test]
async fn client_clock_skew_cannot_expire_live_tree_or_change_failed_acquire_ttl() {
    let Some((runtime, provider)) = fixture().await else {
        return;
    };
    let root = "/local/account/root";
    let child = "/local/account/root/child";
    let before = now(&provider).await;
    let acquired = provider
        .try_acquire_batch_atomic(
            &[request(root, PathLockKind::Tree)],
            "a",
            u128::MAX,
            u128::MAX,
        )
        .await
        .unwrap()
        .unwrap();
    let original = provider.read_token(root).await.unwrap().unwrap();
    assert_server_time(&original, before, now(&provider).await);
    assert!(
        matches!(&acquired[0].change, AcquisitionChange::Created { replacement } if replacement == &original)
    );
    let remaining = ttl(&provider, root).await;
    for (client_now, client_threshold) in [
        (0, 0),
        (
            original.time_ns + 31_000_000_000,
            original.time_ns + 1_000_000_000,
        ),
        (original.time_ns - 31_000_000_000, 0),
        (u128::MAX, u128::MAX),
    ] {
        assert!(matches!(
            provider
                .try_acquire_batch_atomic(
                    &[request(child, PathLockKind::Exact)],
                    "b",
                    client_now,
                    client_threshold
                )
                .await,
            Err(PathLockError::Conflict { .. })
        ));
        assert_eq!(
            provider
                .is_path_locked_atomic(child, client_now, client_threshold, true)
                .await
                .unwrap(),
            Some(true)
        );
        assert_eq!(
            provider.read_token(root).await.unwrap(),
            Some(original.clone())
        );
        assert_eq!(provider.read_token(child).await.unwrap(), None);
    }
    assert!(ttl(&provider, root).await <= remaining);
    runtime.close().await.unwrap();
}

#[tokio::test]
async fn crud_cas_refresh_release_and_rollback_use_server_time_and_fixed_ttl() {
    let Some((runtime, provider)) = fixture().await else {
        return;
    };
    let path = "/local/account/a";
    let other = "/local/account/b";
    let before = now(&provider).await;
    provider
        .try_create_token(path, &token("a", u128::MAX, PathLockKind::Exact))
        .await
        .unwrap();
    let original = provider.read_token(path).await.unwrap().unwrap();
    assert_server_time(&original, before, now(&provider).await);
    assert_fixed_ttl(&provider, path, before).await;
    let before = now(&provider).await;
    assert!(provider
        .compare_and_write_token(path, &original, &token("a", 1, PathLockKind::Tree))
        .await
        .unwrap());
    let tree = provider.read_token(path).await.unwrap().unwrap();
    assert_server_time(&tree, before, now(&provider).await);
    assert_eq!(tree.lock_type, PathLockKind::Tree);
    assert_fixed_ttl(&provider, path, before).await;
    let before = now(&provider).await;
    assert!(provider
        .compare_and_write_token(path, &tree, &token("a", tree.time_ns, PathLockKind::Exact))
        .await
        .unwrap());
    let exact = provider.read_token(path).await.unwrap().unwrap();
    assert_eq!(exact.time_ns, tree.time_ns);
    assert_fixed_ttl(&provider, path, before).await;
    assert!(!provider
        .compare_and_write_token(path, &tree, &original)
        .await
        .unwrap());

    let before = now(&provider).await;
    assert!(provider.refresh_token(path, "a", 0).await.unwrap());
    assert_server_time(
        &provider.read_token(path).await.unwrap().unwrap(),
        before,
        now(&provider).await,
    );
    assert_fixed_ttl(&provider, path, before).await;
    provider
        .try_create_token(other, &token("a", 0, PathLockKind::Exact))
        .await
        .unwrap();
    let before = now(&provider).await;
    let paths = [path.to_string(), other.to_string()];
    assert!(provider
        .refresh_tokens(&paths, "a", u128::MAX)
        .await
        .iter()
        .all(|r| matches!(r, Ok(true))));
    let first = provider.read_token(path).await.unwrap().unwrap();
    let second = provider.read_token(other).await.unwrap().unwrap();
    assert_eq!(first.time_ns, second.time_ns);
    assert_server_time(&first, before, now(&provider).await);
    assert_fixed_ttl(&provider, path, before).await;

    let before = now(&provider).await;
    assert!(provider.remove_token(other, "a", false).await.unwrap());
    assert_fixed_ttl(&provider, path, before).await;
    let previous = provider.read_token(path).await.unwrap().unwrap();
    let before = now(&provider).await;
    let upgrade = provider
        .try_acquire_batch_atomic(&[request(path, PathLockKind::Tree)], "a", 0, u128::MAX)
        .await
        .unwrap()
        .unwrap();
    provider
        .rollback_acquisitions_atomic(&upgrade, "a")
        .await
        .unwrap()
        .unwrap();
    assert_eq!(provider.read_token(path).await.unwrap(), Some(previous));
    assert_fixed_ttl(&provider, path, before).await;
    runtime.close().await.unwrap();
}

#[tokio::test]
async fn legacy_epoch_tokens_remain_compatible_with_reentry_takeover_and_refresh() {
    let Some((runtime, provider)) = fixture().await else {
        return;
    };
    let old = "/local/account/old";
    let future = "/local/account/future";
    let server_time = now(&provider).await;
    // Seed the exact old wire format, including nanoseconds finer than TIME.
    seed(
        &provider,
        old,
        &token("old", server_time - 31_000_000_000 + 7, PathLockKind::Exact),
    )
    .await;
    let legacy = token("future", server_time + 31_000_000_007, PathLockKind::Exact);
    seed(&provider, future, &legacy).await;
    let acquired = provider
        .try_acquire_batch_atomic_with_stats(&[request(old, PathLockKind::Exact)], "new", 0, 0)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(acquired.stale_tokens_removed, 1);
    let before = now(&provider).await;
    assert!(matches!(
        provider
            .try_acquire_batch_atomic(
                &[request(future, PathLockKind::Exact)],
                "other",
                u128::MAX,
                u128::MAX
            )
            .await,
        Err(PathLockError::Conflict { .. })
    ));
    let reentry = provider
        .try_acquire_batch_atomic(&[request(future, PathLockKind::Exact)], "future", 0, 0)
        .await
        .unwrap()
        .unwrap();
    assert!(matches!(reentry[0].change, AcquisitionChange::Reentrant));
    assert_eq!(provider.read_token(future).await.unwrap(), Some(legacy));
    assert!(provider
        .refresh_token(future, "future", u128::MAX)
        .await
        .unwrap());
    assert_server_time(
        &provider.read_token(future).await.unwrap().unwrap(),
        before,
        now(&provider).await,
    );
    runtime.close().await.unwrap();
}
