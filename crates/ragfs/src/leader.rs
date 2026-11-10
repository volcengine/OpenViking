//! CacheRuntime-backed leader leases.

use crate::cache_runtime::{
    CacheError, CacheOperation, CacheResult, CacheRuntime, Expiration, ScriptDefinition,
    ScriptRequest, ScriptValue, SetCondition, SetOptions, SetResult,
};
use bytes::Bytes;
use std::sync::Arc;
use std::time::Duration;
use uuid::Uuid;

const RENEW_SCRIPT_ID: &str = "ragfs.leader.renew.v1";
const RELEASE_SCRIPT_ID: &str = "ragfs.leader.release.v1";

const RENEW_SCRIPT: &str = r#"
if redis.call("GET", KEYS[1]) ~= ARGV[1] then
    return 0
end
return redis.call("PEXPIRE", KEYS[1], ARGV[2])
"#;

const RELEASE_SCRIPT: &str = r#"
if redis.call("GET", KEYS[1]) ~= ARGV[1] then
    return 0
end
return redis.call("DEL", KEYS[1])
"#;

const SCRIPT_DEFINITIONS: &[ScriptDefinition] = &[
    ScriptDefinition {
        id: RENEW_SCRIPT_ID,
        redis_lua: RENEW_SCRIPT,
    },
    ScriptDefinition {
        id: RELEASE_SCRIPT_ID,
        redis_lua: RELEASE_SCRIPT,
    },
];

/// One leader lease. Its ownership token stays inside AGFS.
#[derive(Clone)]
pub struct LeaderLease {
    redis_key: String,
    token: String,
}

/// Atomic leader election over the shared CacheRuntime.
pub struct LeaderManager {
    runtime: Arc<CacheRuntime>,
    service_name: String,
}

impl LeaderManager {
    /// Bind leader election to one service namespace.
    pub fn new(runtime: Arc<CacheRuntime>, service_name: &str) -> CacheResult<Self> {
        validate_component("service_name", service_name)?;
        runtime.require_operations(&[CacheOperation::Set, CacheOperation::ExecuteScript])?;
        for definition in SCRIPT_DEFINITIONS {
            runtime.register_script(*definition)?;
        }
        Ok(Self {
            runtime,
            service_name: service_name.to_string(),
        })
    }

    /// Attempt to acquire one logical leadership key.
    pub async fn try_acquire(
        &self,
        key: &str,
        holder_id: &str,
        ttl: Duration,
    ) -> CacheResult<Option<LeaderLease>> {
        validate_component("leader key", key)?;
        validate_component("holder_id", holder_id)?;
        ttl_millis(ttl)?;
        let token = Uuid::new_v4().to_string();
        let redis_key = format!("{{{}}}:ov:leader:{key}", self.service_name);
        let result = self
            .runtime
            .set(
                &redis_key,
                Bytes::copy_from_slice(token.as_bytes()),
                SetOptions {
                    expiration: Some(Expiration::After(ttl)),
                    condition: SetCondition::Nx,
                    keep_ttl: false,
                },
            )
            .await?;
        match result {
            SetResult::Applied => Ok(Some(LeaderLease { redis_key, token })),
            SetResult::ConditionNotMet => Ok(None),
        }
    }

    /// Extend a lease only when its hidden ownership token still matches.
    pub async fn renew(&self, lease: &LeaderLease, ttl: Duration) -> CacheResult<bool> {
        let ttl_ms = ttl_millis(ttl)?;
        self.compare_script(
            RENEW_SCRIPT_ID,
            lease,
            vec![Bytes::from(ttl_ms.to_string())],
        )
        .await
    }

    /// Release a lease only when its hidden ownership token still matches.
    pub async fn release(&self, lease: &LeaderLease) -> CacheResult<bool> {
        self.compare_script(RELEASE_SCRIPT_ID, lease, Vec::new())
            .await
    }

    async fn compare_script(
        &self,
        script_id: &str,
        lease: &LeaderLease,
        trailing_args: Vec<Bytes>,
    ) -> CacheResult<bool> {
        let mut args = Vec::with_capacity(1 + trailing_args.len());
        args.push(Bytes::copy_from_slice(lease.token.as_bytes()));
        args.extend(trailing_args);
        let result = self
            .runtime
            .execute_script(ScriptRequest {
                script_id: script_id.to_string(),
                keys: vec![lease.redis_key.clone()],
                args,
            })
            .await?
            .decode()?;
        match result {
            ScriptValue::Integer(value) => Ok(value == 1),
            other => Err(CacheError::InvalidData(format!(
                "leader script {script_id} returned non-integer result: {other:?}"
            ))),
        }
    }
}

fn validate_component(name: &str, value: &str) -> CacheResult<()> {
    if value.is_empty()
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-' | b':'))
    {
        return Err(CacheError::InvalidArgument(format!(
            "{name} must contain only ASCII letters, digits, '.', '_', '-' or ':'"
        )));
    }
    Ok(())
}

fn ttl_millis(ttl: Duration) -> CacheResult<u128> {
    let ttl_ms = ttl.as_millis();
    if ttl_ms == 0 || ttl_ms > i64::MAX as u128 {
        return Err(CacheError::InvalidArgument(
            "leader ttl must be between 1 millisecond and i64::MAX milliseconds".to_string(),
        ));
    }
    Ok(ttl_ms)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::cache_runtime::{MemoryMockProvider, ScriptResult};

    fn runtime_with_script_result(value: i64) -> Arc<CacheRuntime> {
        let provider = Arc::new(
            MemoryMockProvider::new()
                .with_script_handler(move |_| ScriptResult::encode(&ScriptValue::Integer(value))),
        );
        CacheRuntime::memory_with_provider(provider)
    }

    #[tokio::test]
    async fn only_one_candidate_acquires_a_live_key() {
        let manager = LeaderManager::new(runtime_with_script_result(1), "service-a").unwrap();
        let first = manager
            .try_acquire("watch", "node-a", Duration::from_secs(1))
            .await
            .unwrap()
            .unwrap();

        assert!(manager
            .try_acquire("watch", "node-b", Duration::from_secs(1))
            .await
            .unwrap()
            .is_none());
        assert!(manager.renew(&first, Duration::from_secs(1)).await.unwrap());
        assert!(manager.release(&first).await.unwrap());
    }

    #[tokio::test]
    async fn compare_failure_reports_lost_ownership() {
        let manager = LeaderManager::new(runtime_with_script_result(0), "service-a").unwrap();
        let lease = manager
            .try_acquire("watch", "node-a", Duration::from_secs(1))
            .await
            .unwrap()
            .unwrap();

        assert!(!manager.renew(&lease, Duration::from_secs(1)).await.unwrap());
        assert!(!manager.release(&lease).await.unwrap());
    }

    #[test]
    fn rejects_sub_millisecond_ttl_and_unsafe_keys() {
        assert!(ttl_millis(Duration::from_nanos(1)).is_err());
        let manager = LeaderManager::new(runtime_with_script_result(1), "service-a").unwrap();
        let result = tokio::runtime::Runtime::new()
            .unwrap()
            .block_on(manager.try_acquire("bad key", "node-a", Duration::from_secs(1)));
        assert!(matches!(result, Err(CacheError::InvalidArgument(_))));
    }
}
