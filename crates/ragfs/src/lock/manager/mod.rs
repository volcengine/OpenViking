//! PathLockManager — single source of truth for all lock semantics.
//!
//! Owns the provider, resolver, lease registry, and metrics. All lock acquisition,
//! refresh, release, handoff, and adopt flows go through this manager.

use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering as AtomicOrdering};
use std::sync::{Arc, Mutex as StdMutex, MutexGuard, Weak};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use futures::stream::{FuturesUnordered, StreamExt};
use rand::Rng;
use tokio::sync::{Mutex as TokioMutex, RwLock};
use tracing::{debug, error, warn};
use uuid::Uuid;

use crate::core::internal_names::{EXACT_LOCK_FILE_PREFIX, PATH_LOCK_FILE};
use crate::core::{FileSystem, FsContextView};

use super::metrics::{LockMetrics, MAX_RECENT_CONFLICTS};
use super::path::normalize_logical_path;
use super::provider::{
    validate_handoff_source, AcquisitionChange, AtomicAcquisition, PathLockHandleMode,
    PathLockProvider,
};
use super::resolver::{LockPathResolver, ResolvedExactPaths};
use super::types::{
    BorrowedPathLockLease, HandoffRecoveryReason, LockToken, OwnedPathLockLease, PathLockConflict,
    PathLockError, PathLockHandoffRef, PathLockKind, PathLockLease, PathLockObserveSnapshot,
    PathLockRequest, PathLockResult,
};


mod acquire;
mod handoff;
mod lease;
mod observe;
mod registry;

use super::codec;
use registry::{InvalidationReason, LeaseRegistry, OwnerRegistry, OwnerState};
#[cfg(test)]
use registry::LeaseInvalidation;

/// Configuration for the PathLockManager.
#[derive(Debug, Clone)]
pub struct PathLockConfig {
    /// Built-in provider name: `filesystem`, `memory`, `sqlite`, `postgres`, or `cache`.
    pub provider: String,
    /// Named Runtime selected when the provider is cache.
    pub instance: String,
    /// OpenViking instance name used by SQLite, PostgreSQL, and cache providers.
    pub service_name: Option<String>,
    /// Absolute local database file shared by SQLite lock participants.
    pub sqlite_path: Option<String>,
    /// Environment variable containing a PostgreSQL connection string.
    pub postgres_dsn_env: Option<String>,
    /// Default wait timeout for auto-acquired locks.
    pub lock_timeout_secs: f64,
    /// Seconds after which a lock token is considered stale.
    pub lock_expire_secs: f64,
}

impl Default for PathLockConfig {
    fn default() -> Self {
        Self {
            provider: "filesystem".to_string(),
            instance: "default".to_string(),
            service_name: None,
            sqlite_path: None,
            postgres_dsn_env: None,
            lock_timeout_secs: 0.0,
            lock_expire_secs: 30.0,
        }
    }
}

/// Revoke authorization before the provider's expiry, leaving a safety margin.
const LEASE_VALIDITY_RATIO: f64 = 0.8;
/// Retry invalidated cleanup for six heartbeat intervals, then forget locally.
const CLEANUP_RETRY_INTERVALS: f64 = 6.0;
/// Decision for one automatic PathLock operation under the current FS context.
#[derive(Debug, Clone)]
pub enum AutoPathLockAction {
    /// The scoped context explicitly disables automatic PathLock.
    Disabled,
    /// An active owned lease already covers the operation.
    Covered(OwnedPathLockLease),
    /// No lease is supplied, so the caller must acquire locks.
    Acquire,
}

/// The central lock manager.
pub struct PathLockManager {
    provider: Arc<dyn PathLockProvider>,
    resolver: LockPathResolver,
    lease_registry: Arc<LeaseRegistry>,
    config: PathLockConfig,
    metrics: Arc<RwLock<LockMetrics>>,
    account_metrics: StdMutex<HashMap<Option<String>, Arc<AccountLockMetrics>>>,
    conflict_sequence: AtomicU64,
}

#[derive(Default)]
struct AccountLockMetrics {
    waiting: AtomicUsize,
    counters: StdMutex<AcquisitionCounters>,
}

#[derive(Default)]
struct AcquisitionCounters {
    stale_tokens_removed: usize,
    wait_duration_ns: u64,
    conflict_count: usize,
    recent_conflicts: Vec<(u64, PathLockConflict)>,
}

impl AccountLockMetrics {
    fn counters(&self) -> MutexGuard<'_, AcquisitionCounters> {
        self.counters
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    fn record_conflict(&self, conflict: PathLockConflict, sequence: &AtomicU64) {
        let mut counters = self.counters();
        counters.conflict_count += 1;
        if counters.recent_conflicts.len() >= MAX_RECENT_CONFLICTS {
            counters.recent_conflicts.remove(0);
        }
        counters
            .recent_conflicts
            .push((sequence.fetch_add(1, AtomicOrdering::Relaxed), conflict));
    }
}

impl PathLockManager {
    fn current_account_metrics(&self) -> Arc<AccountLockMetrics> {
        self.account_metrics
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .entry(FsContextView::current().account_id().map(str::to_owned))
            .or_default()
            .clone()
    }

    /// Return the actual provider's lease handle representation.
    pub fn handle_mode(&self) -> PathLockHandleMode {
        self.provider.handle_mode()
    }

    /// Return the configured default timeout for automatic lock acquisition.
    pub fn default_lock_timeout(&self) -> Duration {
        Duration::from_secs_f64(self.config.lock_timeout_secs)
    }

    /// Create a new PathLockManager.
    pub fn new(
        fs: Arc<dyn FileSystem>,
        provider: Arc<dyn PathLockProvider>,
        config: PathLockConfig,
    ) -> Self {
        let registry = Arc::new(LeaseRegistry::default());
        let metrics = Arc::new(RwLock::new(LockMetrics::default()));

        // Spawn a background task that refreshes active owned leases.
        let refresh_registry = registry.clone();
        let refresh_provider = provider.clone();
        let refresh_config = config.clone();
        let refresh_metrics = metrics.clone();
        let refresh_interval = Duration::from_secs_f64(config.lock_expire_secs / 3.0);
        tokio::spawn(async move {
            let validity = Self::lease_validity(&refresh_config);
            let cleanup_window = Self::cleanup_window(&refresh_config);
            let mut ticks = tokio::time::interval_at(
                tokio::time::Instant::now() + refresh_interval,
                refresh_interval,
            );
            ticks.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
            let mut pending = FuturesUnordered::new();
            let mut in_flight = HashSet::new();

            loop {
                tokio::select! {
                    _ = ticks.tick() => {
                        // Keep ticking while another lease is waiting on its owner or provider.
                        for (lease_ref, owner) in refresh_registry.snapshot() {
                            if in_flight.insert(lease_ref.clone()) {
                                let provider = refresh_provider.clone();
                                let registry = refresh_registry.clone();
                                pending.push(async move {
                                    let result = Self::maintain_lease_with(
                                        &provider, &registry, owner, &lease_ref, validity, cleanup_window,
                                    ).await;
                                    (lease_ref, result)
                                });
                            }
                        }
                        refresh_registry.prune();
                    }
                    Some((lease_ref, result)) = pending.next() => {
                        in_flight.remove(&lease_ref);
                        match result {
                            Ok(true) => {
                                refresh_metrics.write().await.stale_leases_released += 1;
                                debug!(
                                    %lease_ref,
                                    active_count = refresh_registry.active_count(),
                                    expire_secs = refresh_config.lock_expire_secs,
                                    "released stale pathlock lease in background refresh"
                                );
                            }
                            Ok(false) => {}
                            Err(error) => {
                                warn!(%lease_ref, %error, "failed to release stale pathlock lease");
                            }
                        }
                    }
                }
            }
        });

        Self {
            resolver: LockPathResolver::new(fs),
            provider,
            lease_registry: registry,
            config,
            metrics,
            account_metrics: StdMutex::new(HashMap::new()),
            conflict_sequence: AtomicU64::new(0),
        }
    }

    /// Current nanosecond timestamp.
    fn now_ns() -> u128 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos()
    }

    /// Generate a new UUIDv4 owner_id.
    fn new_owner_id() -> String {
        Uuid::new_v4().to_string()
    }

    /// Normalize, deduplicate, and sort requests with Tree taking precedence over Exact.
    fn normalize_requests(requests: &[PathLockRequest]) -> PathLockResult<Vec<PathLockRequest>> {
        let mut by_path: HashMap<String, PathLockKind> = HashMap::new();
        for request in requests {
            let path = normalize_logical_path(&request.path)?.to_string();
            by_path
                .entry(path)
                .and_modify(|kind| {
                    if request.kind == PathLockKind::Tree {
                        *kind = PathLockKind::Tree;
                    }
                })
                .or_insert(request.kind);
        }

        let mut normalized: Vec<PathLockRequest> = by_path
            .into_iter()
            .map(|(path, kind)| PathLockRequest { path, kind })
            .collect();
        normalized.sort_by(|a, b| {
            a.path
                .len()
                .cmp(&b.path.len())
                .then_with(|| a.path.cmp(&b.path))
        });
        Ok(normalized)
    }

    /// Return the configured lock expiry in nanoseconds.
    fn lock_expire_ns(&self) -> u128 {
        (self.config.lock_expire_secs * 1_000_000_000.0) as u128
    }

    fn lease_validity(config: &PathLockConfig) -> Duration {
        Duration::from_secs_f64(config.lock_expire_secs * LEASE_VALIDITY_RATIO)
    }

    fn cleanup_window(config: &PathLockConfig) -> Duration {
        Duration::from_secs_f64(config.lock_expire_secs / 3.0 * CLEANUP_RETRY_INTERVALS)
    }

    /// Check if a token is stale based on config.
    fn is_stale(&self, token: &LockToken, now_ns: u128) -> bool {
        token
            .live_kind(now_ns.saturating_sub(self.lock_expire_ns()))
            .is_none()
    }

}

#[cfg(test)]
#[path = "../tests/mod.rs"]
mod tests;
