//! Redis-backed PathLock storage. Tree locks must fit within one account or
//! system scope; global roots cannot atomically cover the separate scope HASHes.

use std::collections::BTreeMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use crate::metrics::duration::DurationMetrics;
use async_trait::async_trait;
use bytes::Bytes;
use opentelemetry::KeyValue;

use crate::cache_runtime::{
    CacheError, CacheOperation, CacheRuntime, ScriptDefinition, ScriptRequest, ScriptValue,
};

use super::codec::LockTokenCodec;
use super::path::validate_normalized_logical_path;
use super::provider::{
    AcquisitionChange, AtomicAcquisition, AtomicAcquisitionResult, PathLockHandleMode,
    PathLockProvider,
};
use super::types::{
    HandoffRecoveryReason, LockHolder, LockToken, PathLockError, PathLockKind, PathLockRequest,
    PathLockResult,
};

const ACQUIRE_BATCH_ID: &str = "pathlock.acquire_batch.v5";
const ROLLBACK_BATCH_ID: &str = "pathlock.rollback_batch.v2";
const COMPARE_WRITE_ID: &str = "pathlock.compare_write.v3";
const COMPARE_REMOVE_ID: &str = "pathlock.compare_remove.v2";
const REFRESH_ID: &str = "pathlock.refresh.v2";
const REFRESH_BATCH_ID: &str = "pathlock.refresh_batch.v3";
const MUTATE_HOLDER_ID: &str = "pathlock.mutate_holder.v1";
const REPLACE_HOLDER_ID: &str = "pathlock.replace_holder.v1";
// Bound each Lua execution so large leases do not monopolize Redis.
const REFRESH_BATCH_SIZE: usize = 256;
const IS_LOCKED_ID: &str = "pathlock.is_locked.v3";
const SCAN_DESCENDANTS_ID: &str = "pathlock.scan_descendants.v1";
const READ_MANY_ID: &str = "pathlock.read_many.v1";
const CREATE_ID: &str = "pathlock.create.v3";
// Keep both lease_ms and 2 * lease_ms exactly representable by Lua numbers.
const MAX_LEASE_MS: u64 = (1_u64 << 52) - 1;
// Observe requests approaching the default 20ms Redis command timeout.
const SLOW_ACQUIRE_THRESHOLD: Duration = Duration::from_millis(10);

#[macro_use]
mod scripts;
mod runtime;
mod operations;

use scripts::*;

/// Redis-backed PathLock provider.
pub struct RedisPathLockProvider {
    runtime: Arc<CacheRuntime>,
    service_name: String,
    lease_ms: u64,
    acquire_duration: DurationMetrics,
    slow_acquisitions: AtomicU64,
}

impl RedisPathLockProvider {
    /// Create Redis-backed PathLock storage and register its Lua scripts.
    pub fn new(
        runtime: Arc<CacheRuntime>,
        service_name: &str,
        lock_expire_secs: f64,
    ) -> PathLockResult<Self> {
        if service_name.is_empty()
            || !service_name
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
        {
            return Err(PathLockError::InvalidRequest(
                "pathlock Redis service_name must contain only ASCII letters, digits, '.', '_' or '-'"
                    .to_string(),
            ));
        }
        if !lock_expire_secs.is_finite() || lock_expire_secs < 1.0 {
            return Err(PathLockError::InvalidRequest(
                "pathlock lock_expire_secs must be finite and >= 1.0".to_string(),
            ));
        }
        let lease_ms = (lock_expire_secs * 1_000.0).ceil();
        if lease_ms > MAX_LEASE_MS as f64 {
            return Err(PathLockError::InvalidRequest(
                "pathlock Redis lease exceeds Lua's exact integer range".to_string(),
            ));
        }
        runtime
            .require_operations(&[CacheOperation::ExecuteScript])
            .map_err(Self::cache_error)?;
        for definition in SCRIPT_DEFINITIONS {
            runtime
                .register_script(*definition)
                .map_err(Self::cache_error)?;
        }
        Ok(Self {
            runtime,
            service_name: service_name.to_string(),
            lease_ms: lease_ms as u64,
            acquire_duration: DurationMetrics::default(),
            slow_acquisitions: AtomicU64::new(0),
        })
    }

}

#[cfg(test)]
#[path = "../tests/cache/test_refresh.rs"]
mod refresh_tests;

#[cfg(test)]
#[path = "../tests/cache/test_time.rs"]
mod time_tests;

#[cfg(test)]
#[path = "../tests/cache/test_handoff_rollback.rs"]
mod handoff_tests;

#[cfg(test)]
#[path = "../tests/cache/test_clock_perf.rs"]
mod clock_perf_tests;

#[cfg(test)]
#[path = "../tests/cache/mod.rs"]
mod tests;
