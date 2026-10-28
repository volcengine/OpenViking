//! PathLockManager — single source of truth for all lock semantics.
//!
//! Owns the provider, resolver, lease registry, and metrics. All lock acquisition,
//! refresh, release, handoff, and adopt flows go through this manager.

use std::collections::{HashMap, HashSet};
use std::sync::atomic::{AtomicU64, AtomicUsize, Ordering as AtomicOrdering};
use std::sync::{Arc, Mutex as StdMutex, MutexGuard, Weak};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use rand::Rng;
use tokio::sync::{Mutex as TokioMutex, RwLock};
use tracing::{debug, error, info, warn};
use uuid::Uuid;

use crate::core::internal_names::{EXACT_LOCK_FILE_PREFIX, PATH_LOCK_FILE};
use crate::core::{FileSystem, FsContextView};

use super::metrics::LockMetrics;
use super::path::normalize_logical_path;
use super::provider::{AcquisitionChange, AtomicAcquisition, PathLockHandleMode, PathLockProvider};
use super::resolver::{LockPathResolver, ResolvedExactPaths};
use super::types::{
    BorrowedPathLockLease, LockToken, OwnedPathLockLease, PathLockConflict, PathLockError,
    PathLockHandoffRef, PathLockKind, PathLockLease, PathLockObserveSnapshot, PathLockRequest,
    PathLockResult,
};

/// Configuration for the PathLockManager.
#[derive(Debug, Clone)]
pub struct PathLockConfig {
    /// Built-in provider name: `filesystem`, `memory`, `sqlite`, or `cache`.
    pub provider: String,
    /// OpenViking instance name used by the SQLite/cache-backed provider.
    pub service_name: Option<String>,
    /// Absolute local database file shared by SQLite lock participants.
    pub sqlite_path: Option<String>,
    /// Default wait timeout for auto-acquired locks.
    pub lock_timeout_secs: f64,
    /// Seconds after which a lock token is considered stale.
    pub lock_expire_secs: f64,
}

impl Default for PathLockConfig {
    fn default() -> Self {
        Self {
            provider: "filesystem".to_string(),
            service_name: None,
            sqlite_path: None,
            lock_timeout_secs: 0.0,
            lock_expire_secs: 30.0,
        }
    }
}

/// Lock-poll backoff constants used by conflict-retry loops.
///
/// Keep the first retry no faster than the previous fixed 50 ms poll, then
/// spread waiters with bounded jitter while backing off to 500 ms.
const INITIAL_POLL_INTERVAL_MS: u64 = 50;
const MAX_POLL_INTERVAL_MS: u64 = 500;
const POLL_BACKOFF_NUMERATOR: u64 = 3;
const POLL_BACKOFF_DENOMINATOR: u64 = 2;
const POLL_JITTER_PERCENT: u64 = 20;
/// Revoke authorization before the provider's expiry, leaving a safety margin.
const LEASE_VALIDITY_RATIO: f64 = 0.8;
/// Retry invalidated cleanup for six heartbeat intervals, then forget locally.
const CLEANUP_RETRY_INTERVALS: f64 = 6.0;

impl PathLockManager {
    fn poll_interval_bounds(attempt: u32) -> (u64, u64) {
        let mut base_ms = INITIAL_POLL_INTERVAL_MS;
        for _ in 0..attempt {
            if base_ms >= MAX_POLL_INTERVAL_MS {
                break;
            }
            base_ms = base_ms
                .saturating_mul(POLL_BACKOFF_NUMERATOR)
                .saturating_add(POLL_BACKOFF_DENOMINATOR - 1)
                / POLL_BACKOFF_DENOMINATOR;
        }
        base_ms = base_ms.min(MAX_POLL_INTERVAL_MS);

        let jitter_ms = base_ms.saturating_mul(POLL_JITTER_PERCENT) / 100;
        let lower_ms = base_ms
            .saturating_sub(jitter_ms)
            .max(INITIAL_POLL_INTERVAL_MS);
        let upper_ms = base_ms.saturating_add(jitter_ms).min(MAX_POLL_INTERVAL_MS);
        (lower_ms, upper_ms)
    }

    fn poll_interval_for_attempt(attempt: u32) -> Duration {
        let (lower_ms, upper_ms) = Self::poll_interval_bounds(attempt);
        Duration::from_millis(rand::thread_rng().gen_range(lower_ms..=upper_ms))
    }

    fn retry_delay(attempt: u32, remaining: Duration) -> Duration {
        Self::poll_interval_for_attempt(attempt).min(remaining)
    }
}

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

/// Internal registry entry for an active lease.
#[derive(Debug, Clone)]
struct LeaseEntry {
    lease: PathLockLease,
    ownership_ref: String,
    /// True once handoff() has parked this lease for a consumer to adopt.
    /// The entry stays in the registry and keeps refreshing, but the original
    /// ownership_ref can no longer operate it.
    pending_handoff: bool,
    lock_kinds: HashMap<String, PathLockKind>,
    last_active_at: Instant,
    /// Irreversible; retain the capability and references until cleanup finishes.
    invalidation: Option<LeaseInvalidation>,
}

/// The first irreversible authorization revocation, retained through cleanup.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct LeaseInvalidation {
    at: Instant,
    reason: InvalidationReason,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum InvalidationReason {
    TokenLost,
    ConfirmationDeadlineExceeded,
    ReleaseFailed,
}

impl LeaseEntry {
    /// Keep the first cause and timestamp, including during cleanup retries.
    fn invalidate(&mut self, at: Instant, reason: InvalidationReason, paths: &[&str]) {
        if self.invalidation.is_some() {
            return;
        }
        self.invalidation = Some(LeaseInvalidation { at, reason });
        warn!(
            lease_ref = %self.lease.lease_ref,
            owner_id = %self.lease.owner_id,
            ?reason,
            affected_paths = ?paths,
            "pathlock lease invalidated"
        );
    }
}

#[derive(Debug)]
struct TokenHealth {
    last_confirmed_at: Instant,
    consecutive_failures: u64,
}

#[derive(Debug)]
enum DowngradeTokenResult {
    Downgraded,
    AlreadyExact,
    Missing,
    OwnerLost,
    Changed,
}

#[derive(Debug, Default)]
struct OwnerRegistry {
    entries: HashMap<String, LeaseEntry>,
    lock_refs: HashMap<String, usize>,
    token_health: HashMap<String, TokenHealth>,
    // ponytail: grows with handoffs while this owner lives; encode generations if measured.
    consumed_handoff_refs: HashSet<String>,
}

impl OwnerRegistry {
    /// Register one owned lease and increment its local token references.
    fn insert(
        &mut self,
        expected_owner_id: &str,
        lease: PathLockLease,
        ownership_ref: String,
        lock_kinds: &[PathLockKind],
        confirmed_at: Instant,
    ) -> PathLockResult<()> {
        let lease_ref = lease.lease_ref.clone();
        if lease.owner_id != expected_owner_id {
            return Err(PathLockError::Internal(format!(
                "pathlock lease owner '{}' does not match owner state '{expected_owner_id}'",
                lease.owner_id
            )));
        }
        if self.entries.contains_key(&lease_ref) {
            return Err(PathLockError::Internal(format!(
                "duplicate pathlock lease ref '{lease_ref}'"
            )));
        }
        if lease.lock_paths.len() != lock_kinds.len() {
            return Err(PathLockError::Internal(format!(
                "pathlock lease '{lease_ref}' has mismatched lock paths and kinds"
            )));
        }
        let lock_kinds = lease
            .lock_paths
            .iter()
            .cloned()
            .zip(lock_kinds.iter().copied())
            .collect();
        let mut unique_paths = HashSet::new();
        for lock_path in &lease.lock_paths {
            if unique_paths.insert(lock_path.clone()) {
                *self.lock_refs.entry(lock_path.clone()).or_default() += 1;
                self.token_health
                    .entry(lock_path.clone())
                    .or_insert(TokenHealth {
                        last_confirmed_at: confirmed_at,
                        consecutive_failures: 0,
                    });
            }
        }
        self.entries.insert(
            lease_ref,
            LeaseEntry {
                lease,
                ownership_ref,
                pending_handoff: false,
                lock_kinds,
                last_active_at: Instant::now(),
                invalidation: None,
            },
        );
        Ok(())
    }

    /// Find one active lease by its opaque reference.
    fn get_by_ref(&self, lease_ref: &str) -> Option<&LeaseEntry> {
        self.entries.get(lease_ref)
    }

    /// Remove one newly inserted lease without touching provider state.
    fn rollback_insert(&mut self, lease_ref: &str) -> Option<LeaseEntry> {
        let entry = self.entries.remove(lease_ref)?;
        self.decrement_refs(&entry.lease.lock_paths);
        Some(entry)
    }

    /// True when the entry exists, is owned (not parked for handoff) and its
    /// ownership_ref matches. Used to gate acquisition/refresh against a stale
    /// capability under the same write guard that performs the mutation.
    fn capability_matches(&self, lease_ref: &str, ownership_ref: &str) -> bool {
        self.entries.get(lease_ref).is_some_and(|entry| {
            !entry.pending_handoff
                && entry.invalidation.is_none()
                && entry.ownership_ref == ownership_ref
        })
    }

    /// Return the strongest remaining kind after one lease drops a lock path.
    fn strongest_kind_excluding(&self, lease_ref: &str, lock_path: &str) -> Option<PathLockKind> {
        self.entries
            .iter()
            .filter(|(candidate_ref, _)| candidate_ref.as_str() != lease_ref)
            .filter(|(_, entry)| entry.invalidation.is_none())
            .filter_map(|(_, entry)| entry.lock_kinds.get(lock_path).copied())
            .max_by_key(|kind| match kind {
                PathLockKind::Exact => 0,
                PathLockKind::Tree => 1,
            })
    }

    /// Remove one committed lock path from a lease and its local reference count.
    fn remove_path(&mut self, lease_ref: &str, lock_path: &str) {
        let remove_entry = {
            let Some(entry) = self.entries.get_mut(lease_ref) else {
                return;
            };
            if entry.lease.lock_paths.len() == entry.lease.covered_paths.len() {
                entry.lease.covered_paths = entry
                    .lease
                    .lock_paths
                    .iter()
                    .zip(&entry.lease.covered_paths)
                    .filter(|(path, _)| path.as_str() != lock_path)
                    .map(|(_, request)| request.clone())
                    .collect();
            }
            entry
                .lease
                .lock_paths
                .retain(|path| path.as_str() != lock_path);
            entry.lock_kinds.remove(lock_path);
            entry.lease.lock_paths.is_empty()
        };
        self.decrement_refs(&[lock_path.to_string()]);
        if remove_entry {
            self.entries.remove(lease_ref);
        }
    }

    /// Park an owned lease for handoff: keep it (and its refresh) in the registry,
    /// but reject the original ownership_ref. Requires a matching, not-yet-parked entry.
    fn mark_pending_handoff(&mut self, lease_ref: &str, ownership_ref: &str) -> PathLockResult<()> {
        match self.entries.get_mut(lease_ref) {
            Some(entry)
                if entry.ownership_ref == ownership_ref
                    && !entry.pending_handoff
                    && entry.invalidation.is_none() =>
            {
                entry.pending_handoff = true;
                Ok(())
            }
            Some(entry) if entry.pending_handoff => Err(PathLockError::InvalidRequest(format!(
                "pathlock lease '{lease_ref}' is already pending handoff"
            ))),
            Some(_) => Err(PathLockError::InvalidRequest(format!(
                "owned lease capability does not match ref '{lease_ref}'"
            ))),
            None => Err(PathLockError::InvalidRequest(format!(
                "unknown pathlock lease ref '{lease_ref}'"
            ))),
        }
    }

    /// Adopt a parked lease: migrate the entry to a fresh lease_ref key and rotate
    /// ownership_ref, permanently invalidating the producer's stale refs (both the
    /// lease_ref-only paths like borrow/auto-lock and the ownership_ref paths).
    /// Returns the refreshed owned lease. Errors when the entry is missing or not
    /// currently pending handoff.
    ///
    /// Re-keying entries does not change lock paths or reference counts.
    fn take_pending_handoff(
        &mut self,
        lease_ref: &str,
        new_lease_ref: String,
        new_ownership_ref: String,
    ) -> Option<OwnedPathLockLease> {
        if !self.entries.get(lease_ref)?.pending_handoff
            || self.entries.get(lease_ref)?.invalidation.is_some()
        {
            return None;
        }
        let mut entry = self.entries.remove(lease_ref)?;
        entry.pending_handoff = false;
        entry.ownership_ref = new_ownership_ref.clone();
        entry.lease.lease_ref = new_lease_ref.clone();
        entry.last_active_at = Instant::now();
        let owned = OwnedPathLockLease {
            lease: entry.lease.clone(),
            ownership_ref: new_ownership_ref,
        };
        self.entries.insert(new_lease_ref, entry);
        Some(owned)
    }

    /// Decrement local references for unique lock paths.
    fn decrement_refs(&mut self, lock_paths: &[String]) {
        let mut unique_paths = HashSet::new();
        for lock_path in lock_paths {
            if !unique_paths.insert(lock_path.clone()) {
                continue;
            }
            match self.lock_refs.get_mut(lock_path) {
                Some(count) if *count > 1 => *count -= 1,
                Some(_) => {
                    self.lock_refs.remove(lock_path);
                    self.token_health.remove(lock_path);
                }
                None => {}
            }
        }
    }

    /// Update the last successful refresh timestamp for one lease.
    fn touch(&mut self, lease_ref: &str) {
        if let Some(entry) = self.entries.get_mut(lease_ref) {
            entry.last_active_at = Instant::now();
        }
    }

    /// Invalidate entire leases sharing an actual token, never logical ancestors.
    fn invalidate_paths(
        &mut self,
        paths: &HashSet<String>,
        now: Instant,
        reason: InvalidationReason,
    ) {
        for entry in self.entries.values_mut() {
            if entry.invalidation.is_some() {
                continue;
            }
            let affected_paths: Vec<String> = entry
                .lease
                .lock_paths
                .iter()
                .filter(|path| paths.contains(*path))
                .cloned()
                .collect();
            if !affected_paths.is_empty() {
                if reason == InvalidationReason::ConfirmationDeadlineExceeded {
                    for path in &affected_paths {
                        if let Some(health) = self.token_health.get(path) {
                            warn!(
                                lease_ref = %entry.lease.lease_ref,
                                owner_id = %entry.lease.owner_id,
                                ?reason,
                                lock_path = %path,
                                since_last_confirmation_ms = now
                                    .saturating_duration_since(health.last_confirmed_at)
                                    .as_millis() as u64,
                                failures = health.consecutive_failures,
                                "pathlock token confirmation deadline exceeded"
                            );
                        }
                    }
                }
                entry.invalidate(
                    now,
                    reason,
                    &affected_paths
                        .iter()
                        .map(String::as_str)
                        .collect::<Vec<_>>(),
                );
            }
        }
    }

    fn invalidate_expired(&mut self, now: Instant, validity: Duration) {
        let expired = self
            .token_health
            .iter()
            .filter(|(_, health)| {
                now.saturating_duration_since(health.last_confirmed_at) >= validity
            })
            .map(|(path, _)| path.clone())
            .collect();
        self.invalidate_paths(
            &expired,
            now,
            InvalidationReason::ConfirmationDeadlineExceeded,
        );
    }

    fn has_only_invalid_refs(&self, path: &str) -> bool {
        self.lock_refs.contains_key(path)
            && !self.entries.values().any(|entry| {
                entry.invalidation.is_none()
                    && entry
                        .lease
                        .lock_paths
                        .iter()
                        .any(|lock_path| lock_path == path)
            })
    }

    /// Both heartbeat and explicit refresh use this state transition.
    fn apply_refresh_results(
        &mut self,
        lease_ref: &str,
        paths: &[String],
        results: Vec<PathLockResult<bool>>,
        started_at: Instant,
        validity: Duration,
    ) -> &'static str {
        // A late success must never restore a lease that crossed its deadline.
        let now = Instant::now();
        self.invalidate_expired(now, validity);
        if !self.entries.contains_key(lease_ref) {
            return "lost";
        }
        let mut lost_paths = HashSet::new();
        let mut all_ok = results.len() == paths.len();
        let mut results = results.into_iter();
        for path in paths {
            let Some(health) = self.token_health.get_mut(path) else {
                continue;
            };
            match results.next() {
                Some(Ok(true)) => {
                    health.last_confirmed_at = started_at;
                    health.consecutive_failures = 0;
                }
                Some(Ok(false)) => {
                    lost_paths.insert(path.clone());
                    all_ok = false;
                }
                result => {
                    health.consecutive_failures = health.consecutive_failures.saturating_add(1);
                    warn!(%lease_ref, owner_id = %self.entries[lease_ref].lease.owner_id,
                        lock_path = %path, failures = health.consecutive_failures,
                        since_last_confirmation_ms = now.saturating_duration_since(health.last_confirmed_at).as_millis() as u64,
                        ?result, "pathlock refresh uncertain");
                    all_ok = false;
                }
            }
        }
        self.invalidate_paths(&lost_paths, now, InvalidationReason::TokenLost);
        self.invalidate_expired(now, validity);
        if self.entries[lease_ref].invalidation.is_some() {
            "lost"
        } else if all_ok {
            self.touch(lease_ref);
            "refreshed"
        } else {
            "failed"
        }
    }
}

#[derive(Debug)]
struct OwnerState {
    owner_id: String,
    registry: TokioMutex<OwnerRegistry>,
}

impl OwnerState {
    /// Create unpublished state for one owner.
    fn new(owner_id: String) -> Self {
        Self {
            owner_id,
            registry: TokioMutex::new(OwnerRegistry::default()),
        }
    }
}

#[derive(Debug, Default)]
struct LeaseIndex {
    by_lease: HashMap<String, Arc<OwnerState>>,
    by_owner: HashMap<String, Weak<OwnerState>>,
}

#[derive(Debug, Default)]
struct LeaseRegistry {
    index: StdMutex<LeaseIndex>,
}

struct WaitingCountGuard<'a>(Option<&'a AtomicUsize>);

impl Drop for WaitingCountGuard<'_> {
    /// Undo the waiting increment on every exit path.
    fn drop(&mut self) {
        if let Some(counter) = self.0 {
            counter.fetch_sub(1, AtomicOrdering::Relaxed);
        }
    }
}

impl LeaseRegistry {
    /// Lock the short-lived index, recovering data if another task panicked.
    fn lock(&self) -> MutexGuard<'_, LeaseIndex> {
        self.index
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    /// Resolve one lease to its owner state without awaiting owner work.
    fn resolve(&self, lease_ref: &str) -> Option<Arc<OwnerState>> {
        self.lock().by_lease.get(lease_ref).cloned()
    }

    /// Resolve one owner to its live state without awaiting owner work.
    fn resolve_owner(&self, owner_id: &str) -> Option<Arc<OwnerState>> {
        self.lock().by_owner.get(owner_id).and_then(Weak::upgrade)
    }

    /// Resolve or create the canonical live state for one owner under the index mutex.
    fn resolve_or_create_owner(&self, owner_id: &str) -> Arc<OwnerState> {
        let mut index = self.lock();
        if let Some(owner) = index.by_owner.get(owner_id).and_then(Weak::upgrade) {
            return owner;
        }
        let owner = Arc::new(OwnerState::new(owner_id.to_string()));
        index
            .by_owner
            .insert(owner_id.to_string(), Arc::downgrade(&owner));
        owner
    }

    /// Publish a lease only after its owner registry entry is committed.
    fn publish(&self, lease_ref: String, owner: Arc<OwnerState>) -> PathLockResult<()> {
        let mut index = self.lock();
        if index.by_lease.contains_key(&lease_ref) {
            return Err(PathLockError::Internal(
                "duplicate pathlock lease ref".to_string(),
            ));
        }
        if index
            .by_owner
            .get(&owner.owner_id)
            .and_then(Weak::upgrade)
            .is_some_and(|existing| !Arc::ptr_eq(&existing, &owner))
        {
            return Err(PathLockError::Internal(
                "duplicate pathlock owner state".to_string(),
            ));
        }
        index
            .by_owner
            .insert(owner.owner_id.clone(), Arc::downgrade(&owner));
        index.by_lease.insert(lease_ref, owner);
        Ok(())
    }

    /// Remove one lease index entry while its owner mutex is held.
    fn unpublish(&self, lease_ref: &str, owner: &Arc<OwnerState>) -> PathLockResult<()> {
        let mut index = self.lock();
        if !index
            .by_lease
            .get(lease_ref)
            .is_some_and(|existing| Arc::ptr_eq(existing, owner))
        {
            return Err(PathLockError::Internal(
                "invalid pathlock lease index".to_string(),
            ));
        }
        index.by_lease.remove(lease_ref);
        Ok(())
    }

    /// Atomically move one published lease reference to a fresh key.
    fn rekey(
        &self,
        old_lease_ref: &str,
        new_lease_ref: String,
        owner: &Arc<OwnerState>,
    ) -> PathLockResult<()> {
        let mut index = self.lock();
        if index.by_lease.contains_key(&new_lease_ref) {
            return Err(PathLockError::Internal(
                "duplicate pathlock lease ref".to_string(),
            ));
        }
        if !index
            .by_lease
            .get(old_lease_ref)
            .is_some_and(|existing| Arc::ptr_eq(existing, owner))
        {
            return Err(PathLockError::Internal(
                "invalid pathlock lease index".to_string(),
            ));
        }
        index.by_lease.remove(old_lease_ref);
        index.by_lease.insert(new_lease_ref, owner.clone());
        Ok(())
    }

    /// Snapshot published leases without holding the index across async work.
    fn snapshot(&self) -> Vec<(String, Arc<OwnerState>)> {
        self.lock()
            .by_lease
            .iter()
            .map(|(lease_ref, owner)| (lease_ref.clone(), owner.clone()))
            .collect()
    }

    /// Count currently published leases under the short index lock.
    fn active_count(&self) -> usize {
        self.lock().by_lease.len()
    }

    /// Remove owner index entries whose weak state no longer has a live lease.
    fn prune(&self) {
        self.lock()
            .by_owner
            .retain(|_, owner| owner.strong_count() > 0);
    }
}

/// The central lock manager.
pub struct PathLockManager {
    provider: Arc<dyn PathLockProvider>,
    resolver: LockPathResolver,
    lease_registry: Arc<LeaseRegistry>,
    config: PathLockConfig,
    metrics: Arc<RwLock<LockMetrics>>,
    waiting_lock_count: AtomicUsize,
    wait_duration_ns: AtomicU64,
}

impl PathLockManager {
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
            loop {
                tokio::time::sleep(refresh_interval).await;
                let leases = refresh_registry.snapshot();
                for (lease_ref, owner) in leases {
                    let mut owner_registry = owner.registry.lock().await;
                    owner_registry
                        .invalidate_expired(Instant::now(), Self::lease_validity(&refresh_config));
                    let Some(entry) = owner_registry.entries.get(&lease_ref) else {
                        continue;
                    };
                    if entry.invalidation.is_none() {
                        Self::refresh_entry_with(
                            &refresh_provider,
                            &mut owner_registry,
                            &owner.owner_id,
                            &lease_ref,
                            Self::lease_validity(&refresh_config),
                        )
                        .await;
                    }
                }
                let stale_candidates = refresh_registry.snapshot();
                let mut removed = 0;
                for (lease_ref, owner) in stale_candidates {
                    match Self::release_lease_paths_with(
                        &refresh_provider,
                        &refresh_registry,
                        owner,
                        &lease_ref,
                        None,
                        None,
                        Some(Self::cleanup_window(&refresh_config)),
                    )
                    .await
                    {
                        Ok(true) => removed += 1,
                        Ok(false) => {}
                        Err(error) => {
                            warn!(lease_ref = %lease_ref, error = %error, "failed to release stale pathlock lease");
                        }
                    }
                }
                refresh_registry.prune();
                let active_count = refresh_registry.active_count();
                refresh_metrics.write().await.stale_leases_released += removed;
                if removed > 0 {
                    info!(
                        removed_count = removed,
                        active_count = active_count,
                        expire_secs = refresh_config.lock_expire_secs,
                        "released stale pathlock leases in background refresh"
                    );
                }
            }
        });

        Self {
            resolver: LockPathResolver::new(fs),
            provider,
            lease_registry: registry,
            config,
            metrics,
            waiting_lock_count: AtomicUsize::new(0),
            wait_duration_ns: AtomicU64::new(0),
        }
    }

    /// Build one release error when the provider reports the token changed underneath us.
    fn release_changed_error(lock_path: &str) -> PathLockError {
        PathLockError::Io(format!("lock path '{lock_path}' changed while releasing"))
    }

    /// Remove one owned token and distinguish ownership loss from a same-owner CAS change.
    ///
    /// A tree lock is stored inside the directory it protects. A successful
    /// recursive delete therefore removes both the directory and its lock token
    /// before the outer lease is released. Missing tokens remain idempotent.
    async fn remove_owned_token_with(
        provider: &Arc<dyn PathLockProvider>,
        lock_path: &str,
        owner_id: &str,
    ) -> PathLockResult<bool> {
        match provider.remove_token(lock_path, owner_id, false).await {
            Ok(true) => Ok(true),
            Ok(false) => match provider.read_token(lock_path).await? {
                None => Ok(true),
                Some(token) if token.owner_id != owner_id => Ok(false),
                Some(_) => Err(Self::release_changed_error(lock_path)),
            },
            Err(error) => Err(error),
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

    async fn refresh_entry_with(
        provider: &Arc<dyn PathLockProvider>,
        registry: &mut OwnerRegistry,
        owner_id: &str,
        lease_ref: &str,
        validity: Duration,
    ) -> &'static str {
        let paths = registry.entries[lease_ref].lease.lock_paths.clone();
        let started_at = Instant::now();
        let results = provider
            .refresh_tokens(&paths, owner_id, Self::now_ns())
            .await;
        registry.apply_refresh_results(lease_ref, &paths, results, started_at, validity)
    }

    /// Check if a token is stale based on config.
    fn is_stale(&self, token: &LockToken, now_ns: u128) -> bool {
        now_ns.saturating_sub(token.time_ns) > self.lock_expire_ns()
    }

    /// Return whether an acquire-loop error should be retried within the wait budget.
    fn is_retryable_error(error: &PathLockError) -> bool {
        matches!(
            error,
            PathLockError::Conflict { .. }
                | PathLockError::Busy { .. }
                | PathLockError::EmptyToken { .. }
        )
    }

    /// Preserve both errors and mark a failed rollback as non-retryable.
    fn rollback_error(error: PathLockError, rollback_error: PathLockError) -> PathLockError {
        PathLockError::Internal(format!("{error}; rollback failed: {rollback_error}"))
    }

    /// Resolve a reentrant lease to its owner state or create unpublished state.
    fn resolve_owner_state(
        &self,
        owner_capability: Option<(&str, &str)>,
    ) -> PathLockResult<Arc<OwnerState>> {
        match owner_capability {
            Some((lease_ref, _)) => self.lease_registry.resolve(lease_ref).ok_or_else(|| {
                PathLockError::InvalidRequest(format!(
                    "owned lease capability does not match ref '{lease_ref}'"
                ))
            }),
            None => Ok(Arc::new(OwnerState::new(Self::new_owner_id()))),
        }
    }

    // ── Public API ──

    /// Acquire an exact lock on a single path.
    pub async fn acquire_exact(
        &self,
        path: &str,
        timeout: Duration,
        owner_capability: Option<(&str, &str)>,
    ) -> PathLockResult<OwnedPathLockLease> {
        let request = PathLockRequest {
            path: path.to_string(),
            kind: PathLockKind::Exact,
        };
        self.acquire_batch(&[request], timeout, owner_capability)
            .await
    }

    /// Acquire a tree lock on a single path.
    pub async fn acquire_tree(
        &self,
        path: &str,
        timeout: Duration,
        owner_capability: Option<(&str, &str)>,
    ) -> PathLockResult<OwnedPathLockLease> {
        let request = PathLockRequest {
            path: path.to_string(),
            kind: PathLockKind::Tree,
        };
        self.acquire_batch(&[request], timeout, owner_capability)
            .await
    }

    /// Acquire exact locks on multiple paths.
    pub async fn acquire_exact_batch(
        &self,
        paths: &[String],
        timeout: Duration,
        owner_capability: Option<(&str, &str)>,
    ) -> PathLockResult<OwnedPathLockLease> {
        let requests: Vec<PathLockRequest> = paths
            .iter()
            .map(|p| PathLockRequest {
                path: p.clone(),
                kind: PathLockKind::Exact,
            })
            .collect();
        self.acquire_batch(&requests, timeout, owner_capability)
            .await
    }

    /// Acquire tree locks on multiple paths.
    pub async fn acquire_tree_batch(
        &self,
        paths: &[String],
        timeout: Duration,
        owner_capability: Option<(&str, &str)>,
    ) -> PathLockResult<OwnedPathLockLease> {
        let requests: Vec<PathLockRequest> = paths
            .iter()
            .map(|p| PathLockRequest {
                path: p.clone(),
                kind: PathLockKind::Tree,
            })
            .collect();
        self.acquire_batch(&requests, timeout, owner_capability)
            .await
    }

    /// Acquire a mixed batch of exact and tree locks.
    pub async fn acquire_exact_tree_batch(
        &self,
        exact_paths: &[String],
        tree_paths: &[String],
        timeout: Duration,
        owner_capability: Option<(&str, &str)>,
    ) -> PathLockResult<OwnedPathLockLease> {
        let mut requests: Vec<PathLockRequest> = exact_paths
            .iter()
            .map(|p| PathLockRequest {
                path: p.clone(),
                kind: PathLockKind::Exact,
            })
            .collect();
        requests.extend(tree_paths.iter().map(|p| PathLockRequest {
            path: p.clone(),
            kind: PathLockKind::Tree,
        }));
        self.acquire_batch(&requests, timeout, owner_capability)
            .await
    }

    /// Acquire a batch of locks with all-or-nothing semantics.
    ///
    /// Requests are sorted deterministically by `(len(path), path)` to avoid deadlocks.
    pub async fn acquire_batch(
        &self,
        requests: &[PathLockRequest],
        timeout: Duration,
        owner_capability: Option<(&str, &str)>,
    ) -> PathLockResult<OwnedPathLockLease> {
        if requests.is_empty() {
            return Err(PathLockError::InvalidRequest(
                "lock request batch must not be empty".to_string(),
            ));
        }
        let sorted = Self::normalize_requests(requests)?;

        let owner = self.resolve_owner_state(owner_capability)?;
        let owner_id = owner.owner_id.clone();
        debug!(owner_id = %owner_id, requests = ?requests, normalized_requests = ?sorted, timeout_ms = timeout.as_millis() as u64, "pathlock acquire batch start");

        let start = Instant::now();
        let mut waiting = WaitingCountGuard(None);
        let mut retry_attempt: u32 = 0;
        let mut first_attempt = true;

        let result = loop {
            let (mut owner_registry, locked_immediately) = match owner.registry.try_lock() {
                Ok(owner_registry) => (owner_registry, true),
                Err(_) => {
                    if waiting.0.is_none() {
                        debug!(owner_id = %owner_id, requests = ?sorted, timeout_ms = timeout.as_millis() as u64, "pathlock acquire batch waiting for owner state");
                        waiting.0 = Some(&self.waiting_lock_count);
                        self.waiting_lock_count
                            .fetch_add(1, AtomicOrdering::Relaxed);
                    }
                    let remaining = timeout.saturating_sub(start.elapsed());
                    if remaining.is_zero() {
                        break Err(PathLockError::Timeout {
                            elapsed_ms: start.elapsed().as_millis() as u64,
                        });
                    }
                    match tokio::time::timeout(remaining, owner.registry.lock()).await {
                        Ok(owner_registry) => (owner_registry, false),
                        Err(_) => {
                            break Err(PathLockError::Timeout {
                                elapsed_ms: start.elapsed().as_millis() as u64,
                            });
                        }
                    }
                }
            };

            if (!first_attempt || !locked_immediately) && start.elapsed() >= timeout {
                drop(owner_registry);
                break Err(PathLockError::Timeout {
                    elapsed_ms: start.elapsed().as_millis() as u64,
                });
            }
            owner_registry.invalidate_expired(Instant::now(), Self::lease_validity(&self.config));
            if let Some((lease_ref, ownership_ref)) = owner_capability {
                if !owner_registry.capability_matches(lease_ref, ownership_ref) {
                    break Err(PathLockError::InvalidRequest(format!(
                        "owned lease capability does not match ref '{lease_ref}'"
                    )));
                }
            }
            first_attempt = false;
            let confirmed_at = Instant::now();
            let acquired_lock_paths = match self.try_acquire_batch_once(&sorted, &owner_id).await {
                Ok(acquired) => acquired,
                Err(err) => {
                    drop(owner_registry);
                    if !Self::is_retryable_error(&err) {
                        break Err(err);
                    }

                    if waiting.0.is_none() {
                        debug!(owner_id = %owner_id, requests = ?sorted, timeout_ms = timeout.as_millis() as u64, error = %err, "pathlock acquire batch entered wait state");
                        waiting.0 = Some(&self.waiting_lock_count);
                        self.waiting_lock_count
                            .fetch_add(1, AtomicOrdering::Relaxed);
                    }
                    if let PathLockError::Conflict {
                        ref lock_path,
                        ref owner,
                        kind,
                    } = &err
                    {
                        self.metrics
                            .write()
                            .await
                            .record_conflict(PathLockConflict {
                                lock_path: lock_path.clone(),
                                conflicting_owner: owner.clone(),
                                conflicting_kind: *kind,
                            });
                    }

                    if start.elapsed() >= timeout {
                        break Err(PathLockError::Timeout {
                            elapsed_ms: start.elapsed().as_millis() as u64,
                        });
                    }

                    let remaining = timeout.saturating_sub(start.elapsed());
                    tokio::time::sleep(Self::retry_delay(retry_attempt, remaining)).await;
                    retry_attempt = retry_attempt.saturating_add(1);
                    continue;
                }
            };

            owner_registry.invalidate_expired(Instant::now(), Self::lease_validity(&self.config));
            let source_invalid = owner_capability.is_some_and(|(lease_ref, ownership_ref)| {
                !owner_registry.capability_matches(lease_ref, ownership_ref)
            });
            let reuses_invalid_token = acquired_lock_paths
                .iter()
                .any(|acquisition| owner_registry.has_only_invalid_refs(&acquisition.handle));
            if source_invalid
                || reuses_invalid_token
                || confirmed_at.elapsed() >= Self::lease_validity(&self.config)
            {
                let error = PathLockError::InvalidRequest(
                    "pathlock lease expired during acquisition".to_string(),
                );
                let rollback = self
                    .rollback_acquisitions(&acquired_lock_paths, &owner_id)
                    .await;
                break match rollback {
                    Ok(()) => Err(error),
                    Err(rollback_error) => Err(Self::rollback_error(error, rollback_error)),
                };
            }
            let lease = PathLockLease {
                lease_ref: Self::new_owner_id(),
                owner_id: owner_id.clone(),
                lock_paths: acquired_lock_paths
                    .iter()
                    .map(|acquisition| acquisition.handle.clone())
                    .collect(),
                covered_paths: sorted.clone(),
            };
            let ownership_ref = Self::new_owner_id();
            let lock_kinds: Vec<_> = sorted.iter().map(|request| request.kind).collect();
            if let Err(error) = owner_registry.insert(
                &owner_id,
                lease.clone(),
                ownership_ref.clone(),
                &lock_kinds,
                confirmed_at,
            ) {
                let rollback = self
                    .rollback_acquisitions(&acquired_lock_paths, &owner_id)
                    .await;
                break match rollback {
                    Ok(()) => Err(error),
                    Err(rollback_error) => Err(Self::rollback_error(error, rollback_error)),
                };
            }
            if let Err(error) = self
                .lease_registry
                .publish(lease.lease_ref.clone(), owner.clone())
            {
                owner_registry.rollback_insert(&lease.lease_ref);
                let rollback = self
                    .rollback_acquisitions(&acquired_lock_paths, &owner_id)
                    .await;
                break match rollback {
                    Ok(()) => Err(error),
                    Err(rollback_error) => Err(Self::rollback_error(error, rollback_error)),
                };
            }

            break Ok(OwnedPathLockLease {
                lease,
                ownership_ref,
            });
        };

        match result {
            Ok(owned) => {
                let elapsed_ns = u64::try_from(start.elapsed().as_nanos()).unwrap_or(u64::MAX);
                let _ = self.wait_duration_ns.fetch_update(
                    AtomicOrdering::Relaxed,
                    AtomicOrdering::Relaxed,
                    |total| Some(total.saturating_add(elapsed_ns)),
                );
                if waiting.0.is_some() {
                    debug!(lease_ref = %owned.lease.lease_ref, owner_id = %owned.lease.owner_id, lock_paths = ?owned.lease.lock_paths, covered_paths = ?owned.lease.covered_paths, wait_ms = start.elapsed().as_millis() as u64, "pathlock acquire batch succeeded after waiting");
                }
                Ok(owned)
            }
            Err(error) => Err(error),
        }
    }

    /// Run one complete acquire attempt and roll back every recorded token mutation on error.
    async fn try_acquire_batch_once(
        &self,
        requests: &[PathLockRequest],
        owner_id: &str,
    ) -> PathLockResult<Vec<AtomicAcquisition>> {
        let now_ns = Self::now_ns();
        match self
            .provider
            .try_acquire_batch_atomic_with_stats(
                requests,
                owner_id,
                now_ns,
                now_ns.saturating_sub(self.lock_expire_ns()),
            )
            .await
        {
            Ok(Some(result)) => {
                if result.stale_tokens_removed != 0 {
                    self.metrics.write().await.stale_tokens_removed += result.stale_tokens_removed;
                }
                return Ok(result.acquisitions);
            }
            Ok(None) => {}
            Err(error) => return Err(error),
        }

        let mut acquired = Vec::new();
        let mut exact_resolutions: HashMap<String, ResolvedExactPaths> = HashMap::new();
        let acquisition: PathLockResult<()> = async {
            for request in requests {
                match request.kind {
                    PathLockKind::Exact => {
                        self.check_ancestor_tree_locks(&request.path, owner_id)
                            .await?;
                        let resolved = self.resolver.resolve_exact_paths(&request.path).await?;
                        self.check_lock_paths(&resolved.conflict_paths, owner_id)
                            .await?;
                        let lock_path = resolved.acquire_lock_path.clone();
                        exact_resolutions.insert(request.path.clone(), resolved);
                        self.ensure_lock_dir(&lock_path).await.map_err(|error| {
                            PathLockError::Io(format!("failed to create lock dir: {error}"))
                        })?;
                        let change = self
                            .try_acquire_one(&lock_path, owner_id, PathLockKind::Exact)
                            .await?;
                        acquired.push(AtomicAcquisition {
                            handle: lock_path,
                            change,
                        });
                    }
                    PathLockKind::Tree => {
                        let resolved = self.resolver.resolve_tree_paths(&request.path).await?;
                        self.check_ancestor_tree_locks(&request.path, owner_id)
                            .await?;
                        self.check_lock_paths(&resolved.conflict_paths, owner_id)
                            .await?;
                        self.check_descendant_locks(&request.path, owner_id).await?;
                        let lock_path = resolved.acquire_lock_path;
                        self.ensure_lock_dir(&lock_path).await.map_err(|error| {
                            PathLockError::Io(format!("failed to create lock dir: {error}"))
                        })?;
                        let change = self
                            .try_acquire_one(&lock_path, owner_id, PathLockKind::Tree)
                            .await?;
                        acquired.push(AtomicAcquisition {
                            handle: lock_path,
                            change,
                        });
                    }
                }
            }
            Ok(())
        }
        .await;
        if let Err(error) = acquisition {
            if let Err(rollback_error) = self.rollback_acquisitions(&acquired, owner_id).await {
                return Err(Self::rollback_error(error, rollback_error));
            }
            return Err(error);
        }

        let verification: PathLockResult<()> = async {
            for request in requests {
                match request.kind {
                    PathLockKind::Exact => {
                        self.check_ancestor_tree_locks(&request.path, owner_id)
                            .await?;
                        let resolved = exact_resolutions.get(&request.path).ok_or_else(|| {
                            PathLockError::Io(format!(
                                "missing cached exact resolution for '{}'",
                                request.path
                            ))
                        })?;
                        self.check_lock_paths(&resolved.conflict_paths, owner_id)
                            .await?;
                    }
                    PathLockKind::Tree => {
                        self.check_ancestor_tree_locks(&request.path, owner_id)
                            .await?;
                        let tree_candidates = self
                            .resolver
                            .resolve_tree_conflict_paths(&request.path)
                            .await?;
                        self.check_lock_paths(&tree_candidates, owner_id).await?;
                        self.check_descendant_locks(&request.path, owner_id).await?;
                    }
                }
            }
            Ok(())
        }
        .await;
        if let Err(error) = verification {
            if let Err(rollback_error) = self.rollback_acquisitions(&acquired, owner_id).await {
                return Err(Self::rollback_error(error, rollback_error));
            }
            return Err(error);
        }
        Ok(acquired)
    }

    /// Roll back token changes made by one incomplete batch acquisition.
    async fn rollback_acquisitions(
        &self,
        acquired_lock_paths: &[AtomicAcquisition],
        owner_id: &str,
    ) -> PathLockResult<()> {
        if let Some(result) = self
            .provider
            .rollback_acquisitions_atomic(acquired_lock_paths, owner_id)
            .await?
        {
            return Ok(result);
        }

        let mut first_error = None;
        for acquisition in acquired_lock_paths.iter().rev() {
            let result = match &acquisition.change {
                AcquisitionChange::Created { .. } => {
                    Self::remove_owned_token_with(&self.provider, &acquisition.handle, owner_id)
                        .await
                        .map(|_| ())
                }
                AcquisitionChange::Reentrant => Ok(()),
                AcquisitionChange::Upgraded {
                    previous,
                    replacement,
                } => match self
                    .provider
                    .compare_and_write_token(&acquisition.handle, replacement, previous)
                    .await
                {
                    Ok(true) => Ok(()),
                    Ok(false) => Err(PathLockError::Io(format!(
                        "failed to roll back token upgrade at '{}'",
                        acquisition.handle
                    ))),
                    Err(error) => Err(error),
                },
            };
            if first_error.is_none() {
                if let Err(error) = result {
                    first_error = Some(error);
                }
            }
        }
        match first_error {
            Some(error) => Err(error),
            None => Ok(()),
        }
    }

    /// Try to acquire a single lock path.
    async fn try_acquire_one(
        &self,
        lock_path: &str,
        owner_id: &str,
        kind: PathLockKind,
    ) -> PathLockResult<AcquisitionChange> {
        let now_ns = Self::now_ns();
        let token = LockToken {
            owner_id: owner_id.to_string(),
            time_ns: now_ns,
            lock_type: kind,
        };

        // Check existing token.
        if let Some(existing) = self.provider.read_token(lock_path).await? {
            if existing.owner_id == owner_id {
                if existing.lock_type == PathLockKind::Exact && kind == PathLockKind::Tree {
                    if self
                        .provider
                        .compare_and_write_token(lock_path, &existing, &token)
                        .await?
                    {
                        debug!(lock_path = %lock_path, owner_id = %owner_id, from = ?existing.lock_type, to = ?kind, "upgraded pathlock token for existing owner");
                        return Ok(AcquisitionChange::Upgraded {
                            previous: existing,
                            replacement: token,
                        });
                    }
                    return Err(PathLockError::Conflict {
                        lock_path: lock_path.to_string(),
                        owner: owner_id.to_string(),
                        kind: existing.lock_type,
                    });
                }
                debug!(lock_path = %lock_path, owner_id = %owner_id, kind = ?existing.lock_type, "reused existing pathlock token for same owner");
                return Ok(AcquisitionChange::Reentrant);
            }
            // Replace only the stale snapshot we observed. A concurrent heartbeat
            // or takeover must not be erased by a separate remove/create sequence.
            if self.is_stale(&existing, now_ns)
                && self
                    .provider
                    .compare_and_write_token(lock_path, &existing, &token)
                    .await?
            {
                info!(lock_path = %lock_path, stale_owner = %existing.owner_id, owner_id = %owner_id, "replaced stale pathlock token during acquire");
                self.metrics.write().await.stale_tokens_removed += 1;
                return Ok(AcquisitionChange::Created { replacement: token });
            }
            return Err(PathLockError::Conflict {
                lock_path: lock_path.to_string(),
                owner: existing.owner_id,
                kind: existing.lock_type,
            });
        }

        self.provider.try_create_token(lock_path, &token).await?;
        debug!(lock_path = %lock_path, owner_id = %owner_id, kind = ?kind, "created new pathlock token");
        Ok(AcquisitionChange::Created { replacement: token })
    }

    /// Check concrete lock-file paths for a live token owned by another owner.
    async fn check_lock_paths(&self, lock_paths: &[String], owner_id: &str) -> PathLockResult<()> {
        let now_ns = Self::now_ns();
        for lock_path in lock_paths {
            if let Some(token) = self.provider.read_token(lock_path).await? {
                if token.owner_id != owner_id && !self.is_stale(&token, now_ns) {
                    return Err(PathLockError::Conflict {
                        lock_path: lock_path.clone(),
                        owner: token.owner_id,
                        kind: token.lock_type,
                    });
                }
            }
        }
        Ok(())
    }

    /// Check ancestor directories for tree locks that would conflict.
    async fn check_ancestor_tree_locks(&self, path: &str, owner_id: &str) -> PathLockResult<()> {
        let now_ns = Self::now_ns();

        for parent in LockPathResolver::ancestor_paths(path) {
            for ancestor_lock in self.resolver.resolve_tree_conflict_paths(&parent).await? {
                if let Some(token) = self.provider.read_token(&ancestor_lock).await? {
                    // Only Tree locks propagate to descendants; an Exact lock on
                    // /a must not block /a/b.
                    if token.lock_type == PathLockKind::Tree
                        && token.owner_id != owner_id
                        && !self.is_stale(&token, now_ns)
                    {
                        return Err(PathLockError::Conflict {
                            lock_path: ancestor_lock,
                            owner: token.owner_id,
                            kind: token.lock_type,
                        });
                    }
                }
            }
        }
        Ok(())
    }

    /// Check for descendant locks that would conflict with a tree lock.
    async fn check_descendant_locks(&self, path: &str, owner_id: &str) -> PathLockResult<()> {
        let scan_start = Instant::now();
        let descendants = self.provider.scan_descendant_locks(path).await?;
        let now_ns = Self::now_ns();

        let metrics = self.metrics.read().await;
        metrics.descendant_scan.record(scan_start.elapsed(), &[]);
        drop(metrics);
        debug!(path = %path, descendant_count = descendants.len(), scan_ms = scan_start.elapsed().as_millis() as u64, "scanned descendant pathlock tokens");

        for lp in descendants {
            if let Some(token) = self.provider.read_token(&lp).await? {
                if token.owner_id != owner_id && !self.is_stale(&token, now_ns) {
                    return Err(PathLockError::Conflict {
                        lock_path: lp,
                        owner: token.owner_id,
                        kind: token.lock_type,
                    });
                }
            }
        }
        Ok(())
    }

    /// Ensure the parent directory of a lock path exists.
    async fn ensure_lock_dir(&self, lock_path: &str) -> PathLockResult<()> {
        if let Some(parent_end) = lock_path.rfind('/') {
            let parent = &lock_path[..parent_end];
            if parent.is_empty() || parent == "/" {
                return Ok(());
            }
            // Walk up creating missing ancestors.
            let mut missing: Vec<String> = Vec::new();
            let mut current = parent.to_string();
            loop {
                match self.resolver.fs.stat(&current).await {
                    Ok(info) if info.is_dir => break,
                    _ => {
                        missing.push(current.clone());
                        match current.rsplit_once('/') {
                            Some((p, _)) if !p.is_empty() => current = p.to_string(),
                            _ => break,
                        }
                    }
                }
            }
            let mut created_dirs = Vec::new();
            for dir in missing.into_iter().rev() {
                if let Err(mkdir_error) = self.resolver.fs.mkdir(&dir, 0o755).await {
                    match self.resolver.fs.stat(&dir).await {
                        Ok(info) if info.is_dir => continue,
                        _ => {
                            return Err(PathLockError::Io(format!(
                                "failed to create lock dir '{dir}': {mkdir_error}"
                            )));
                        }
                    }
                }
                created_dirs.push(dir);
            }
            if !created_dirs.is_empty() {
                debug!(lock_path = %lock_path, created_dirs = ?created_dirs, "ensured pathlock parent directories");
            }
        }
        Ok(())
    }

    /// Refresh an owned lease. Returns "refreshed", "lost", or "failed".
    pub async fn refresh(&self, lease: &OwnedPathLockLease) -> PathLockResult<String> {
        let owner = self
            .lease_registry
            .resolve(&lease.lease.lease_ref)
            .ok_or_else(|| {
                PathLockError::InvalidRequest(format!(
                    "owned lease capability does not match ref '{}'",
                    lease.lease.lease_ref
                ))
            })?;
        let mut owner_registry = owner.registry.lock().await;
        owner_registry.invalidate_expired(Instant::now(), Self::lease_validity(&self.config));
        if !owner_registry.capability_matches(&lease.lease.lease_ref, &lease.ownership_ref) {
            return Err(PathLockError::InvalidRequest(format!(
                "owned lease capability does not match ref '{}'",
                lease.lease.lease_ref
            )));
        }
        let result = Self::refresh_entry_with(
            &self.provider,
            &mut owner_registry,
            &owner.owner_id,
            &lease.lease.lease_ref,
            Self::lease_validity(&self.config),
        )
        .await;
        if result == "refreshed" {
            debug!(lease_ref = %lease.lease.lease_ref, owner_id = %lease.lease.owner_id, lock_paths = ?lease.lease.lock_paths, result = %result, "pathlock refresh completed");
        } else {
            warn!(lease_ref = %lease.lease.lease_ref, owner_id = %lease.lease.owner_id, lock_paths = ?lease.lease.lock_paths, result = %result, "pathlock refresh completed");
        }
        Ok(result.to_string())
    }

    /// Release an owned lease.
    pub async fn release(&self, lease: &OwnedPathLockLease) -> PathLockResult<()> {
        let owner = self
            .lease_registry
            .resolve(&lease.lease.lease_ref)
            .ok_or_else(|| {
                PathLockError::InvalidRequest(format!(
                    "owned lease capability does not match ref '{}'",
                    lease.lease.lease_ref
                ))
            })?;
        let result = Self::release_lease_paths_with(
            &self.provider,
            &self.lease_registry,
            owner,
            &lease.lease.lease_ref,
            Some(&lease.ownership_ref),
            None,
            None,
        )
        .await;
        let active_count = self.lease_registry.active_count();
        if let Err(release_error) = result {
            error!(
                lease_ref = %lease.lease.lease_ref,
                owner_id = %lease.lease.owner_id,
                lock_paths = ?lease.lease.lock_paths,
                error = %release_error,
                "failed to release pathlock lease"
            );
            return Err(release_error);
        }
        debug!(lease_ref = %lease.lease.lease_ref, owner_id = %lease.lease.owner_id, lock_paths = ?lease.lease.lock_paths, active_count = active_count, "released pathlock lease");
        Ok(())
    }

    /// Release selected lock paths from an owned lease.
    pub async fn release_selected(
        &self,
        lease: &OwnedPathLockLease,
        lock_paths: &[String],
    ) -> PathLockResult<()> {
        let owner = self
            .lease_registry
            .resolve(&lease.lease.lease_ref)
            .ok_or_else(|| {
                PathLockError::InvalidRequest(format!(
                    "owned lease capability does not match ref '{}'",
                    lease.lease.lease_ref
                ))
            })?;
        let result = Self::release_lease_paths_with(
            &self.provider,
            &self.lease_registry,
            owner,
            &lease.lease.lease_ref,
            Some(&lease.ownership_ref),
            Some(lock_paths),
            None,
        )
        .await;
        let active_count = self.lease_registry.active_count();
        result?;
        debug!(lease_ref = %lease.lease.lease_ref, owner_id = %lease.lease.owner_id, requested_lock_paths = ?lock_paths, active_count = active_count, "released selected pathlock lease paths");
        Ok(())
    }

    /// Release selected paths while serializing all state and provider work for one owner.
    async fn release_lease_paths_with(
        provider: &Arc<dyn PathLockProvider>,
        lease_registry: &Arc<LeaseRegistry>,
        owner: Arc<OwnerState>,
        lease_ref: &str,
        ownership_ref: Option<&str>,
        selected_paths: Option<&[String]>,
        cleanup_window: Option<Duration>,
    ) -> PathLockResult<bool> {
        let mut owner_registry = owner.registry.lock().await;
        let Some(entry) = owner_registry.entries.get(lease_ref) else {
            if ownership_ref.is_some() {
                return Err(PathLockError::InvalidRequest(format!(
                    "owned lease capability does not match ref '{lease_ref}'"
                )));
            }
            return Ok(false);
        };
        if let Some(ownership_ref) = ownership_ref {
            if entry.pending_handoff || entry.ownership_ref != ownership_ref {
                return Err(PathLockError::InvalidRequest(format!(
                    "owned lease capability does not match ref '{lease_ref}'"
                )));
            }
        }
        if let Some(window) = cleanup_window {
            let Some(invalidation) = entry.invalidation else {
                return Ok(false);
            };
            if invalidation.at.elapsed() >= window {
                error!(%lease_ref, owner_id = %owner.owner_id, reason = ?invalidation.reason,
                    invalidated_for_ms = invalidation.at.elapsed().as_millis() as u64,
                    remaining_paths = ?entry.lease.lock_paths,
                    "pathlock cleanup retry window exhausted; forgetting local lease");
                lease_registry.unpublish(lease_ref, &owner)?;
                owner_registry.rollback_insert(lease_ref);
                return Ok(true);
            }
        }
        let selected: Option<HashSet<&str>> =
            selected_paths.map(|paths| paths.iter().map(String::as_str).collect());
        let mut seen = HashSet::new();
        let target_paths: Vec<String> = entry
            .lease
            .lock_paths
            .iter()
            .filter(|path| {
                selected
                    .as_ref()
                    .is_none_or(|selected| selected.contains(path.as_str()))
            })
            .filter(|path| seen.insert((*path).clone()))
            .cloned()
            .collect();
        let mut first_error = None;

        for lock_path in target_paths {
            let Some(entry) = owner_registry.entries.get(lease_ref) else {
                break;
            };
            let ref_count = owner_registry
                .lock_refs
                .get(&lock_path)
                .copied()
                .ok_or_else(|| {
                    PathLockError::Internal(format!("missing local pathlock ref for '{lock_path}'"))
                })?;
            let released_kind = entry.lock_kinds.get(&lock_path).copied();
            let remaining_kind = owner_registry.strongest_kind_excluding(lease_ref, &lock_path);
            let invalidated = entry.invalidation.is_some();

            if ref_count == 1 {
                let result = if invalidated {
                    provider
                        .remove_token(&lock_path, &owner.owner_id, false)
                        .await
                } else {
                    Self::remove_owned_token_with(provider, &lock_path, &owner.owner_id).await
                };
                match result {
                    Ok(removed) => {
                        if !removed && !invalidated {
                            owner_registry.invalidate_paths(
                                &HashSet::from([lock_path.clone()]),
                                Instant::now(),
                                InvalidationReason::TokenLost,
                            );
                        }
                        if owner_registry.entries[lease_ref]
                            .lease
                            .lock_paths
                            .iter()
                            .all(|p| p == &lock_path)
                        {
                            lease_registry.unpublish(lease_ref, &owner)?;
                        }
                        owner_registry.remove_path(lease_ref, &lock_path);
                    }
                    Err(release_error) => {
                        // Stop authorizing writes while failed release is retried.
                        if let Some(entry) = owner_registry.entries.get_mut(lease_ref) {
                            entry.invalidate(
                                Instant::now(),
                                InvalidationReason::ReleaseFailed,
                                &[&lock_path],
                            );
                            warn!(%lease_ref, owner_id = %owner.owner_id, lock_path = %lock_path,
                                reason = ?entry.invalidation.unwrap().reason, error = %release_error,
                                "pathlock release failed; retaining lease for cleanup");
                        }
                        if first_error.is_none() {
                            first_error = Some(release_error);
                        }
                    }
                }
                continue;
            }

            let result = if released_kind == Some(PathLockKind::Tree)
                && remaining_kind == Some(PathLockKind::Exact)
            {
                Self::downgrade_token_to_exact_with(provider, &lock_path, &owner.owner_id)
                    .await
                    .and_then(|status| match status {
                        DowngradeTokenResult::Downgraded | DowngradeTokenResult::AlreadyExact => {
                            Ok(true)
                        }
                        DowngradeTokenResult::Missing | DowngradeTokenResult::OwnerLost => {
                            Ok(false)
                        }
                        DowngradeTokenResult::Changed => {
                            Err(Self::release_changed_error(&lock_path))
                        }
                    })
            } else {
                Ok(true)
            };

            match result {
                Ok(true) => {
                    if owner_registry
                        .entries
                        .get(lease_ref)
                        .is_some_and(|entry| entry.lease.lock_paths.iter().all(|p| p == &lock_path))
                    {
                        lease_registry.unpublish(lease_ref, &owner)?;
                    }
                    owner_registry.remove_path(lease_ref, &lock_path);
                }
                Ok(false) => {
                    owner_registry.invalidate_paths(
                        &HashSet::from([lock_path.clone()]),
                        Instant::now(),
                        InvalidationReason::TokenLost,
                    );
                    if owner_registry.entries[lease_ref]
                        .lease
                        .lock_paths
                        .iter()
                        .all(|p| p == &lock_path)
                    {
                        lease_registry.unpublish(lease_ref, &owner)?;
                    }
                    owner_registry.remove_path(lease_ref, &lock_path);
                }
                Err(release_error) => {
                    let owner_mismatch = matches!(
                        &release_error,
                        PathLockError::Conflict { owner: current_owner, .. }
                            if current_owner != &owner.owner_id
                    );
                    if owner_mismatch {
                        owner_registry.invalidate_paths(
                            &HashSet::from([lock_path.clone()]),
                            Instant::now(),
                            InvalidationReason::TokenLost,
                        );
                    }
                    if let Some(entry) = owner_registry.entries.get_mut(lease_ref) {
                        entry.invalidate(
                            Instant::now(),
                            InvalidationReason::ReleaseFailed,
                            &[&lock_path],
                        );
                        warn!(%lease_ref, owner_id = %owner.owner_id, lock_path = %lock_path,
                            reason = ?entry.invalidation.unwrap().reason, error = %release_error,
                            "pathlock release failed; retaining lease for cleanup");
                    }
                    if first_error.is_none() {
                        first_error = Some(release_error);
                    }
                }
            }
        }

        let removed = !owner_registry.entries.contains_key(lease_ref);
        if let Some(error) = first_error {
            if ownership_ref.is_some() || !removed {
                return Err(error);
            }
        }
        Ok(removed)
    }

    /// Atomically reduce one same-owner Tree token to Exact.
    async fn downgrade_token_to_exact_with(
        provider: &Arc<dyn PathLockProvider>,
        lock_path: &str,
        owner_id: &str,
    ) -> PathLockResult<DowngradeTokenResult> {
        let Some(current) = provider.read_token(lock_path).await? else {
            return Ok(DowngradeTokenResult::Missing);
        };
        if current.owner_id != owner_id {
            return Ok(DowngradeTokenResult::OwnerLost);
        }
        if current.lock_type == PathLockKind::Exact {
            return Ok(DowngradeTokenResult::AlreadyExact);
        }
        let replacement = LockToken {
            owner_id: current.owner_id.clone(),
            time_ns: current.time_ns,
            lock_type: PathLockKind::Exact,
        };
        if provider
            .compare_and_write_token(lock_path, &current, &replacement)
            .await?
        {
            return Ok(DowngradeTokenResult::Downgraded);
        }

        let Some(current) = provider.read_token(lock_path).await? else {
            return Ok(DowngradeTokenResult::Missing);
        };
        if current.owner_id != owner_id {
            return Ok(DowngradeTokenResult::OwnerLost);
        }
        if current.lock_type == PathLockKind::Exact {
            return Ok(DowngradeTokenResult::AlreadyExact);
        }
        Ok(DowngradeTokenResult::Changed)
    }

    /// Create a borrowed view of an owned lease.
    pub fn as_borrowed(&self, lease: &OwnedPathLockLease) -> BorrowedPathLockLease {
        BorrowedPathLockLease {
            lease: lease.lease.clone(),
        }
    }

    /// Export a handoff ref from an owned lease.
    pub fn to_handoff(&self, lease: &OwnedPathLockLease) -> PathLockHandoffRef {
        PathLockHandoffRef {
            lease_ref: Some(lease.lease.lease_ref.clone()),
            owner_id: lease.lease.owner_id.clone(),
            lock_paths: lease.lease.lock_paths.clone(),
            covered_paths: lease.lease.covered_paths.clone(),
        }
    }

    /// Park an owned lease for handoff. The entry stays in the registry and keeps
    /// refreshing, but the producer's ownership_ref can no longer operate it until
    /// a consumer adopts it.
    pub async fn handoff(&self, lease: &OwnedPathLockLease) -> PathLockResult<()> {
        let owner = self
            .lease_registry
            .resolve(&lease.lease.lease_ref)
            .ok_or_else(|| {
                PathLockError::InvalidRequest(format!(
                    "unknown pathlock lease ref '{}'",
                    lease.lease.lease_ref
                ))
            })?;
        let mut owner_registry = owner.registry.lock().await;
        owner_registry.invalidate_expired(Instant::now(), Self::lease_validity(&self.config));
        owner_registry.mark_pending_handoff(&lease.lease.lease_ref, &lease.ownership_ref)?;
        let active_count = self.lease_registry.active_count();
        debug!(lease_ref = %lease.lease.lease_ref, owner_id = %lease.lease.owner_id, lock_paths = ?lease.lease.lock_paths, active_count = active_count, "parked pathlock lease for handoff");
        Ok(())
    }

    /// Adopt a handoff ref, returning a new owned lease.
    pub async fn adopt(&self, handoff: &PathLockHandoffRef) -> PathLockResult<OwnedPathLockLease> {
        if handoff.owner_id.is_empty() || handoff.lock_paths.is_empty() {
            return Err(PathLockError::InvalidRequest(
                "handoff owner_id and lock_paths must not be empty".to_string(),
            ));
        }
        for request in &handoff.covered_paths {
            normalize_logical_path(&request.path)?;
        }

        // Local fast path: a handoff carrying lease_ref MUST be adopted from this
        // process's registry — it is never legacy. Adopt in-place (migrate to a fresh
        // lease_ref, rotate ownership_ref) so the background refresh never lapses.
        if let Some(lease_ref) = handoff.lease_ref.as_deref() {
            if let Some(owner) = self.lease_registry.resolve(lease_ref) {
                let mut owner_registry = owner.registry.lock().await;
                owner_registry
                    .invalidate_expired(Instant::now(), Self::lease_validity(&self.config));
                if owner_registry
                    .get_by_ref(lease_ref)
                    .is_some_and(|entry| entry.invalidation.is_some())
                {
                    return Err(PathLockError::HandoffFailed(format!(
                        "pathlock lease '{lease_ref}' is invalidated"
                    )));
                }
                let local = owner_registry.get_by_ref(lease_ref).map(|entry| {
                    (
                        entry.lease.owner_id == handoff.owner_id
                            && entry.lease.lock_paths == handoff.lock_paths
                            && entry.lease.covered_paths == handoff.covered_paths,
                        entry.pending_handoff,
                    )
                });
                match local {
                    Some((false, _)) => {
                        return Err(PathLockError::HandoffFailed(format!(
                            "handoff ref '{lease_ref}' does not match the local lease"
                        )));
                    }
                    Some((true, false)) => {
                        return Err(PathLockError::HandoffFailed(format!(
                            "pathlock lease '{lease_ref}' is not pending handoff (handoff() not called yet, or already adopted)"
                        )));
                    }
                    Some((true, true)) => {
                        let new_ownership_ref = Self::new_owner_id();
                        let new_lease_ref = Self::new_owner_id();
                        self.lease_registry
                            .rekey(lease_ref, new_lease_ref.clone(), &owner)?;
                        let owned = match owner_registry.take_pending_handoff(
                            lease_ref,
                            new_lease_ref.clone(),
                            new_ownership_ref,
                        ) {
                            Some(owned) => owned,
                            None => {
                                self.lease_registry.rekey(
                                    &new_lease_ref,
                                    lease_ref.to_string(),
                                    &owner,
                                )?;
                                return Err(PathLockError::Internal(format!(
                                    "pathlock lease '{lease_ref}' vanished during adopt"
                                )));
                            }
                        };
                        owner_registry
                            .consumed_handoff_refs
                            .insert(lease_ref.to_string());
                        let active_count = self.lease_registry.active_count();
                        drop(owner_registry);
                        debug!(lease_ref = %owned.lease.lease_ref, owner_id = %owned.lease.owner_id, lock_paths = ?owned.lease.lock_paths, active_count = active_count, "adopted pathlock lease (local fast path)");
                        return Ok(owned);
                    }
                    None => {}
                }
            }
        }

        let legacy_handoff = handoff.covered_paths.is_empty();
        let handle_mode = self.provider.handle_mode();
        if handle_mode == PathLockHandleMode::LogicalPath && legacy_handoff {
            return Err(PathLockError::HandoffFailed(
                "logical-path provider handoff requires covered_paths".to_string(),
            ));
        }
        if !legacy_handoff && handoff.covered_paths.len() != handoff.lock_paths.len() {
            return Err(PathLockError::InvalidRequest(
                "handoff lock_paths and covered_paths must have equal lengths".to_string(),
            ));
        }
        let mut expected_kinds = Vec::with_capacity(handoff.lock_paths.len());

        // ponytail: legacy durable handoffs only persisted owner_id/handle_id + lock_paths.
        // We validate live ownership/kind by the lock file path itself and keep covered_paths empty.
        // Upgrade path: once old queue payloads are drained, remove this branch and require covered_paths.
        for (index, lp) in handoff.lock_paths.iter().enumerate() {
            let expected_kind = match handle_mode {
                PathLockHandleMode::LogicalPath => {
                    let request = &handoff.covered_paths[index];
                    let expected_path = normalize_logical_path(&request.path)?;
                    if lp != expected_path {
                        return Err(PathLockError::InvalidRequest(format!(
                            "handoff coverage '{}' does not match logical lock path '{lp}'",
                            request.path
                        )));
                    }
                    request.kind
                }
                PathLockHandleMode::LockPath if legacy_handoff => {
                    let file_name = lp.rsplit('/').next().unwrap_or("");
                    if !(lp == &format!("/{}", PATH_LOCK_FILE)
                        || lp.ends_with(&format!("/{}", PATH_LOCK_FILE))
                        || file_name.starts_with(EXACT_LOCK_FILE_PREFIX))
                    {
                        return Err(PathLockError::InvalidRequest(format!(
                            "legacy handoff lock path '{lp}' is not a supported lock file"
                        )));
                    }
                    self.provider
                        .read_token(lp)
                        .await?
                        .ok_or_else(|| {
                            PathLockError::HandoffFailed(format!(
                                "lock path '{lp}' no longer exists"
                            ))
                        })?
                        .lock_type
                }
                PathLockHandleMode::LockPath => {
                    let request = &handoff.covered_paths[index];
                    let expected_paths = match request.kind {
                        PathLockKind::Exact => {
                            self.resolver
                                .resolve_exact_conflict_paths(&request.path)
                                .await?
                        }
                        PathLockKind::Tree => {
                            self.resolver
                                .resolve_tree_conflict_paths(&request.path)
                                .await?
                        }
                    };
                    if !expected_paths.contains(lp) {
                        return Err(PathLockError::InvalidRequest(format!(
                            "handoff coverage '{}' does not map to lock path '{lp}'",
                            request.path
                        )));
                    }
                    request.kind
                }
            };
            expected_kinds.push(expected_kind);
        }

        let owner = self
            .lease_registry
            .resolve_or_create_owner(&handoff.owner_id);
        let mut owner_registry = owner.registry.lock().await;
        owner_registry.invalidate_expired(Instant::now(), Self::lease_validity(&self.config));
        if handoff
            .lock_paths
            .iter()
            .any(|path| owner_registry.has_only_invalid_refs(path))
        {
            return Err(PathLockError::HandoffFailed(
                "handoff shares an invalidated token".to_string(),
            ));
        }
        let already_consumed = match handoff.lease_ref.as_deref() {
            Some(source_lease_ref) => owner_registry
                .consumed_handoff_refs
                .contains(source_lease_ref),
            None => owner_registry.entries.values().any(|entry| {
                handoff
                    .lock_paths
                    .iter()
                    .all(|lock_path| entry.lease.lock_paths.contains(lock_path))
            }),
        };
        if already_consumed {
            return Err(PathLockError::HandoffFailed(format!(
                "pathlock handoff for owner '{}' was already adopted",
                handoff.owner_id
            )));
        }
        let confirmed_at = Instant::now();
        let now_ns = Self::now_ns();
        for (lock_path, expected_kind) in handoff.lock_paths.iter().zip(&expected_kinds) {
            match self.provider.read_token(lock_path).await? {
                Some(token)
                    if token.owner_id == handoff.owner_id
                        && token.lock_type == *expected_kind
                        && now_ns.saturating_sub(token.time_ns)
                            < (self.lock_expire_ns() as f64 * LEASE_VALIDITY_RATIO) as u128 => {}
                _ => {
                    return Err(PathLockError::HandoffFailed(format!(
                        "lock path '{lock_path}' is no longer owned by '{}'",
                        handoff.owner_id
                    )));
                }
            }
            match self
                .provider
                .refresh_token(lock_path, &handoff.owner_id, now_ns)
                .await
            {
                Ok(true) => {}
                Ok(false) => {
                    return Err(PathLockError::HandoffFailed(format!(
                        "lock path '{lock_path}' changed while adopting owner '{}'",
                        handoff.owner_id
                    )));
                }
                Err(error) => return Err(error),
            }
            debug!(lock_path = %lock_path, owner_id = %handoff.owner_id, kind = ?expected_kind, "refreshed pathlock token during adopt");
        }

        owner_registry.invalidate_expired(Instant::now(), Self::lease_validity(&self.config));
        if confirmed_at.elapsed() >= Self::lease_validity(&self.config)
            || handoff
                .lock_paths
                .iter()
                .any(|path| owner_registry.has_only_invalid_refs(path))
        {
            return Err(PathLockError::HandoffFailed(
                "handoff expired during adoption".to_string(),
            ));
        }
        let lease = PathLockLease {
            lease_ref: Self::new_owner_id(),
            owner_id: handoff.owner_id.clone(),
            lock_paths: handoff.lock_paths.clone(),
            covered_paths: handoff.covered_paths.clone(),
        };
        let ownership_ref = Self::new_owner_id();
        owner_registry.insert(
            &owner.owner_id,
            lease.clone(),
            ownership_ref.clone(),
            &expected_kinds,
            confirmed_at,
        )?;
        if let Err(error) = self
            .lease_registry
            .publish(lease.lease_ref.clone(), owner.clone())
        {
            owner_registry.rollback_insert(&lease.lease_ref);
            return Err(error);
        }
        if let Some(source_lease_ref) = &handoff.lease_ref {
            owner_registry
                .consumed_handoff_refs
                .insert(source_lease_ref.clone());
        }
        drop(owner_registry);
        let active_count = self.lease_registry.active_count();
        debug!(lease_ref = %lease.lease_ref, owner_id = %lease.owner_id, lock_paths = ?lease.lock_paths, legacy_handoff = legacy_handoff, active_count = active_count, "adopted pathlock lease");

        Ok(OwnedPathLockLease {
            lease,
            ownership_ref,
        })
    }

    /// Check if a path is locked (for observability).
    pub async fn is_locked(&self, path: &str, ignore_stale: bool) -> PathLockResult<bool> {
        let now_ns = Self::now_ns();
        let normalized_path = normalize_logical_path(path)?;
        if let Some(locked) = self
            .provider
            .is_path_locked_atomic(
                normalized_path,
                now_ns,
                now_ns.saturating_sub(self.lock_expire_ns()),
                ignore_stale,
            )
            .await?
        {
            return Ok(locked);
        }

        // Check the current sidecar and the legacy in-directory token.
        let exact_paths = self
            .resolver
            .resolve_exact_conflict_paths(normalized_path)
            .await?;
        for lp in &exact_paths {
            if let Ok(Some(token)) = self.provider.read_token(lp).await {
                if !ignore_stale || !self.is_stale(&token, now_ns) {
                    return Ok(true);
                }
            }
        }

        // Check ancestor tree locks.
        for parent in LockPathResolver::ancestor_paths(normalized_path) {
            for ancestor_lock in self.resolver.resolve_tree_conflict_paths(&parent).await? {
                if let Ok(Some(token)) = self.provider.read_token(&ancestor_lock).await {
                    // Only Tree locks propagate to descendants.
                    if token.lock_type == PathLockKind::Tree
                        && (!ignore_stale || !self.is_stale(&token, now_ns))
                    {
                        return Ok(true);
                    }
                }
            }
        }

        Ok(false)
    }

    /// Return an observability snapshot.
    pub async fn observe(&self) -> PathLockObserveSnapshot {
        let metrics = self.metrics_snapshot().await;
        PathLockObserveSnapshot {
            active_locks: metrics.active_lock_count,
            waiting_locks: metrics.waiting_lock_count,
            stale_locks_removed: metrics.stale_tokens_removed,
            conflicts: metrics.recent_conflicts,
        }
    }

    /// Look up an owned lease by owner_id for cross-FFI lease operations.
    pub async fn get_owned_lease(&self, owner_id: &str) -> Option<OwnedPathLockLease> {
        let owner = self.lease_registry.resolve_owner(owner_id)?;
        let mut owner_registry = owner.registry.lock().await;
        owner_registry.invalidate_expired(Instant::now(), Self::lease_validity(&self.config));
        owner_registry
            .entries
            .values()
            .find(|entry| !entry.pending_handoff && entry.invalidation.is_none())
            .map(|entry| OwnedPathLockLease {
                lease: entry.lease.clone(),
                ownership_ref: entry.ownership_ref.clone(),
            })
    }

    /// Look up an owned lease by opaque lease_ref for cross-FFI lease operations.
    pub async fn get_owned_lease_by_ref(&self, lease_ref: &str) -> Option<OwnedPathLockLease> {
        let owner = self.lease_registry.resolve(lease_ref)?;
        let mut owner_registry = owner.registry.lock().await;
        owner_registry.invalidate_expired(Instant::now(), Self::lease_validity(&self.config));
        owner_registry
            .get_by_ref(lease_ref)
            .filter(|entry| !entry.pending_handoff && entry.invalidation.is_none())
            .map(|entry| OwnedPathLockLease {
                lease: entry.lease.clone(),
                ownership_ref: entry.ownership_ref.clone(),
            })
    }

    /// Look up an owned lease only when its lifecycle capability matches.
    pub async fn get_owned_lease_by_capability(
        &self,
        lease_ref: &str,
        ownership_ref: &str,
    ) -> Option<OwnedPathLockLease> {
        let owner = self.lease_registry.resolve(lease_ref)?;
        let mut owner_registry = owner.registry.lock().await;
        owner_registry.invalidate_expired(Instant::now(), Self::lease_validity(&self.config));
        owner_registry
            .get_by_ref(lease_ref)
            .filter(|entry| {
                !entry.pending_handoff
                    && entry.invalidation.is_none()
                    && entry.ownership_ref == ownership_ref
            })
            .map(|entry| OwnedPathLockLease {
                lease: entry.lease.clone(),
                ownership_ref: entry.ownership_ref.clone(),
            })
    }

    /// Resolve a release capability even after authorization has been revoked.
    pub async fn get_releasable_lease_by_capability(
        &self,
        lease_ref: &str,
        ownership_ref: &str,
    ) -> Option<OwnedPathLockLease> {
        let owner = self.lease_registry.resolve(lease_ref)?;
        let registry = owner.registry.lock().await;
        registry
            .get_by_ref(lease_ref)
            .filter(|entry| !entry.pending_handoff && entry.ownership_ref == ownership_ref)
            .map(|entry| OwnedPathLockLease {
                lease: entry.lease.clone(),
                ownership_ref: entry.ownership_ref.clone(),
            })
    }

    /// Validate that an opaque lease reference exists and covers all requested paths.
    pub async fn require_covered_lease_ref(
        &self,
        lease_ref: &str,
        requests: &[PathLockRequest],
    ) -> PathLockResult<()> {
        for request in requests {
            normalize_logical_path(&request.path)?;
        }
        let owner = self.lease_registry.resolve(lease_ref).ok_or_else(|| {
            PathLockError::InvalidRequest(format!("unknown pathlock lease ref '{lease_ref}'"))
        })?;
        let mut owner_registry = owner.registry.lock().await;
        owner_registry.invalidate_expired(Instant::now(), Self::lease_validity(&self.config));
        let entry = owner_registry
            .get_by_ref(lease_ref)
            .filter(|entry| !entry.pending_handoff && entry.invalidation.is_none())
            .ok_or_else(|| {
                PathLockError::InvalidRequest(format!("unknown pathlock lease ref '{lease_ref}'"))
            })?;
        if requests.iter().all(|request| entry.lease.covers(request)) {
            Ok(())
        } else {
            Err(PathLockError::InvalidRequest(format!(
                "pathlock lease ref '{lease_ref}' does not cover the requested operation"
            )))
        }
    }

    /// Resolve automatic PathLock behavior from the current immutable FS context.
    pub async fn resolve_auto_pathlock_action(
        &self,
        requests: &[PathLockRequest],
    ) -> PathLockResult<AutoPathLockAction> {
        let view = FsContextView::current();
        if view.disable_auto_pathlock() {
            debug!(requests = ?requests, "pathlock auto action resolved to disabled");
            return Ok(AutoPathLockAction::Disabled);
        }
        for request in requests {
            normalize_logical_path(&request.path)?;
        }
        let Some(lease_ref) = view.pathlock_lease_ref() else {
            debug!(requests = ?requests, "pathlock auto action resolved to acquire");
            return Ok(AutoPathLockAction::Acquire);
        };
        let lease = self
            .get_owned_lease_by_ref(lease_ref)
            .await
            .ok_or_else(|| {
                PathLockError::InvalidRequest(format!("unknown pathlock lease ref '{lease_ref}'"))
            })?;
        if requests.iter().all(|request| lease.lease.covers(request)) {
            debug!(lease_ref = %lease.lease.lease_ref, requests = ?requests, "pathlock auto action resolved to covered");
            Ok(AutoPathLockAction::Covered(lease))
        } else {
            Err(PathLockError::InvalidRequest(format!(
                "pathlock lease ref '{lease_ref}' does not cover the requested operation"
            )))
        }
    }

    /// Return a reference to the metrics for external reading.
    pub async fn metrics_snapshot(&self) -> LockMetrics {
        let mut metrics = self.metrics.read().await.clone();
        metrics.active_lock_count = self.lease_registry.active_count();
        metrics.waiting_lock_count = self.waiting_lock_count.load(AtomicOrdering::Relaxed);
        metrics.wait_duration_ns = self.wait_duration_ns.load(AtomicOrdering::Relaxed);
        metrics
    }
}

#[cfg(test)]
#[path = "lost_lease_tests.rs"]
mod lost_lease_tests;

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::{Error, FileInfo, Result as FsResult, WriteFlag};
    use crate::plugins::memfs::MemFileSystem;
    use async_trait::async_trait;
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
    use tokio::sync::Barrier;

    struct ConcurrentParentCreationFs {
        inner: Arc<MemFileSystem>,
        shared_parent: String,
        initial_missing_stats: AtomicUsize,
        initial_stat_barrier: Barrier,
        shared_parent_mkdir_attempts: AtomicUsize,
    }

    impl ConcurrentParentCreationFs {
        fn new(inner: Arc<MemFileSystem>, shared_parent: &str) -> Self {
            Self {
                inner,
                shared_parent: shared_parent.to_string(),
                initial_missing_stats: AtomicUsize::new(0),
                initial_stat_barrier: Barrier::new(2),
                shared_parent_mkdir_attempts: AtomicUsize::new(0),
            }
        }
    }

    #[async_trait]
    impl FileSystem for ConcurrentParentCreationFs {
        async fn create(&self, path: &str) -> FsResult<()> {
            self.inner.create(path).await
        }

        async fn mkdir(&self, path: &str, mode: u32) -> FsResult<()> {
            if path == self.shared_parent {
                self.shared_parent_mkdir_attempts
                    .fetch_add(1, Ordering::SeqCst);
            }
            self.inner.mkdir(path, mode).await
        }

        async fn remove(&self, path: &str) -> FsResult<()> {
            self.inner.remove(path).await
        }

        async fn remove_all(&self, path: &str) -> FsResult<()> {
            self.inner.remove_all(path).await
        }

        async fn read(&self, path: &str, offset: u64, size: u64) -> FsResult<Vec<u8>> {
            self.inner.read(path, offset, size).await
        }

        async fn write(
            &self,
            path: &str,
            data: &[u8],
            offset: u64,
            flags: WriteFlag,
        ) -> FsResult<u64> {
            self.inner.write(path, data, offset, flags).await
        }

        async fn read_dir(
            &self,
            path: &str,
            offset: Option<usize>,
            limit: Option<usize>,
            sort_by: Option<crate::core::ListSortBy>,
            sort_order: Option<crate::core::SortOrder>,
        ) -> FsResult<Vec<FileInfo>> {
            self.inner
                .read_dir(path, offset, limit, sort_by, sort_order)
                .await
        }

        async fn stat(&self, path: &str) -> FsResult<FileInfo> {
            if path == self.shared_parent
                && self.initial_missing_stats.fetch_add(1, Ordering::SeqCst) < 2
            {
                self.initial_stat_barrier.wait().await;
                return Err(Error::not_found(path));
            }
            self.inner.stat(path).await
        }

        async fn rename(&self, old_path: &str, new_path: &str) -> FsResult<()> {
            self.inner.rename(old_path, new_path).await
        }

        async fn chmod(&self, path: &str, mode: u32) -> FsResult<()> {
            self.inner.chmod(path, mode).await
        }
    }

    struct FailNextRemoveProvider {
        inner: crate::lock::provider::MemoryPathLockProvider,
        fail_next_read: AtomicBool,
        fail_next_remove: AtomicBool,
        return_false_next_remove: AtomicBool,
        busy_next_takeover: AtomicBool,
        busy_takeover_count: AtomicUsize,
        reject_compare: bool,
        refresh_on_takeover: bool,
    }

    impl FailNextRemoveProvider {
        /// Build a memory provider whose next token read fails.
        fn with_read_failure() -> Self {
            Self {
                inner: crate::lock::provider::MemoryPathLockProvider::new(),
                fail_next_read: AtomicBool::new(true),
                fail_next_remove: AtomicBool::new(false),
                return_false_next_remove: AtomicBool::new(false),
                busy_next_takeover: AtomicBool::new(false),
                busy_takeover_count: AtomicUsize::new(0),
                reject_compare: false,
                refresh_on_takeover: false,
            }
        }

        /// Build a memory provider whose next takeover reports a retryable busy error.
        fn with_busy_takeover() -> Self {
            Self {
                inner: crate::lock::provider::MemoryPathLockProvider::new(),
                fail_next_read: AtomicBool::new(false),
                fail_next_remove: AtomicBool::new(false),
                return_false_next_remove: AtomicBool::new(false),
                busy_next_takeover: AtomicBool::new(true),
                busy_takeover_count: AtomicUsize::new(0),
                reject_compare: false,
                refresh_on_takeover: false,
            }
        }

        /// Build a memory provider that always reports compare-and-write misses.
        fn with_compare_miss() -> Self {
            Self {
                inner: crate::lock::provider::MemoryPathLockProvider::new(),
                fail_next_read: AtomicBool::new(false),
                fail_next_remove: AtomicBool::new(false),
                return_false_next_remove: AtomicBool::new(false),
                busy_next_takeover: AtomicBool::new(false),
                busy_takeover_count: AtomicUsize::new(0),
                reject_compare: true,
                refresh_on_takeover: false,
            }
        }
    }

    #[async_trait]
    impl PathLockProvider for FailNextRemoveProvider {
        fn name(&self) -> &'static str {
            "fail-next-remove"
        }

        async fn read_token(&self, lock_path: &str) -> PathLockResult<Option<LockToken>> {
            if self.fail_next_read.swap(false, Ordering::SeqCst) {
                return Err(PathLockError::Io("injected read failure".to_string()));
            }
            self.inner.read_token(lock_path).await
        }

        async fn try_create_token(&self, lock_path: &str, token: &LockToken) -> PathLockResult<()> {
            self.inner.try_create_token(lock_path, token).await
        }

        async fn compare_and_write_token(
            &self,
            lock_path: &str,
            expected: &LockToken,
            replacement: &LockToken,
        ) -> PathLockResult<bool> {
            if self.busy_next_takeover.swap(false, Ordering::SeqCst) {
                self.busy_takeover_count.fetch_add(1, Ordering::SeqCst);
                return Err(PathLockError::Busy {
                    lock_path: lock_path.to_string(),
                    operation: "compare_and_write".to_string(),
                });
            }
            if self.reject_compare {
                return Ok(false);
            }
            if self.refresh_on_takeover {
                self.inner
                    .refresh_token(lock_path, &expected.owner_id, PathLockManager::now_ns())
                    .await?;
            }
            self.inner
                .compare_and_write_token(lock_path, expected, replacement)
                .await
        }

        async fn refresh_token(
            &self,
            lock_path: &str,
            owner_id: &str,
            time_ns: u128,
        ) -> PathLockResult<bool> {
            self.inner.refresh_token(lock_path, owner_id, time_ns).await
        }

        async fn remove_token(
            &self,
            lock_path: &str,
            owner_id: &str,
            force: bool,
        ) -> PathLockResult<bool> {
            if self.return_false_next_remove.swap(false, Ordering::SeqCst) {
                return Ok(false);
            }
            if self.fail_next_remove.swap(false, Ordering::SeqCst) {
                return Err(PathLockError::Io("injected remove failure".to_string()));
            }
            if self.refresh_on_takeover {
                self.inner
                    .refresh_token(lock_path, owner_id, PathLockManager::now_ns())
                    .await?;
            }
            self.inner.remove_token(lock_path, owner_id, force).await
        }

        async fn scan_descendant_locks(&self, root: &str) -> PathLockResult<Vec<String>> {
            self.inner.scan_descendant_locks(root).await
        }
    }

    /// Alternate conflicts before acquisition and during post-verification.
    ///
    /// Every call to `try_create_token` is one real acquire-loop probe. Odd
    /// probes fail there with Busy; even probes create a token, then the next
    /// read exposes a synthetic ancestor Tree token so post-verification fails.
    struct RetryLoopCountingProvider {
        inner: crate::lock::provider::MemoryPathLockProvider,
        create_probes: AtomicUsize,
        pre_acquire_conflicts: AtomicUsize,
        post_verify_conflicts: AtomicUsize,
        inject_post_verify_conflict: AtomicBool,
    }

    impl RetryLoopCountingProvider {
        fn new() -> Self {
            Self {
                inner: crate::lock::provider::MemoryPathLockProvider::new(),
                create_probes: AtomicUsize::new(0),
                pre_acquire_conflicts: AtomicUsize::new(0),
                post_verify_conflicts: AtomicUsize::new(0),
                inject_post_verify_conflict: AtomicBool::new(false),
            }
        }
    }

    #[async_trait]
    impl PathLockProvider for RetryLoopCountingProvider {
        fn name(&self) -> &'static str {
            "retry-loop-counting"
        }

        async fn read_token(&self, lock_path: &str) -> PathLockResult<Option<LockToken>> {
            if self
                .inject_post_verify_conflict
                .swap(false, Ordering::SeqCst)
            {
                self.post_verify_conflicts.fetch_add(1, Ordering::SeqCst);
                return Ok(Some(LockToken {
                    owner_id: "post-verify-blocker".to_string(),
                    time_ns: PathLockManager::now_ns(),
                    lock_type: PathLockKind::Tree,
                }));
            }
            self.inner.read_token(lock_path).await
        }

        async fn try_create_token(&self, lock_path: &str, token: &LockToken) -> PathLockResult<()> {
            let probe = self.create_probes.fetch_add(1, Ordering::SeqCst);
            if probe.is_multiple_of(2) {
                self.pre_acquire_conflicts.fetch_add(1, Ordering::SeqCst);
                return Err(PathLockError::Busy {
                    lock_path: lock_path.to_string(),
                    operation: "injected pre-acquire conflict".to_string(),
                });
            }

            self.inner.try_create_token(lock_path, token).await?;
            self.inject_post_verify_conflict
                .store(true, Ordering::SeqCst);
            Ok(())
        }

        async fn compare_and_write_token(
            &self,
            lock_path: &str,
            expected: &LockToken,
            replacement: &LockToken,
        ) -> PathLockResult<bool> {
            self.inner
                .compare_and_write_token(lock_path, expected, replacement)
                .await
        }

        async fn refresh_token(
            &self,
            lock_path: &str,
            owner_id: &str,
            time_ns: u128,
        ) -> PathLockResult<bool> {
            self.inner.refresh_token(lock_path, owner_id, time_ns).await
        }

        async fn remove_token(
            &self,
            lock_path: &str,
            owner_id: &str,
            force: bool,
        ) -> PathLockResult<bool> {
            self.inner.remove_token(lock_path, owner_id, force).await
        }

        async fn scan_descendant_locks(&self, root: &str) -> PathLockResult<Vec<String>> {
            self.inner.scan_descendant_locks(root).await
        }
    }

    /// Build a manager backed by the real in-memory filesystem.
    async fn make_manager() -> PathLockManager {
        make_manager_with_config(PathLockConfig::default()).await
    }

    /// Build a manager with custom lock configuration.
    async fn make_manager_with_config(config: PathLockConfig) -> PathLockManager {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        fs.mkdir("/data/sub", 0o755).await.unwrap();
        let provider = Arc::new(crate::lock::provider::MemoryPathLockProvider::new());
        PathLockManager::new(fs, provider, config)
    }

    /// Build a manager and expose its real in-memory filesystem.
    async fn make_manager_with_fs() -> (PathLockManager, Arc<MemFileSystem>) {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        let provider = Arc::new(crate::lock::provider::MemoryPathLockProvider::new());
        (
            PathLockManager::new(fs.clone(), provider, PathLockConfig::default()),
            fs,
        )
    }

    async fn make_filesystem_managers() -> (PathLockManager, PathLockManager) {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        fs.mkdir("/data/a", 0o755).await.unwrap();
        fs.mkdir("/data/a/b", 0o755).await.unwrap();
        fs.mkdir("/data/a/c", 0o755).await.unwrap();
        let config = PathLockConfig::default();
        let first_provider = Arc::new(crate::lock::provider::FilesystemPathLockProvider::new(
            fs.clone(),
            config.lock_expire_secs,
        ));
        let second_provider = Arc::new(crate::lock::provider::FilesystemPathLockProvider::new(
            fs.clone(),
            config.lock_expire_secs,
        ));
        (
            PathLockManager::new(fs.clone(), first_provider, config.clone()),
            PathLockManager::new(fs, second_provider, config),
        )
    }

    async fn acquire_kind(
        manager: &PathLockManager,
        path: &str,
        kind: PathLockKind,
    ) -> PathLockResult<OwnedPathLockLease> {
        match kind {
            PathLockKind::Exact => manager.acquire_exact(path, Duration::ZERO, None).await,
            PathLockKind::Tree => manager.acquire_tree(path, Duration::ZERO, None).await,
        }
    }

    #[tokio::test]
    async fn exact_locks_tolerate_concurrent_parent_directory_creation() {
        let inner = Arc::new(MemFileSystem::new());
        inner.mkdir("/local", 0o755).await.unwrap();
        inner.mkdir("/local/default", 0o755).await.unwrap();
        inner
            .mkdir("/local/default/resources", 0o755)
            .await
            .unwrap();
        let shared_parent = "/local/default/resources/shared";
        let fs = Arc::new(ConcurrentParentCreationFs::new(
            inner.clone(),
            shared_parent,
        ));
        let provider = Arc::new(crate::lock::provider::MemoryPathLockProvider::new());
        let first_manager =
            PathLockManager::new(fs.clone(), provider.clone(), PathLockConfig::default());
        let second_manager = PathLockManager::new(fs.clone(), provider, PathLockConfig::default());
        let first_path = format!("{shared_parent}/a.md");
        let second_path = format!("{shared_parent}/b.md");

        let (first, second) = tokio::join!(
            first_manager.acquire_exact(&first_path, Duration::ZERO, None),
            second_manager.acquire_exact(&second_path, Duration::ZERO, None),
        );

        assert!(first.is_ok(), "first lock failed: {first:?}");
        assert!(second.is_ok(), "second lock failed: {second:?}");
        assert_eq!(fs.shared_parent_mkdir_attempts.load(Ordering::SeqCst), 2);
        assert!(inner.stat(shared_parent).await.unwrap().is_dir);
    }

    #[tokio::test]
    async fn release_tree_lease_succeeds_after_locked_directory_is_deleted() {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        fs.mkdir("/data/delete-me", 0o755).await.unwrap();
        let provider = Arc::new(crate::lock::provider::FilesystemPathLockProvider::new(
            fs.clone(),
            PathLockConfig::default().lock_expire_secs,
        ));
        let mgr = PathLockManager::new(fs.clone(), provider, PathLockConfig::default());
        let lease = mgr
            .acquire_tree("/data/delete-me", Duration::ZERO, None)
            .await
            .unwrap();
        assert!(lease.lease.lock_paths[0].starts_with("/data/.exact.ovlock.delete-me."));

        fs.remove_all("/data/delete-me").await.unwrap();

        assert!(
            mgr.acquire_tree("/data/delete-me", Duration::ZERO, None)
                .await
                .is_err(),
            "the sidecar must keep the deleted path locked until release"
        );
        mgr.release(&lease).await.unwrap();
        let next = mgr
            .acquire_tree("/data/delete-me", Duration::ZERO, None)
            .await
            .unwrap();
        mgr.release(&next).await.unwrap();
        assert!(mgr
            .get_owned_lease_by_ref(&lease.lease.lease_ref)
            .await
            .is_none());
        assert_eq!(mgr.metrics_snapshot().await.active_lock_count, 0);
    }

    #[tokio::test]
    async fn acquire_exact_reentrant_requires_local_owned_capability() {
        let mgr = make_manager().await;
        let lease1 = mgr
            .acquire_exact("/data/file.txt", Duration::from_secs(1), None)
            .await
            .unwrap();
        let capability = (
            lease1.lease.lease_ref.as_str(),
            lease1.ownership_ref.as_str(),
        );
        let lease2 = mgr
            .acquire_exact("/data/file.txt", Duration::from_secs(1), Some(capability))
            .await
            .unwrap();
        assert_eq!(lease1.lease.lock_paths, lease2.lease.lock_paths);

        mgr.release(&lease2).await.unwrap();
        assert!(matches!(
            mgr.acquire_exact("/data/file.txt", Duration::ZERO, None)
                .await,
            Err(PathLockError::Timeout { .. })
        ));

        mgr.release(&lease1).await.unwrap();
        assert!(mgr
            .acquire_exact("/data/file.txt", Duration::ZERO, None)
            .await
            .is_ok());
    }

    /// Keep coverage for paths that remain after a partial lease release.
    #[tokio::test]
    async fn release_selected_preserves_remaining_coverage() {
        let mgr = make_manager().await;
        let lease = mgr
            .acquire_exact_batch(
                &["/data/a.txt".to_string(), "/data/b.txt".to_string()],
                Duration::ZERO,
                None,
            )
            .await
            .unwrap();
        let released_lock_path = lease.lease.lock_paths[0].clone();

        mgr.release_selected(&lease, &[released_lock_path])
            .await
            .unwrap();

        assert!(mgr
            .require_covered_lease_ref(
                &lease.lease.lease_ref,
                &[PathLockRequest {
                    path: "/data/b.txt".to_string(),
                    kind: PathLockKind::Exact,
                }],
            )
            .await
            .is_ok());
        mgr.release(&lease).await.unwrap();
    }

    #[tokio::test]
    async fn failed_batch_rolls_back_same_owner_tree_upgrade() {
        let mgr = make_manager().await;
        let exact = mgr
            .acquire_exact("/data/sub", Duration::ZERO, None)
            .await
            .unwrap();
        let blocker = mgr
            .acquire_exact("/data/z.txt", Duration::ZERO, None)
            .await
            .unwrap();
        let capability = (exact.lease.lease_ref.as_str(), exact.ownership_ref.as_str());

        let result = mgr
            .acquire_batch(
                &[
                    PathLockRequest {
                        path: "/data/sub".to_string(),
                        kind: PathLockKind::Tree,
                    },
                    PathLockRequest {
                        path: "/data/z.txt".to_string(),
                        kind: PathLockKind::Exact,
                    },
                ],
                Duration::ZERO,
                Some(capability),
            )
            .await;

        assert!(matches!(result, Err(PathLockError::Timeout { .. })));
        let token = mgr
            .provider
            .read_token(&exact.lease.lock_paths[0])
            .await
            .unwrap()
            .unwrap();
        assert_eq!(token.lock_type, PathLockKind::Exact);

        mgr.release(&blocker).await.unwrap();
        mgr.release(&exact).await.unwrap();
    }

    #[tokio::test]
    async fn missing_exact_and_same_path_tree_conflict_in_both_orders() {
        let (mgr, _) = make_manager_with_fs().await;
        let exact = mgr
            .acquire_exact("/data/new", Duration::ZERO, None)
            .await
            .unwrap();
        assert!(matches!(
            mgr.acquire_tree("/data/new", Duration::ZERO, None).await,
            Err(PathLockError::Timeout { .. })
        ));
        mgr.release(&exact).await.unwrap();

        let tree = mgr
            .acquire_tree("/data/other", Duration::ZERO, None)
            .await
            .unwrap();
        assert!(matches!(
            mgr.acquire_exact("/data/other", Duration::ZERO, None).await,
            Err(PathLockError::Timeout { .. })
        ));
        mgr.release(&tree).await.unwrap();
    }

    #[tokio::test]
    async fn filesystem_lock_relationship_matrix() {
        struct Case {
            name: &'static str,
            held_path: &'static str,
            held_kind: PathLockKind,
            requested_path: &'static str,
            requested_kind: PathLockKind,
            conflicts: bool,
        }

        let cases = [
            Case {
                name: "ancestor tree blocks descendant exact",
                held_path: "/data/a",
                held_kind: PathLockKind::Tree,
                requested_path: "/data/a/b",
                requested_kind: PathLockKind::Exact,
                conflicts: true,
            },
            Case {
                name: "descendant exact blocks ancestor tree",
                held_path: "/data/a/b",
                held_kind: PathLockKind::Exact,
                requested_path: "/data/a",
                requested_kind: PathLockKind::Tree,
                conflicts: true,
            },
            Case {
                name: "ancestor tree blocks descendant tree",
                held_path: "/data/a",
                held_kind: PathLockKind::Tree,
                requested_path: "/data/a/b",
                requested_kind: PathLockKind::Tree,
                conflicts: true,
            },
            Case {
                name: "descendant tree blocks ancestor tree",
                held_path: "/data/a/b",
                held_kind: PathLockKind::Tree,
                requested_path: "/data/a",
                requested_kind: PathLockKind::Tree,
                conflicts: true,
            },
            Case {
                name: "ancestor exact allows descendant exact",
                held_path: "/data/a",
                held_kind: PathLockKind::Exact,
                requested_path: "/data/a/b",
                requested_kind: PathLockKind::Exact,
                conflicts: false,
            },
            Case {
                name: "descendant exact allows ancestor exact",
                held_path: "/data/a/b",
                held_kind: PathLockKind::Exact,
                requested_path: "/data/a",
                requested_kind: PathLockKind::Exact,
                conflicts: false,
            },
            Case {
                name: "sibling tree allows sibling exact",
                held_path: "/data/a/b",
                held_kind: PathLockKind::Tree,
                requested_path: "/data/a/c",
                requested_kind: PathLockKind::Exact,
                conflicts: false,
            },
            Case {
                name: "sibling tree allows sibling tree",
                held_path: "/data/a/b",
                held_kind: PathLockKind::Tree,
                requested_path: "/data/a/c",
                requested_kind: PathLockKind::Tree,
                conflicts: false,
            },
        ];

        for case in cases {
            let (holder, contender) = make_filesystem_managers().await;
            let held = acquire_kind(&holder, case.held_path, case.held_kind)
                .await
                .unwrap_or_else(|error| {
                    panic!("{}: failed to acquire held lock: {error}", case.name)
                });
            let requested =
                acquire_kind(&contender, case.requested_path, case.requested_kind).await;

            if case.conflicts {
                assert!(
                    matches!(requested, Err(PathLockError::Timeout { .. })),
                    "{}: expected conflict, got {requested:?}",
                    case.name
                );
            } else {
                let requested = requested
                    .unwrap_or_else(|error| panic!("{}: unexpected conflict: {error}", case.name));
                contender.release(&requested).await.unwrap();
            }
            holder.release(&held).await.unwrap();
        }
    }

    #[tokio::test]
    async fn legacy_tree_token_still_blocks_descendant_acquisition() {
        let mgr = make_manager().await;
        mgr.provider
            .try_create_token(
                "/data/sub/.path.ovlock",
                &LockToken {
                    owner_id: "legacy-owner".to_string(),
                    time_ns: PathLockManager::now_ns(),
                    lock_type: PathLockKind::Tree,
                },
            )
            .await
            .unwrap();

        assert!(matches!(
            mgr.acquire_exact("/data/sub/file.txt", Duration::ZERO, None)
                .await,
            Err(PathLockError::Timeout { .. })
        ));
    }

    #[tokio::test]
    async fn handoff_and_adopt() {
        let mgr = make_manager().await;
        let lease = mgr
            .acquire_tree("/data/sub", Duration::from_secs(1), None)
            .await
            .unwrap();
        let handoff = mgr.to_handoff(&lease);
        assert_eq!(
            handoff.lease_ref.as_deref(),
            Some(lease.lease.lease_ref.as_str())
        );
        mgr.handoff(&lease).await.unwrap();

        let adopted = mgr.adopt(&handoff).await.unwrap();
        assert_eq!(adopted.lease.owner_id, lease.lease.owner_id);
        assert_eq!(adopted.lease.lock_paths, handoff.lock_paths);
        // adopt migrates to a fresh lease_ref and ownership_ref; the producer's
        // stale refs no longer resolve.
        assert_ne!(adopted.lease.lease_ref, lease.lease.lease_ref);
        assert_ne!(adopted.ownership_ref, lease.ownership_ref);
        assert!(mgr
            .get_owned_lease_by_ref(&lease.lease.lease_ref)
            .await
            .is_none());
    }

    #[tokio::test]
    async fn pending_handoff_rejects_stale_capability_but_keeps_refreshing() {
        let mgr = make_manager_with_config(PathLockConfig {
            lock_expire_secs: 0.03,
            ..PathLockConfig::default()
        })
        .await;
        let lease = mgr
            .acquire_tree("/data/sub", Duration::from_secs(1), None)
            .await
            .unwrap();
        let lock_path = lease.lease.lock_paths[0].clone();
        mgr.handoff(&lease).await.unwrap();

        // Producer's stale capability can no longer resolve the parked lease.
        // (FFI lifecycle ops all go through capability resolution.)
        assert!(mgr
            .get_owned_lease_by_capability(&lease.lease.lease_ref, &lease.ownership_ref)
            .await
            .is_none());
        assert!(mgr
            .get_owned_lease_by_ref(&lease.lease.lease_ref)
            .await
            .is_none());

        // But the entry stays in the registry and keeps refreshing.
        let before = mgr
            .provider
            .read_token(&lock_path)
            .await
            .unwrap()
            .unwrap()
            .time_ns;
        tokio::time::sleep(Duration::from_millis(60)).await;
        let after = mgr
            .provider
            .read_token(&lock_path)
            .await
            .unwrap()
            .unwrap()
            .time_ns;
        assert!(after > before, "parked lease token must keep refreshing");

        let handoff = mgr.to_handoff(&lease);
        let adopted = mgr.adopt(&handoff).await.unwrap();
        assert_eq!(adopted.lease.owner_id, lease.lease.owner_id);
        // After adopt the new owner can operate the lease again.
        assert!(mgr.release(&adopted).await.is_ok());
    }

    #[tokio::test]
    async fn pending_handoff_is_forgotten_after_cross_manager_release() {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        fs.mkdir("/data/sub", 0o755).await.unwrap();
        let provider = Arc::new(crate::lock::provider::MemoryPathLockProvider::new());
        let config = PathLockConfig {
            lock_expire_secs: 0.3,
            ..PathLockConfig::default()
        };
        let producer = PathLockManager::new(fs.clone(), provider.clone(), config.clone());
        let consumer = PathLockManager::new(fs, provider.clone(), config);
        let lease = producer
            .acquire_tree("/data/sub", Duration::ZERO, None)
            .await
            .unwrap();
        let lock_path = lease.lease.lock_paths[0].clone();
        let handoff = producer.to_handoff(&lease);
        producer.handoff(&lease).await.unwrap();

        let adopted = consumer.adopt(&handoff).await.unwrap();
        consumer.release(&adopted).await.unwrap();

        // The producer sees the consumer's release on its next renewal and
        // drops only its local pending-handoff registration. The old stale-GC
        // path would retain this entry for two lock-expiry periods.
        tokio::time::timeout(Duration::from_millis(400), async {
            loop {
                if producer.observe().await.active_locks == 0 {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("producer must forget a released cross-manager handoff");
        assert_eq!(provider.read_token(&lock_path).await.unwrap(), None);
    }

    #[tokio::test]
    async fn pending_handoff_token_loss_invalidates_the_entire_lease() {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        let provider = Arc::new(crate::lock::provider::MemoryPathLockProvider::new());
        let config = PathLockConfig {
            lock_expire_secs: 0.3,
            ..PathLockConfig::default()
        };
        let producer = PathLockManager::new(fs.clone(), provider.clone(), config.clone());
        let consumer = PathLockManager::new(fs, provider.clone(), config);
        let lease = producer
            .acquire_exact_batch(
                &["/data/a".to_string(), "/data/b".to_string()],
                Duration::ZERO,
                None,
            )
            .await
            .unwrap();
        let first_lock_path = lease.lease.lock_paths[0].clone();
        let second_lock_path = lease.lease.lock_paths[1].clone();
        let handoff = producer.to_handoff(&lease);
        producer.handoff(&lease).await.unwrap();

        let adopted = consumer.adopt(&handoff).await.unwrap();
        consumer
            .release_selected(&adopted, &[first_lock_path.clone()])
            .await
            .unwrap();

        // Losing one required token invalidates the entire parked lease.
        tokio::time::sleep(Duration::from_millis(700)).await;
        assert_eq!(producer.observe().await.active_locks, 0);
        assert_eq!(provider.read_token(&first_lock_path).await.unwrap(), None);
        assert!(provider
            .read_token(&second_lock_path)
            .await
            .unwrap()
            .is_none());

        assert!(consumer
            .get_owned_lease_by_ref(&adopted.lease.lease_ref)
            .await
            .is_none());
        tokio::time::timeout(Duration::from_millis(400), async {
            loop {
                if producer.observe().await.active_locks == 0 {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
        })
        .await
        .expect("producer must forget every released handoff path");
    }

    #[tokio::test]
    async fn adopt_before_handoff_is_retryable() {
        let mgr = make_manager().await;
        let lease = mgr
            .acquire_tree("/data/sub", Duration::from_secs(1), None)
            .await
            .unwrap();
        let handoff = mgr.to_handoff(&lease);

        // Consumer races ahead of the producer's handoff(): entry is still Owned.
        let err = mgr.adopt(&handoff).await.unwrap_err();
        assert!(matches!(err, PathLockError::HandoffFailed(_)));

        // Once the producer parks it, adopt succeeds with a fresh lease_ref.
        mgr.handoff(&lease).await.unwrap();
        let adopted = mgr.adopt(&handoff).await.unwrap();
        assert_ne!(adopted.lease.lease_ref, lease.lease.lease_ref);
    }

    #[tokio::test]
    async fn double_handoff_is_rejected() {
        let mgr = make_manager().await;
        let lease = mgr
            .acquire_tree("/data/sub", Duration::from_secs(1), None)
            .await
            .unwrap();
        mgr.handoff(&lease).await.unwrap();
        assert!(mgr.handoff(&lease).await.is_err());
    }

    #[tokio::test]
    async fn adopt_fast_path_rejects_forged_covered_paths() {
        let mgr = make_manager().await;
        let lease = mgr
            .acquire_tree("/data/sub", Duration::from_secs(1), None)
            .await
            .unwrap();
        let mut handoff = mgr.to_handoff(&lease);
        // Tamper the coverage while keeping owner_id/lock_paths intact.
        handoff.covered_paths = vec![PathLockRequest {
            path: "/data/other".to_string(),
            kind: PathLockKind::Tree,
        }];
        mgr.handoff(&lease).await.unwrap();
        assert!(matches!(
            mgr.adopt(&handoff).await,
            Err(PathLockError::HandoffFailed(_))
        ));
    }

    #[tokio::test]
    async fn legacy_handoff_without_lease_ref_uses_token_fallback() {
        // A legacy payload (no lease_ref) only occurs across a restart: the token is
        // still on disk but the original registry entry is gone. Model that with a
        // second manager sharing the same fs + provider (an empty registry).
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        fs.mkdir("/data/sub", 0o755).await.unwrap();
        let provider = Arc::new(crate::lock::provider::MemoryPathLockProvider::new());
        let producer =
            PathLockManager::new(fs.clone(), provider.clone(), PathLockConfig::default());
        let lease = producer
            .acquire_tree("/data/sub", Duration::from_secs(1), None)
            .await
            .unwrap();
        // Historic payload: no lease_ref, no covered_paths.
        let legacy = PathLockHandoffRef {
            lease_ref: None,
            owner_id: lease.lease.owner_id.clone(),
            lock_paths: lease.lease.lock_paths.clone(),
            covered_paths: Vec::new(),
        };
        // "Restarted" process: fresh manager, empty registry, token persisted on disk.
        let consumer = PathLockManager::new(fs, provider, PathLockConfig::default());
        let adopted = consumer.adopt(&legacy).await.unwrap();
        assert_eq!(adopted.lease.owner_id, lease.lease.owner_id);
        let capability = (&*adopted.lease.lease_ref, &*adopted.ownership_ref);
        let exact = consumer
            .acquire_exact("/data/sub", Duration::ZERO, Some(capability))
            .await
            .unwrap();
        consumer.release(&adopted).await.unwrap();
        let token = consumer
            .provider
            .read_token(&legacy.lock_paths[0])
            .await
            .unwrap()
            .unwrap();
        assert_eq!(token.lock_type, PathLockKind::Exact);
        consumer.release(&exact).await.unwrap();
    }

    #[tokio::test]
    async fn adopt_accepts_legacy_tree_path_with_covered_paths() {
        let mgr = make_manager().await;
        let owner_id = "legacy-covered-owner".to_string();
        let lock_path = "/data/sub/.path.ovlock".to_string();
        mgr.provider
            .try_create_token(
                &lock_path,
                &LockToken {
                    owner_id: owner_id.clone(),
                    time_ns: PathLockManager::now_ns(),
                    lock_type: PathLockKind::Tree,
                },
            )
            .await
            .unwrap();
        let handoff = PathLockHandoffRef {
            lease_ref: Some("legacy-covered-lease".to_string()),
            owner_id,
            lock_paths: vec![lock_path],
            covered_paths: vec![PathLockRequest {
                path: "/data/sub".to_string(),
                kind: PathLockKind::Tree,
            }],
        };

        let adopted = mgr.adopt(&handoff).await.unwrap();
        mgr.release(&adopted).await.unwrap();
    }

    #[tokio::test]
    async fn adopt_distinguishes_reentrant_leases_from_replay() {
        let (producer, fs) = make_manager_with_fs().await;
        let lease1 = producer
            .acquire_exact("/data/file.txt", Duration::from_secs(1), None)
            .await
            .unwrap();
        let capability = (&*lease1.lease.lease_ref, &*lease1.ownership_ref);
        let lease2 = producer
            .acquire_exact("/data/file.txt", Duration::from_secs(1), Some(capability))
            .await
            .unwrap();
        let handoff1 = producer.to_handoff(&lease1);
        producer.handoff(&lease1).await.unwrap();
        producer.handoff(&lease2).await.unwrap();
        let consumer =
            PathLockManager::new(fs, producer.provider.clone(), PathLockConfig::default());
        let adopted1 = consumer.adopt(&handoff1).await.unwrap();
        consumer.adopt(&producer.to_handoff(&lease2)).await.unwrap();
        consumer.handoff(&adopted1).await.unwrap();
        consumer
            .adopt(&consumer.to_handoff(&adopted1))
            .await
            .unwrap();
        assert!(matches!(
            consumer.adopt(&handoff1).await,
            Err(PathLockError::HandoffFailed(_))
        ));
    }

    #[tokio::test]
    async fn opaque_lease_ref_must_cover_requested_path() {
        let mgr = make_manager().await;
        let lease = mgr
            .acquire_tree("/data", Duration::from_secs(1), None)
            .await
            .unwrap();

        assert_ne!(lease.lease.lease_ref, lease.lease.owner_id);
        assert!(mgr
            .require_covered_lease_ref(
                &lease.lease.owner_id,
                &[PathLockRequest {
                    path: "/data/sub/file.txt".to_string(),
                    kind: PathLockKind::Exact,
                }],
            )
            .await
            .is_err());
        assert!(mgr
            .require_covered_lease_ref(
                &lease.lease.lease_ref,
                &[PathLockRequest {
                    path: "/data/sub/file.txt".to_string(),
                    kind: PathLockKind::Exact,
                }],
            )
            .await
            .is_ok());
        assert!(mgr
            .require_covered_lease_ref(
                &lease.lease.lease_ref,
                &[PathLockRequest {
                    path: "/other/file.txt".to_string(),
                    kind: PathLockKind::Exact,
                }],
            )
            .await
            .is_err());
    }

    #[tokio::test]
    async fn is_locked_detects_root_tree_lock() {
        let mgr = make_manager().await;
        assert!(!mgr.is_locked("/data/file.txt", true).await.unwrap());
        let _root = mgr.acquire_tree("/", Duration::ZERO, None).await.unwrap();
        assert!(mgr.is_locked("/data/file.txt", true).await.unwrap());
    }

    #[tokio::test]
    async fn provider_io_error_is_not_retried() {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        let provider = Arc::new(FailNextRemoveProvider::with_read_failure());
        let mgr = PathLockManager::new(fs, provider, PathLockConfig::default());

        assert!(matches!(
            mgr.acquire_exact("/data/file.txt", Duration::ZERO, None)
                .await,
            Err(PathLockError::Io(message)) if message == "injected read failure"
        ));
    }

    #[tokio::test]
    async fn busy_stale_takeover_is_retried() {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        let provider = Arc::new(FailNextRemoveProvider::with_busy_takeover());
        let mgr = PathLockManager::new(
            fs,
            provider.clone(),
            PathLockConfig {
                lock_expire_secs: 1.0,
                ..PathLockConfig::default()
            },
        );
        let lock_path = mgr
            .resolver
            .resolve_tree_lock_path("/data/file.txt")
            .await
            .unwrap();
        provider
            .inner
            .try_create_token(
                &lock_path,
                &LockToken {
                    owner_id: "stale-owner".to_string(),
                    time_ns: 1,
                    lock_type: PathLockKind::Tree,
                },
            )
            .await
            .unwrap();

        let lease = mgr
            .acquire_tree("/data/file.txt", Duration::from_millis(200), None)
            .await
            .unwrap();

        assert_eq!(provider.busy_takeover_count.load(Ordering::SeqCst), 1);
        mgr.release(&lease).await.unwrap();
        let error = PathLockManager::rollback_error(
            PathLockError::Io("commit".into()),
            PathLockError::Io("rollback".into()),
        );
        assert!(!PathLockManager::is_retryable_error(&error));
    }

    #[tokio::test]
    async fn stale_takeover_preserves_a_concurrent_refresh() {
        for (refresh, timeout) in [
            (false, Duration::ZERO),
            (true, Duration::ZERO),
            (true, Duration::from_millis(200)),
        ] {
            let fs = Arc::new(MemFileSystem::new());
            fs.mkdir("/data", 0o755).await.unwrap();
            let provider = Arc::new(FailNextRemoveProvider {
                reject_compare: false,
                refresh_on_takeover: refresh,
                ..FailNextRemoveProvider::with_compare_miss()
            });
            let mgr = PathLockManager::new(fs, provider.clone(), PathLockConfig::default());
            let lock_path = mgr.resolver.resolve_tree_lock_path("/data").await.unwrap();
            provider
                .inner
                .try_create_token(
                    &lock_path,
                    &LockToken {
                        owner_id: "original-owner".to_string(),
                        time_ns: 1,
                        lock_type: PathLockKind::Tree,
                    },
                )
                .await
                .unwrap();

            let result = mgr.acquire_tree("/data", timeout, None).await;
            let token = provider
                .inner
                .read_token(&lock_path)
                .await
                .unwrap()
                .unwrap();
            assert!(token.time_ns > 1);
            if refresh {
                // The heartbeat wins after the stale read but before takeover.
                // Both the renewed token and its ownership must survive.
                assert!(matches!(result, Err(PathLockError::Timeout { .. })));
                assert_eq!(token.owner_id, "original-owner");
            } else {
                // A genuinely stale token remains reclaimable without waiting.
                let lease = result.unwrap();
                assert_eq!(token.owner_id, lease.lease.owner_id);
                mgr.release(&lease).await.unwrap();
            }
        }
    }

    /// Verify a downgrade CAS miss returns without dropping local lease state.
    #[tokio::test]
    async fn downgrade_compare_miss_is_bounded_and_retryable() {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        fs.mkdir("/data/sub", 0o755).await.unwrap();
        let provider = Arc::new(FailNextRemoveProvider::with_compare_miss());
        let mgr = PathLockManager::new(fs, provider, PathLockConfig::default());
        let tree = mgr
            .acquire_tree("/data/sub", Duration::ZERO, None)
            .await
            .unwrap();
        let capability = (&*tree.lease.lease_ref, &*tree.ownership_ref);
        let exact = mgr
            .acquire_exact("/data/sub", Duration::ZERO, Some(capability))
            .await
            .unwrap();

        let error = tokio::time::timeout(Duration::from_secs(1), mgr.release(&tree))
            .await
            .expect("downgrade CAS miss must not loop")
            .unwrap_err();
        assert!(matches!(error, PathLockError::Io(_)));
        assert!(mgr
            .get_owned_lease_by_capability(&tree.lease.lease_ref, &tree.ownership_ref)
            .await
            .is_none());
        assert!(mgr
            .get_releasable_lease_by_capability(&tree.lease.lease_ref, &tree.ownership_ref)
            .await
            .is_some());

        mgr.release(&exact).await.unwrap();
        mgr.release(&tree).await.unwrap();
    }

    #[tokio::test]
    async fn contending_waiters_eventually_acquire_with_backoff() {
        let mgr = Arc::new(make_manager().await);
        let holder = mgr
            .acquire_tree("/data/sub", Duration::ZERO, None)
            .await
            .unwrap();

        let mut waiters = Vec::new();
        for _ in 0..4 {
            let waiter_mgr = mgr.clone();
            waiters.push(tokio::spawn(async move {
                let lease = waiter_mgr
                    .acquire_tree("/data/sub", Duration::from_secs(5), None)
                    .await?;
                waiter_mgr.release(&lease).await
            }));
        }

        tokio::time::timeout(Duration::from_secs(2), async {
            loop {
                if mgr.metrics_snapshot().await.waiting_lock_count == 4 {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        })
        .await
        .expect("all waiters should enter the conflict/backoff path");
        mgr.release(&holder).await.unwrap();

        for waiter in waiters {
            tokio::time::timeout(Duration::from_secs(5), waiter)
                .await
                .expect("waiter should complete after holder release")
                .unwrap()
                .unwrap();
        }
        let mut stale_metrics = mgr.metrics.write().await;
        stale_metrics.waiting_lock_count = usize::MAX;
        drop(stale_metrics);
        let metrics = mgr.metrics_snapshot().await;
        assert_eq!(metrics.waiting_lock_count, 0);
    }

    #[test]
    fn poll_interval_bounds_back_off_and_saturate() {
        assert_eq!(PathLockManager::poll_interval_bounds(0), (50, 60));
        assert_eq!(PathLockManager::poll_interval_bounds(1), (60, 90));
        assert_eq!(PathLockManager::poll_interval_bounds(2), (91, 135));
        assert_eq!(PathLockManager::poll_interval_bounds(3), (136, 204));
        assert_eq!(PathLockManager::poll_interval_bounds(4), (204, 306));
        assert_eq!(PathLockManager::poll_interval_bounds(5), (307, 459));
        assert_eq!(PathLockManager::poll_interval_bounds(6), (400, 500));
        assert_eq!(PathLockManager::poll_interval_bounds(u32::MAX), (400, 500));
    }

    #[test]
    fn poll_interval_samples_stay_within_deterministic_bounds() {
        for attempt in [0, 1, 2, 3, 4, 5, 6, 50, u32::MAX] {
            let (lower_ms, upper_ms) = PathLockManager::poll_interval_bounds(attempt);
            for _ in 0..100 {
                let ms = PathLockManager::poll_interval_for_attempt(attempt).as_millis() as u64;
                assert!(
                    (lower_ms..=upper_ms).contains(&ms),
                    "attempt {attempt} produced {ms}ms outside [{lower_ms}, {upper_ms}]"
                );
            }
        }
    }

    #[test]
    fn retry_delay_does_not_overshoot_remaining_timeout() {
        let remaining = Duration::from_millis(7);
        assert_eq!(PathLockManager::retry_delay(0, remaining), remaining);
        assert_eq!(PathLockManager::retry_delay(u32::MAX, remaining), remaining);
    }

    #[tokio::test]
    async fn real_retry_loop_stays_within_ten_second_probe_budget() {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        let provider = Arc::new(RetryLoopCountingProvider::new());
        let mgr = PathLockManager::new(
            fs,
            provider.clone(),
            PathLockConfig {
                lock_expire_secs: 60.0,
                ..PathLockConfig::default()
            },
        );
        let requests = [PathLockRequest {
            path: "/data/file.txt".to_string(),
            kind: PathLockKind::Exact,
        }];

        let result = tokio::time::timeout(
            Duration::from_secs(12),
            mgr.acquire_batch(&requests, Duration::from_secs(10), None),
        )
        .await
        .expect("acquire loop should honor its ten-second timeout");

        assert!(matches!(result, Err(PathLockError::Timeout { .. })));
        let total_probes = provider.create_probes.load(Ordering::SeqCst);
        let retry_probes = total_probes.saturating_sub(1);
        let pre_acquire_conflicts = provider.pre_acquire_conflicts.load(Ordering::SeqCst);
        let post_verify_conflicts = provider.post_verify_conflicts.load(Ordering::SeqCst);
        assert!(
            retry_probes <= 29,
            "ten-second acquire exceeded retry probe budget: total={total_probes}, retries={retry_probes}"
        );
        assert!(
            pre_acquire_conflicts > 0,
            "test must exercise the acquisition conflict branch"
        );
        assert!(
            post_verify_conflicts > 0,
            "test must exercise the post-verification conflict branch"
        );
        assert_eq!(
            pre_acquire_conflicts + post_verify_conflicts,
            total_probes,
            "every acquire-loop probe must terminate in one of the two conflict branches"
        );
    }

    /// Verify owner mutexes serialize one owner without blocking another.
    #[tokio::test]
    async fn owner_mutex_is_scoped_to_owner() {
        let owner_a = OwnerState::new("owner-a".to_string());
        let owner_b = OwnerState::new("owner-b".to_string());
        let _guard = owner_a.registry.lock().await;
        assert!(owner_a.registry.try_lock().is_err());
        assert!(owner_b.registry.try_lock().is_ok());
    }
}
