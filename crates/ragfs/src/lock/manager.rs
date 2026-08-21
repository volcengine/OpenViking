//! PathLockManager — single source of truth for all lock semantics.
//!
//! Owns the provider, resolver, lease registry, and metrics. All lock acquisition,
//! refresh, release, handoff, and adopt flows go through this manager.

use std::collections::{HashMap, HashSet};
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use rand::Rng;
use tokio::sync::RwLock;
use tracing::{debug, error, info};
use uuid::Uuid;

use crate::core::internal_names::{EXACT_LOCK_FILE_PREFIX, PATH_LOCK_FILE};
use crate::core::{FileSystem, FsContextView};

use super::metrics::LockMetrics;
use super::provider::PathLockProvider;
use super::resolver::{LockPathResolver, ResolvedExactPaths};
use super::types::{
    BorrowedPathLockLease, LockToken, OwnedPathLockLease, PathLockConflict, PathLockHandoffRef,
    PathLockKind, PathLockLease, PathLockObserveSnapshot, PathLockRequest, PathLockResult,
    PathLockError,
};

/// Configuration for the PathLockManager.
#[derive(Debug, Clone)]
pub struct PathLockConfig {
    /// Built-in provider name: `filesystem` or `memory`.
    pub provider: String,
    /// Default wait timeout for auto-acquired locks.
    pub lock_timeout_secs: f64,
    /// Seconds after which a lock token is considered stale.
    pub lock_expire_secs: f64,
}

impl Default for PathLockConfig {
    fn default() -> Self {
        Self {
            provider: "filesystem".to_string(),
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
}

#[derive(Debug)]
enum AcquisitionChange {
    Created,
    Reentrant,
    Upgraded {
        previous: LockToken,
        replacement: LockToken,
    },
}

/// Registry of all active leases in this process.
#[derive(Debug, Default)]
struct LeaseRegistry {
    entries: HashMap<String, LeaseEntry>,
    lock_refs: HashMap<(String, String), usize>,
}

impl LeaseRegistry {
    /// Register one owned lease and increment its local token references.
    fn insert(&mut self, lease: PathLockLease, ownership_ref: String) {
        let lease_ref = lease.lease_ref.clone();
        let lock_kinds = lease
            .lock_paths
            .iter()
            .cloned()
            .zip(lease.covered_paths.iter().map(|request| request.kind))
            .collect();
        let mut unique_paths = HashSet::new();
        for lock_path in &lease.lock_paths {
            if unique_paths.insert(lock_path.clone()) {
                *self
                    .lock_refs
                    .entry((lease.owner_id.clone(), lock_path.clone()))
                    .or_default() += 1;
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
            },
        );
    }

    /// Find one active lease for an owner.
    fn get_by_owner(&self, owner_id: &str) -> Option<&LeaseEntry> {
        self.entries
            .values()
            .find(|entry| entry.lease.owner_id == owner_id)
    }

    /// Find one active lease by its opaque reference.
    fn get_by_ref(&self, lease_ref: &str) -> Option<&LeaseEntry> {
        self.entries.get(lease_ref)
    }

    /// Remove one lease and return token paths whose local reference count reached zero.
    fn remove(&mut self, lease_ref: &str) -> Option<(LeaseEntry, Vec<String>, Vec<String>)> {
        let entry = self.entries.remove(lease_ref)?;
        let release_paths = self.decrement_refs(&entry.lease.owner_id, &entry.lease.lock_paths);
        let downgrade_paths = entry
            .lock_kinds
            .iter()
            .filter(|(path, kind)| {
                **kind == PathLockKind::Tree
                    && !release_paths.contains(path)
                    && self.strongest_kind(&entry.lease.owner_id, path) == Some(PathLockKind::Exact)
            })
            .map(|(path, _)| path.clone())
            .collect();
        Some((entry, release_paths, downgrade_paths))
    }

    /// True when the entry exists, is owned (not parked for handoff) and its
    /// ownership_ref matches. Used to gate release/refresh against a stale
    /// capability under the same write guard that performs the mutation.
    fn capability_matches(&self, lease_ref: &str, ownership_ref: &str) -> bool {
        self.entries
            .get(lease_ref)
            .is_some_and(|entry| !entry.pending_handoff && entry.ownership_ref == ownership_ref)
    }

    /// True when any live entry already holds every one of these lock paths for
    /// this owner. Used by the adopt token fallback to reject a same-process
    /// replay of an already-migrated handoff (which would otherwise insert a
    /// duplicate entry and inflate lock_refs).
    fn owner_already_holds(&self, owner_id: &str, lock_paths: &[String]) -> bool {
        self.entries.values().any(|entry| {
            entry.lease.owner_id == owner_id
                && lock_paths
                    .iter()
                    .all(|lp| entry.lease.lock_paths.contains(lp))
        })
    }

    /// Remove selected token references from one lease.
    fn remove_selected(
        &mut self,
        lease_ref: &str,
        selected_paths: &[String],
    ) -> Option<(LeaseEntry, String, Vec<String>, Vec<String>)> {
        let mut entry = self.entries.remove(lease_ref)?;
        let original_entry = entry.clone();
        let selected: HashSet<&str> = selected_paths.iter().map(String::as_str).collect();
        let removed: Vec<String> = entry
            .lease
            .lock_paths
            .iter()
            .filter(|path| selected.contains(path.as_str()))
            .cloned()
            .collect();
        entry
            .lease
            .lock_paths
            .retain(|path| !selected.contains(path.as_str()));
        entry
            .lock_kinds
            .retain(|path, _| !selected.contains(path.as_str()));
        entry.lease.covered_paths = original_entry
            .lease
            .lock_paths
            .iter()
            .zip(&original_entry.lease.covered_paths)
            .filter(|(path, _)| !selected.contains(path.as_str()))
            .map(|(_, request)| request.clone())
            .collect();

        let owner_id = entry.lease.owner_id.clone();
        let release_paths = self.decrement_refs(&owner_id, &removed);
        if !entry.lease.lock_paths.is_empty() {
            self.entries.insert(lease_ref.to_string(), entry);
        }
        let downgrade_paths = original_entry
            .lock_kinds
            .iter()
            .filter(|(path, kind)| {
                selected.contains(path.as_str())
                    && **kind == PathLockKind::Tree
                    && !release_paths.contains(path)
                    && self.strongest_kind(&owner_id, path) == Some(PathLockKind::Exact)
            })
            .map(|(path, _)| path.clone())
            .collect();
        Some((
            original_entry,
            owner_id,
            release_paths,
            downgrade_paths,
        ))
    }

    /// Restore one lease after a provider release failure.
    fn restore(&mut self, entry: LeaseEntry) {
        self.entries
            .insert(entry.lease.lease_ref.clone(), entry);
        self.rebuild_lock_refs();
    }

    /// Rebuild local token reference counts from active lease entries.
    fn rebuild_lock_refs(&mut self) {
        let mut lock_refs = HashMap::new();
        for entry in self.entries.values() {
            let mut unique_paths = HashSet::new();
            for lock_path in &entry.lease.lock_paths {
                if unique_paths.insert(lock_path) {
                    *lock_refs
                        .entry((entry.lease.owner_id.clone(), lock_path.clone()))
                        .or_default() += 1;
                }
            }
        }
        self.lock_refs = lock_refs;
    }

    /// Update the last successful refresh timestamp for one lease.
    fn touch(&mut self, lease_ref: &str) {
        if let Some(entry) = self.entries.get_mut(lease_ref) {
            entry.last_active_at = Instant::now();
        }
    }

    /// Park an owned lease for handoff: keep it (and its refresh) in the registry,
    /// but reject the original ownership_ref. Requires a matching, not-yet-parked entry.
    fn mark_pending_handoff(&mut self, lease_ref: &str, ownership_ref: &str) -> PathLockResult<()> {
        match self.entries.get_mut(lease_ref) {
            Some(entry) if entry.ownership_ref == ownership_ref && !entry.pending_handoff => {
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
    /// lock_refs is keyed by (owner_id, lock_path) and owner_id/lock_paths are
    /// unchanged, so re-keying `entries` needs no lock_refs adjustment. Uses raw
    /// entries.remove/insert (not LeaseRegistry::remove/insert) to avoid touching
    /// refcounts or rebuilding lock_kinds.
    fn take_pending_handoff(
        &mut self,
        lease_ref: &str,
        new_lease_ref: String,
        new_ownership_ref: String,
    ) -> Option<OwnedPathLockLease> {
        if !self.entries.get(lease_ref)?.pending_handoff {
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

    /// Decrement local references and return paths no longer used by any local lease.
    fn decrement_refs(&mut self, owner_id: &str, lock_paths: &[String]) -> Vec<String> {
        let mut release_paths = Vec::new();
        let mut unique_paths = HashSet::new();
        for lock_path in lock_paths {
            if !unique_paths.insert(lock_path.clone()) {
                continue;
            }
            let key = (owner_id.to_string(), lock_path.clone());
            match self.lock_refs.get_mut(&key) {
                Some(count) if *count > 1 => *count -= 1,
                Some(_) => {
                    self.lock_refs.remove(&key);
                    release_paths.push(lock_path.clone());
                }
                None => {}
            }
        }
        release_paths
    }

    /// Return the strongest active reference kind for one owner and token path.
    fn strongest_kind(&self, owner_id: &str, lock_path: &str) -> Option<PathLockKind> {
        self.entries
            .values()
            .filter(|entry| entry.lease.owner_id == owner_id)
            .filter_map(|entry| entry.lock_kinds.get(lock_path).copied())
            .max_by_key(|kind| match kind {
                PathLockKind::Exact => 0,
                PathLockKind::Tree => 1,
            })
    }
}

/// The central lock manager.
pub struct PathLockManager {
    provider: Arc<dyn PathLockProvider>,
    resolver: LockPathResolver,
    lease_registry: Arc<RwLock<LeaseRegistry>>,
    config: PathLockConfig,
    metrics: Arc<RwLock<LockMetrics>>,
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
        let registry = Arc::new(RwLock::new(LeaseRegistry::default()));
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
                let now_ns = Self::now_ns();
                let lease_refs: Vec<String> = {
                    let reg = refresh_registry.read().await;
                    reg.entries
                        .iter()
                        .map(|(lease_ref, _)| lease_ref.clone())
                        .collect()
                };
                for lease_ref in lease_refs {
                    let refresh_guard = refresh_registry.read().await;
                    let Some(entry) = refresh_guard.entries.get(&lease_ref) else {
                        continue;
                    };
                    let owner_id = entry.lease.owner_id.clone();
                    let lock_paths = entry.lease.lock_paths.clone();
                    let mut all_ok = true;
                    for lp in &lock_paths {
                        match refresh_provider.refresh_token(lp, &owner_id, now_ns).await {
                            Ok(true) => {
                                info!(lease_ref = %lease_ref, owner_id = %owner_id, lock_path = %lp, "pathlock lease automatically refreshed");
                            }
                            Ok(false) => {
                                all_ok = false;
                                info!(lease_ref = %lease_ref, owner_id = %owner_id, lock_path = %lp, "pathlock lease automatic refresh failed");
                            }
                            Err(error) => {
                                all_ok = false;
                                info!(lease_ref = %lease_ref, owner_id = %owner_id, lock_path = %lp, error = %error, "pathlock lease automatic refresh failed");
                            }
                        }
                    }
                    drop(refresh_guard);
                    if all_ok {
                        refresh_registry.write().await.touch(&lease_ref);
                    }
                }
                let stale_cutoff = Instant::now()
                    - Duration::from_secs_f64(refresh_config.lock_expire_secs * 2.0);
                let stale_refs: Vec<String> = {
                    let reg = refresh_registry.read().await;
                    reg.entries
                        .iter()
                        .filter(|(_, entry)| entry.last_active_at <= stale_cutoff)
                        .map(|(lease_ref, _)| lease_ref.clone())
                        .collect()
                };
                let mut removed = 0;
                for lease_ref in stale_refs {
                    let removed_entry = refresh_registry.write().await.remove(&lease_ref);
                    if let Some((entry, release_paths, _)) = removed_entry {
                        for lock_path in release_paths {
                            let _ = refresh_provider
                                .remove_token(&lock_path, &entry.lease.owner_id)
                                .await;
                        }
                        removed += 1;
                    }
                }
                let active_count = refresh_registry.read().await.entries.len();
                if removed > 0 { info!(removed_count = removed, active_count = active_count, expire_secs = refresh_config.lock_expire_secs, "released stale pathlock leases in background refresh"); }
                let mut m = refresh_metrics.write().await;
                m.active_lock_count = active_count;
                m.stale_leases_released += removed;
            }
        });

        Self {
            resolver: LockPathResolver::new(fs),
            provider,
            lease_registry: registry,
            config,
            metrics,
        }
    }

    /// Build one release error when the provider reports the token changed underneath us.
    fn release_changed_error(lock_path: &str) -> PathLockError {
        PathLockError::Io(format!("lock path '{lock_path}' changed while releasing"))
    }

    /// Remove one owned token while treating an already-missing token as released.
    ///
    /// A tree lock is stored inside the directory it protects. A successful
    /// recursive delete therefore removes both the directory and its lock token
    /// before the outer lease is released. Keep wrong-owner/CAS changes strict,
    /// but make release idempotent when the token no longer exists.
    async fn remove_owned_token(&self, lock_path: &str, owner_id: &str) -> PathLockResult<()> {
        match self.provider.remove_token(lock_path, owner_id).await {
            Ok(true) => Ok(()),
            Ok(false) => match self.provider.read_token(lock_path).await? {
                None => Ok(()),
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
    fn normalize_requests(requests: &[PathLockRequest]) -> Vec<PathLockRequest> {
        let mut by_path: HashMap<String, PathLockKind> = HashMap::new();
        for request in requests {
            let trimmed = request.path.trim_end_matches('/');
            let path = if trimmed.is_empty() {
                "/".to_string()
            } else {
                trimmed.to_string()
            };
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
        normalized
    }

    /// Check if a token is stale based on config.
    fn is_stale(&self, token: &LockToken, now_ns: u128) -> bool {
        let expire_ns = (self.config.lock_expire_secs * 1_000_000_000.0) as u128;
        now_ns.saturating_sub(token.time_ns) > expire_ns
    }

    /// Return whether an acquire-loop error should be retried within the wait budget.
    fn is_retryable_error(error: &PathLockError) -> bool {
        matches!(
            error,
            PathLockError::Conflict { .. } | PathLockError::Busy { .. }
        )
    }

    /// Resolve a trusted reentrant owner from an active local lease capability.
    async fn resolve_owner_id(
        &self,
        owner_capability: Option<(&str, &str)>,
    ) -> PathLockResult<String> {
        match owner_capability {
            Some((lease_ref, ownership_ref)) => self
                .get_owned_lease_by_capability(lease_ref, ownership_ref)
                .await
                .map(|lease| lease.lease.owner_id)
                .ok_or_else(|| {
                    PathLockError::InvalidRequest(format!(
                        "owned lease capability does not match ref '{lease_ref}'"
                    ))
                }),
            None => Ok(Self::new_owner_id()),
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
        let sorted = Self::normalize_requests(requests);

        let owner_id = self.resolve_owner_id(owner_capability).await?;
        debug!(owner_id = %owner_id, requests = ?requests, normalized_requests = ?sorted, timeout_ms = timeout.as_millis() as u64, "pathlock acquire batch start");

        let start = Instant::now();
        let mut acquired_lock_paths: Vec<(String, AcquisitionChange)> = Vec::new();
        let mut is_waiting = false;
        let mut retry_attempt: u32 = 0;

        loop {
            let mut conflict: Option<PathLockError> = None;
            let mut exact_resolutions: HashMap<String, ResolvedExactPaths> = HashMap::new();

            for req in &sorted {
                match req.kind {
                    PathLockKind::Exact => {
                        // Check ancestor tree locks — a tree lock on an ancestor
                        // blocks any exact lock on a descendant.
                        if let Err(e) = self.check_ancestor_tree_locks(&req.path, &owner_id).await {
                            conflict = Some(e);
                            break;
                        }
                        let resolved = self
                            .resolver
                            .resolve_exact_paths(&req.path)
                            .await?;
                        if let Err(e) = self
                            .check_lock_paths(&resolved.conflict_paths, &owner_id)
                            .await
                        {
                            conflict = Some(e);
                            break;
                        }
                        let lock_path = resolved.acquire_lock_path.clone();
                        exact_resolutions.insert(req.path.clone(), resolved);
                        if let Err(e) = self.ensure_lock_dir(&lock_path).await {
                            conflict = Some(PathLockError::Io(format!(
                                "failed to create lock dir: {e}"
                            )));
                            break;
                        }
                        match self
                            .try_acquire_one(&lock_path, &owner_id, PathLockKind::Exact)
                            .await
                        {
                            Ok(change) => acquired_lock_paths.push((lock_path, change)),
                            Err(e) => {
                                conflict = Some(e);
                                break;
                            }
                        }
                    }
                    PathLockKind::Tree => {
                        let lock_path = self.resolver.resolve_tree_lock_path(&req.path).await?;
                        // Check conflicts before creating directories so
                        // failed requests don't leave empty dirs behind.
                        if let Err(e) = self.check_ancestor_tree_locks(&req.path, &owner_id).await {
                            conflict = Some(e);
                            break;
                        }
                        let exact_candidates = self
                            .resolver
                            .resolve_exact_conflict_paths(&req.path)
                            .await?;
                        if let Err(e) =
                            self.check_lock_paths(&exact_candidates, &owner_id).await
                        {
                            conflict = Some(e);
                            break;
                        }
                        if let Err(e) = self.check_descendant_locks(&req.path, &owner_id).await {
                            conflict = Some(e);
                            break;
                        }
                        if let Err(e) = self.ensure_lock_dir(&lock_path).await {
                            conflict = Some(PathLockError::Io(format!(
                                "failed to create lock dir: {e}"
                            )));
                            break;
                        }
                        match self
                            .try_acquire_one(&lock_path, &owner_id, PathLockKind::Tree)
                            .await
                        {
                            Ok(change) => acquired_lock_paths.push((lock_path, change)),
                            Err(e) => {
                                conflict = Some(e);
                                break;
                            }
                        }
                    }
                }
                if conflict.is_some() {
                    break;
                }
            }

            if let Some(err) = conflict {
                // Rollback all acquired locks.
                self.rollback_acquisitions(&acquired_lock_paths, &owner_id)
                    .await?;
                acquired_lock_paths.clear();

                if !Self::is_retryable_error(&err) {
                    if is_waiting {
                        let mut metrics = self.metrics.write().await;
                        metrics.waiting_lock_count = metrics.waiting_lock_count.saturating_sub(1);
                    }
                    return Err(err);
                }

                // Track conflict and waiting metrics.
                {
                    let mut metrics = self.metrics.write().await;
                    if !is_waiting {
                        info!(owner_id = %owner_id, requests = ?sorted, timeout_ms = timeout.as_millis() as u64, error = %err, "pathlock acquire batch entered wait state");
                        is_waiting = true;
                        metrics.waiting_lock_count += 1;
                    }
                    if let PathLockError::Conflict {
                        ref lock_path,
                        ref owner,
                        kind,
                    } = &err
                    {
                        metrics.record_conflict(PathLockConflict {
                            lock_path: lock_path.clone(),
                            conflicting_owner: owner.clone(),
                            conflicting_kind: *kind,
                        });
                    }
                }

                // Check timeout.
                if start.elapsed() >= timeout {
                    if is_waiting {
                        let mut m = self.metrics.write().await;
                        m.waiting_lock_count = m.waiting_lock_count.saturating_sub(1);
                    }
                    return Err(PathLockError::Timeout {
                        elapsed_ms: start.elapsed().as_millis() as u64,
                    });
                }

                // Try stale cleanup on the conflicting lock, then retry.
                if let PathLockError::Conflict { ref lock_path, .. } = &err {
                    if let Some(token) = self.provider.read_token(lock_path).await? {
                        let now_ns = Self::now_ns();
                        if self.is_stale(&token, now_ns) {
                            if self
                                .provider
                                .remove_token(lock_path, &token.owner_id)
                                .await?
                            {
                                info!(lock_path = %lock_path, stale_owner = %token.owner_id, token_kind = ?token.lock_type, age_ms = ((now_ns.saturating_sub(token.time_ns)) / 1_000_000) as u64, "removed stale pathlock token during acquire retry");
                                self.metrics.write().await.stale_tokens_removed += 1;
                            }
                        }
                    }
                }

                let remaining = timeout.saturating_sub(start.elapsed());
                tokio::time::sleep(Self::retry_delay(retry_attempt, remaining)).await;
                retry_attempt = retry_attempt.saturating_add(1);
                continue;
            }

            // Post-conflict check: after writing all tokens, re-verify no
            // conflicting lock appeared in the TOCTOU window between pre-check
            // and token write.
            let mut post_conflict: Option<PathLockError> = None;
            for req in &sorted {
                match req.kind {
                    PathLockKind::Exact => {
                        if let Err(e) =
                            self.check_ancestor_tree_locks(&req.path, &owner_id).await
                        {
                            post_conflict = Some(e);
                            break;
                        }
                        let resolved = exact_resolutions.get(&req.path).ok_or_else(|| {
                            PathLockError::Io(format!(
                                "missing cached exact resolution for '{}'",
                                req.path
                            ))
                        })?;
                        if let Err(e) = self
                            .check_lock_paths(&resolved.conflict_paths, &owner_id)
                            .await
                        {
                            post_conflict = Some(e);
                            break;
                        }
                    }
                    PathLockKind::Tree => {
                        if let Err(e) =
                            self.check_ancestor_tree_locks(&req.path, &owner_id).await
                        {
                            post_conflict = Some(e);
                            break;
                        }
                        let exact_candidates = self
                            .resolver
                            .resolve_exact_conflict_paths(&req.path)
                            .await?;
                        if let Err(e) =
                            self.check_lock_paths(&exact_candidates, &owner_id).await
                        {
                            post_conflict = Some(e);
                            break;
                        }
                        if let Err(e) =
                            self.check_descendant_locks(&req.path, &owner_id).await
                        {
                            post_conflict = Some(e);
                            break;
                        }
                    }
                }
            }

            if let Some(err) = post_conflict {
                // Another process slipped in — rollback and retry.
                self.rollback_acquisitions(&acquired_lock_paths, &owner_id)
                    .await?;
                acquired_lock_paths.clear();

                if !Self::is_retryable_error(&err) {
                    if is_waiting {
                        let mut metrics = self.metrics.write().await;
                        metrics.waiting_lock_count = metrics.waiting_lock_count.saturating_sub(1);
                    }
                    return Err(err);
                }

                {
                    let mut metrics = self.metrics.write().await;
                    if !is_waiting {
                        is_waiting = true;
                        metrics.waiting_lock_count += 1;
                    }
                    if let PathLockError::Conflict {
                        ref lock_path,
                        ref owner,
                        kind,
                    } = &err
                    {
                        metrics.record_conflict(PathLockConflict {
                            lock_path: lock_path.clone(),
                            conflicting_owner: owner.clone(),
                            conflicting_kind: *kind,
                        });
                    }
                }

                if start.elapsed() >= timeout {
                    if is_waiting {
                        let mut m = self.metrics.write().await;
                        m.waiting_lock_count = m.waiting_lock_count.saturating_sub(1);
                    }
                    return Err(PathLockError::Timeout {
                        elapsed_ms: start.elapsed().as_millis() as u64,
                    });
                }

                let remaining = timeout.saturating_sub(start.elapsed());
                tokio::time::sleep(Self::retry_delay(retry_attempt, remaining)).await;
                retry_attempt = retry_attempt.saturating_add(1);
                continue;
            }

            // All acquired and post-verified successfully.
            let lease = PathLockLease {
                lease_ref: Self::new_owner_id(),
                owner_id: owner_id.clone(),
                lock_paths: acquired_lock_paths
                    .into_iter()
                    .map(|(lock_path, _)| lock_path)
                    .collect(),
                covered_paths: sorted.clone(),
            };
            let ownership_ref = Self::new_owner_id();
            self.lease_registry
                .write()
                .await
                .insert(lease.clone(), ownership_ref.clone());

            let active_count = self.lease_registry.read().await.entries.len();
            let mut metrics = self.metrics.write().await;
            metrics.active_lock_count = active_count;
            metrics.wait_duration_ms += start.elapsed().as_millis() as u64;
            if is_waiting {
                metrics.waiting_lock_count = metrics.waiting_lock_count.saturating_sub(1);
            }
            if is_waiting { info!(lease_ref = %lease.lease_ref, owner_id = %lease.owner_id, lock_paths = ?lease.lock_paths, covered_paths = ?lease.covered_paths, wait_ms = start.elapsed().as_millis() as u64, "pathlock acquire batch succeeded after waiting"); }

            return Ok(OwnedPathLockLease {
                lease,
                ownership_ref,
            });
        }
    }

    /// Roll back token changes made by one incomplete batch acquisition.
    async fn rollback_acquisitions(
        &self,
        acquired_lock_paths: &[(String, AcquisitionChange)],
        owner_id: &str,
    ) -> PathLockResult<()> {
        for (lock_path, change) in acquired_lock_paths.iter().rev() {
            match change {
                AcquisitionChange::Created => {
                    self.provider.remove_token(lock_path, owner_id).await?;
                }
                AcquisitionChange::Reentrant => {}
                AcquisitionChange::Upgraded {
                    previous,
                    replacement,
                } => {
                    if !self
                        .provider
                        .compare_and_write_token(lock_path, replacement, previous)
                        .await?
                    {
                        return Err(PathLockError::Io(format!(
                            "failed to roll back token upgrade at '{lock_path}'"
                        )));
                    }
                }
            }
        }
        Ok(())
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
            if !self.is_stale(&existing, now_ns) {
                return Err(PathLockError::Conflict {
                    lock_path: lock_path.to_string(),
                    owner: existing.owner_id,
                    kind: existing.lock_type,
                });
            }
            // Stale — remove it before attempting to create our own token.
            self.provider.remove_token(lock_path, &existing.owner_id).await?;
        }

        self.provider.try_create_token(lock_path, &token).await?;
        debug!(lock_path = %lock_path, owner_id = %owner_id, kind = ?kind, "created new pathlock token");
        Ok(AcquisitionChange::Created)
    }

    /// Check concrete lock-file paths for a live token owned by another owner.
    async fn check_lock_paths(
        &self,
        lock_paths: &[String],
        owner_id: &str,
    ) -> PathLockResult<()> {
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
    async fn check_ancestor_tree_locks(
        &self,
        path: &str,
        owner_id: &str,
    ) -> PathLockResult<()> {
        let mut current = path.to_string();
        let now_ns = Self::now_ns();

        loop {
            let parent = match current.rsplit_once('/') {
                Some((p, _)) if !p.is_empty() => p.to_string(),
                Some(_) if current != "/" => "/".to_string(),
                _ => break,
            };

            let ancestor_lock = if parent == "/" {
                format!("/{}", PATH_LOCK_FILE)
            } else {
                format!("{}/{}", parent, PATH_LOCK_FILE)
            };
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

            current = parent;
        }
        Ok(())
    }

    /// Check for descendant locks that would conflict with a tree lock.
    async fn check_descendant_locks(
        &self,
        path: &str,
        owner_id: &str,
    ) -> PathLockResult<()> {
        let scan_start = Instant::now();
        let descendants = self.provider.scan_descendant_locks(path).await?;
        let now_ns = Self::now_ns();

        let mut metrics = self.metrics.write().await;
        metrics.descendant_scan_count += 1;
        metrics.descendant_scan_duration_ms += scan_start.elapsed().as_millis() as u64;
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
            if !created_dirs.is_empty() { debug!(lock_path = %lock_path, created_dirs = ?created_dirs, "ensured pathlock parent directories"); }
        }
        Ok(())
    }

    /// Refresh an owned lease. Returns "refreshed", "lost", or "failed".
    pub async fn refresh(&self, lease: &OwnedPathLockLease) -> PathLockResult<String> {
        // Validate the capability before touching the provider: a stale or parked
        // lease must not be able to refresh tokens it no longer controls.
        if !self
            .lease_registry
            .read()
            .await
            .capability_matches(&lease.lease.lease_ref, &lease.ownership_ref)
        {
            return Err(PathLockError::InvalidRequest(format!(
                "owned lease capability does not match ref '{}'",
                lease.lease.lease_ref
            )));
        }
        let now_ns = Self::now_ns();
        let mut all_ok = true;
        let mut any_ok = false;

        for lp in &lease.lease.lock_paths {
            match self
                .provider
                .refresh_token(lp, &lease.lease.owner_id, now_ns)
                .await
            {
                Ok(true) => any_ok = true,
                Ok(false) => all_ok = false,
                Err(_) => all_ok = false,
            }
        }

        if any_ok {
            self.lease_registry
                .write()
                .await
                .touch(&lease.lease.lease_ref);
        }

        let result = if all_ok && any_ok { "refreshed" } else if any_ok { "lost" } else { "failed" };
        info!(lease_ref = %lease.lease.lease_ref, owner_id = %lease.lease.owner_id, lock_paths = ?lease.lease.lock_paths, result = %result, "pathlock refresh completed");
        Ok(result.to_string())
    }

    /// Release an owned lease.
    pub async fn release(&self, lease: &OwnedPathLockLease) -> PathLockResult<()> {
        let removed = {
            // Gate the capability check and removal under one write guard so a stale
            // producer capability cannot race an adopt/handoff and delete the
            // consumer's live entry. A mismatch is a hard error, not a silent success.
            let mut registry = self.lease_registry.write().await;
            if !registry.capability_matches(&lease.lease.lease_ref, &lease.ownership_ref) {
                return Err(PathLockError::InvalidRequest(format!(
                    "owned lease capability does not match ref '{}'",
                    lease.lease.lease_ref
                )));
            }
            registry.remove(&lease.lease.lease_ref)
        };
        if let Some((mut entry, release_paths, downgrade_paths)) = removed {
            let mut failed_paths = Vec::new();
            let mut first_error = None;
            for lock_path in release_paths {
                if let Err(error) = self
                    .remove_owned_token(&lock_path, &entry.lease.owner_id)
                    .await
                {
                    failed_paths.push(lock_path);
                    if first_error.is_none() {
                        first_error = Some(error);
                    }
                }
            }
            for lock_path in downgrade_paths {
                if let Err(error) = self
                    .downgrade_token_to_exact(&lock_path, &entry.lease.owner_id)
                    .await
                {
                    failed_paths.push(lock_path);
                    if first_error.is_none() {
                        first_error = Some(error);
                    }
                }
            }
            if let Some(error) = first_error {
                let failed: HashSet<&str> = failed_paths.iter().map(String::as_str).collect();
                entry.lease.covered_paths = entry
                    .lease
                    .lock_paths
                    .iter()
                    .zip(&entry.lease.covered_paths)
                    .filter(|(path, _)| failed.contains(path.as_str()))
                    .map(|(_, request)| request.clone())
                    .collect();
                entry.lease.lock_paths = failed_paths;
                entry
                    .lock_kinds
                    .retain(|path, _| entry.lease.lock_paths.contains(path));
                error!(
                    lease_ref = %entry.lease.lease_ref,
                    owner_id = %entry.lease.owner_id,
                    lock_paths = ?entry.lease.lock_paths,
                    error = %error,
                    "failed to release pathlock lease"
                );
                self.lease_registry.write().await.restore(entry);
                return Err(error);
            }
        }
        let active_count = self.lease_registry.read().await.entries.len();
        let mut metrics = self.metrics.write().await;
        metrics.active_lock_count = active_count;
        debug!(lease_ref = %lease.lease.lease_ref, owner_id = %lease.lease.owner_id, lock_paths = ?lease.lease.lock_paths, active_count = active_count, "released pathlock lease");
        Ok(())
    }

    /// Release selected lock paths from an owned lease.
    pub async fn release_selected(
        &self,
        lease: &OwnedPathLockLease,
        lock_paths: &[String],
    ) -> PathLockResult<()> {
        let removed = {
            // Same-guard capability gate as release(); prevents a stale capability
            // from trimming a re-adopted lease. A mismatch is a hard error.
            let mut registry = self.lease_registry.write().await;
            if !registry.capability_matches(&lease.lease.lease_ref, &lease.ownership_ref) {
                return Err(PathLockError::InvalidRequest(format!(
                    "owned lease capability does not match ref '{}'",
                    lease.lease.lease_ref
                )));
            }
            registry.remove_selected(&lease.lease.lease_ref, lock_paths)
        };
        if let Some((mut entry, owner_id, release_paths, downgrade_paths)) = removed {
            let mut failed_paths = Vec::new();
            let mut first_error = None;
            for lock_path in release_paths {
                if let Err(error) = self.remove_owned_token(&lock_path, &owner_id).await {
                    failed_paths.push(lock_path);
                    if first_error.is_none() {
                        first_error = Some(error);
                    }
                }
            }
            for lock_path in downgrade_paths {
                if let Err(error) = self.downgrade_token_to_exact(&lock_path, &owner_id).await {
                    failed_paths.push(lock_path);
                    if first_error.is_none() {
                        first_error = Some(error);
                    }
                }
            }
            if let Some(error) = first_error {
                let selected: HashSet<&str> = lock_paths.iter().map(String::as_str).collect();
                let failed: HashSet<&str> = failed_paths.iter().map(String::as_str).collect();
                let retained: Vec<(String, PathLockRequest)> = entry
                    .lease
                    .lock_paths
                    .iter()
                    .zip(&entry.lease.covered_paths)
                    .filter(|(path, _)| {
                        !selected.contains(path.as_str()) || failed.contains(path.as_str())
                    })
                    .map(|(path, request)| (path.clone(), request.clone()))
                    .collect();
                entry.lease.lock_paths =
                    retained.iter().map(|(path, _)| path.clone()).collect();
                entry.lease.covered_paths =
                    retained.into_iter().map(|(_, request)| request).collect();
                entry
                    .lock_kinds
                    .retain(|path, _| entry.lease.lock_paths.contains(path));
                self.lease_registry.write().await.restore(entry);
                return Err(error);
            }
        }
        let active_count = self.lease_registry.read().await.entries.len();
        self.metrics.write().await.active_lock_count = active_count;
        debug!(lease_ref = %lease.lease.lease_ref, owner_id = %lease.lease.owner_id, requested_lock_paths = ?lock_paths, active_count = active_count, "released selected pathlock lease paths");
        Ok(())
    }

    /// Atomically reduce one same-owner Tree token to Exact.
    async fn downgrade_token_to_exact(
        &self,
        lock_path: &str,
        owner_id: &str,
    ) -> PathLockResult<()> {
        loop {
            let Some(current) = self.provider.read_token(lock_path).await? else {
                return Ok(());
            };
            if current.owner_id != owner_id {
                return Err(PathLockError::Conflict {
                    lock_path: lock_path.to_string(),
                    owner: current.owner_id,
                    kind: current.lock_type,
                });
            }
            if current.lock_type == PathLockKind::Exact {
                return Ok(());
            }
            let replacement = LockToken {
                owner_id: current.owner_id.clone(),
                time_ns: current.time_ns,
                lock_type: PathLockKind::Exact,
            };
            if self
                .provider
                .compare_and_write_token(lock_path, &current, &replacement)
                .await?
            {
                return Ok(());
            }
        }
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
        self.lease_registry
            .write()
            .await
            .mark_pending_handoff(&lease.lease.lease_ref, &lease.ownership_ref)?;
        let active_count = self.lease_registry.read().await.entries.len();
        info!(lease_ref = %lease.lease.lease_ref, owner_id = %lease.lease.owner_id, lock_paths = ?lease.lease.lock_paths, active_count = active_count, "parked pathlock lease for handoff");
        Ok(())
    }

    /// Adopt a handoff ref, returning a new owned lease.
    pub async fn adopt(
        &self,
        handoff: &PathLockHandoffRef,
    ) -> PathLockResult<OwnedPathLockLease> {
        if handoff.owner_id.is_empty() || handoff.lock_paths.is_empty() {
            return Err(PathLockError::InvalidRequest(
                "handoff owner_id and lock_paths must not be empty".to_string(),
            ));
        }

        // Local fast path: a handoff carrying lease_ref MUST be adopted from this
        // process's registry — it is never legacy. Adopt in-place (migrate to a fresh
        // lease_ref, rotate ownership_ref) so the background refresh never lapses.
        if let Some(lease_ref) = handoff.lease_ref.as_deref() {
            let mut registry = self.lease_registry.write().await;
            let local = registry.get_by_ref(lease_ref).map(|entry| {
                // A lease_ref-bearing handoff is non-legacy: require owner_id,
                // lock_paths AND covered_paths to match the live entry so a forged
                // or corrupted payload cannot be adopted.
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
                    // Entry is Owned, not parked: producer has not called handoff()
                    // yet (retry after handoff), or it was already adopted.
                    return Err(PathLockError::HandoffFailed(format!(
                        "pathlock lease '{lease_ref}' is not pending handoff (handoff() not called yet, or already adopted)"
                    )));
                }
                Some((true, true)) => {
                    let new_ownership_ref = Self::new_owner_id();
                    let new_lease_ref = Self::new_owner_id();
                    let owned = registry
                        .take_pending_handoff(lease_ref, new_lease_ref, new_ownership_ref)
                        .ok_or_else(|| {
                            PathLockError::HandoffFailed(format!(
                                "pathlock lease '{lease_ref}' vanished during adopt"
                            ))
                        })?;
                    let active_count = registry.entries.len();
                    drop(registry);
                    info!(lease_ref = %owned.lease.lease_ref, owner_id = %owned.lease.owner_id, lock_paths = ?owned.lease.lock_paths, active_count = active_count, "adopted pathlock lease (local fast path)");
                    return Ok(owned);
                }
                None => {
                    // lease_ref not in this registry: either a genuine cross-process /
                    // restart adopt, or a same-process replay of an already-migrated
                    // lease. Both are handled by the token fallback below, whose insert
                    // dedups same-owner/path entries to reject replays.
                }
            }
        }

        let legacy_handoff = handoff.covered_paths.is_empty();
        if !legacy_handoff && handoff.covered_paths.len() != handoff.lock_paths.len() {
            return Err(PathLockError::InvalidRequest(
                "handoff lock_paths and covered_paths must have equal lengths".to_string(),
            ));
        }
        let now_ns = Self::now_ns();

        // ponytail: legacy durable handoffs only persisted owner_id/handle_id + lock_paths.
        // We validate live ownership/kind by the lock file path itself and keep covered_paths empty.
        // Upgrade path: once old queue payloads are drained, remove this branch and require covered_paths.
        for (index, lp) in handoff.lock_paths.iter().enumerate() {
            let expected_kind = if legacy_handoff {
                let file_name = lp.rsplit('/').next().unwrap_or("");
                if lp == &format!("/{}", PATH_LOCK_FILE) || lp.ends_with(&format!("/{}", PATH_LOCK_FILE)) {
                    PathLockKind::Tree
                } else if file_name.starts_with(EXACT_LOCK_FILE_PREFIX) {
                    PathLockKind::Exact
                } else {
                    return Err(PathLockError::InvalidRequest(format!(
                        "legacy handoff lock path '{lp}' is not a supported lock file"
                    )));
                }
            } else {
                let request = &handoff.covered_paths[index];
                let expected_paths = match request.kind {
                    PathLockKind::Exact => {
                        self.resolver.resolve_exact_conflict_paths(&request.path).await?
                    }
                    PathLockKind::Tree => {
                        vec![self.resolver.resolve_tree_lock_path(&request.path).await?]
                    }
                };
                if !expected_paths.contains(lp) {
                    return Err(PathLockError::InvalidRequest(format!(
                        "handoff coverage '{}' does not map to lock path '{lp}'",
                        request.path
                    )));
                }
                request.kind
            };
            match self.provider.read_token(lp).await? {
                Some(token)
                    if token.owner_id == handoff.owner_id && token.lock_type == expected_kind =>
                {
                    // Still valid — refresh the timestamp.
                    match self
                        .provider
                        .refresh_token(lp, &handoff.owner_id, now_ns)
                        .await
                    {
                        Ok(true) => {}
                        Ok(false) => {
                            return Err(PathLockError::HandoffFailed(format!(
                                "lock path '{lp}' changed while adopting owner '{}'",
                                handoff.owner_id
                            )));
                        }
                        Err(error) => return Err(error),
                    }
                }
                _ => {
                    return Err(PathLockError::HandoffFailed(format!(
                        "lock path '{lp}' is no longer owned by '{}'",
                        handoff.owner_id
                    )));
                }
            }
        }

        let lease = PathLockLease {
            lease_ref: Self::new_owner_id(),
            owner_id: handoff.owner_id.clone(),
            lock_paths: handoff.lock_paths.clone(),
            covered_paths: handoff.covered_paths.clone(),
        };
        let ownership_ref = Self::new_owner_id();
        let active_count = {
            // Dedup + insert under one write guard: if this owner already holds these
            // paths locally, this is a replay of an already-adopted handoff (the token
            // is still valid because the live lease keeps refreshing it). Reject it so
            // we never create two capabilities for the same lock.
            let mut registry = self.lease_registry.write().await;
            if registry.owner_already_holds(&handoff.owner_id, &handoff.lock_paths) {
                return Err(PathLockError::HandoffFailed(format!(
                    "pathlock handoff for owner '{}' was already adopted",
                    handoff.owner_id
                )));
            }
            registry.insert(lease.clone(), ownership_ref.clone());
            registry.entries.len()
        };
        self.metrics.write().await.active_lock_count = active_count;
        info!(lease_ref = %lease.lease_ref, owner_id = %lease.owner_id, lock_paths = ?lease.lock_paths, legacy_handoff = legacy_handoff, active_count = active_count, "adopted pathlock lease");

        Ok(OwnedPathLockLease {
            lease,
            ownership_ref,
        })
    }

    /// Check if a path is locked (for observability).
    pub async fn is_locked(&self, path: &str, ignore_stale: bool) -> PathLockResult<bool> {
        let now_ns = Self::now_ns();

        // Check own .path.ovlock.
        let dir_lock = format!("{}/{}", path.trim_end_matches('/'), PATH_LOCK_FILE);
        if let Ok(Some(token)) = self.provider.read_token(&dir_lock).await {
            if !ignore_stale || !self.is_stale(&token, now_ns) {
                return Ok(true);
            }
        }

        // Check exact sidecar.
        let exact_paths = self.resolver.resolve_exact_conflict_paths(path).await?;
        for lp in &exact_paths {
            if let Ok(Some(token)) = self.provider.read_token(lp).await {
                if !ignore_stale || !self.is_stale(&token, now_ns) {
                    return Ok(true);
                }
            }
        }

        // Check ancestor tree locks.
        let mut current = path.to_string();
        loop {
            let parent = match current.rsplit_once('/') {
                Some((p, _)) if !p.is_empty() => p.to_string(),
                Some(_) if current != "/" => "/".to_string(),
                _ => break,
            };
            let ancestor_lock = if parent == "/" {
                format!("/{}", PATH_LOCK_FILE)
            } else {
                format!("{}/{}", parent, PATH_LOCK_FILE)
            };
            if let Ok(Some(token)) = self.provider.read_token(&ancestor_lock).await {
                // Only Tree locks propagate to descendants.
                if token.lock_type == PathLockKind::Tree
                    && (!ignore_stale || !self.is_stale(&token, now_ns))
                {
                    return Ok(true);
                }
            }
            current = parent;
        }

        Ok(false)
    }

    /// Return an observability snapshot.
    pub async fn observe(&self) -> PathLockObserveSnapshot {
        let metrics = self.metrics.read().await.clone();
        PathLockObserveSnapshot {
            active_locks: metrics.active_lock_count,
            waiting_locks: metrics.waiting_lock_count,
            stale_locks_removed: metrics.stale_tokens_removed,
            conflicts: metrics.recent_conflicts,
        }
    }

    /// Look up an owned lease by owner_id for cross-FFI lease operations.
    pub async fn get_owned_lease(&self, owner_id: &str) -> Option<OwnedPathLockLease> {
        let registry = self.lease_registry.read().await;
        registry
            .get_by_owner(owner_id)
            .filter(|entry| !entry.pending_handoff)
            .map(|entry| OwnedPathLockLease {
                lease: entry.lease.clone(),
                ownership_ref: entry.ownership_ref.clone(),
            })
    }

    /// Look up an owned lease by opaque lease_ref for cross-FFI lease operations.
    pub async fn get_owned_lease_by_ref(&self, lease_ref: &str) -> Option<OwnedPathLockLease> {
        let registry = self.lease_registry.read().await;
        registry
            .get_by_ref(lease_ref)
            .filter(|entry| !entry.pending_handoff)
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
        let registry = self.lease_registry.read().await;
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
        let registry = self.lease_registry.read().await;
        let entry = registry
            .get_by_ref(lease_ref)
            .filter(|entry| !entry.pending_handoff)
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
        let Some(lease_ref) = view.pathlock_lease_ref() else {
            debug!(requests = ?requests, "pathlock auto action resolved to acquire");
            return Ok(AutoPathLockAction::Acquire);
        };
        let lease = self
            .get_owned_lease_by_ref(lease_ref)
            .await
            .ok_or_else(|| {
                PathLockError::InvalidRequest(format!(
                    "unknown pathlock lease ref '{lease_ref}'"
                ))
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
        self.metrics.read().await.clone()
    }
}

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

        async fn read_dir(&self, path: &str) -> FsResult<Vec<FileInfo>> {
            self.inner.read_dir(path).await
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
        busy_next_remove: AtomicBool,
        busy_remove_count: AtomicUsize,
    }

    impl FailNextRemoveProvider {
        /// Build a memory provider whose next token read fails.
        fn with_read_failure() -> Self {
            Self {
                inner: crate::lock::provider::MemoryPathLockProvider::new(),
                fail_next_read: AtomicBool::new(true),
                fail_next_remove: AtomicBool::new(false),
                return_false_next_remove: AtomicBool::new(false),
                busy_next_remove: AtomicBool::new(false),
                busy_remove_count: AtomicUsize::new(0),
            }
        }

        /// Build a memory provider whose next remove reports a retryable busy error.
        fn with_busy_remove() -> Self {
            Self {
                inner: crate::lock::provider::MemoryPathLockProvider::new(),
                fail_next_read: AtomicBool::new(false),
                fail_next_remove: AtomicBool::new(false),
                return_false_next_remove: AtomicBool::new(false),
                busy_next_remove: AtomicBool::new(true),
                busy_remove_count: AtomicUsize::new(0),
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

        async fn try_create_token(
            &self,
            lock_path: &str,
            token: &LockToken,
        ) -> PathLockResult<()> {
            self.inner.try_create_token(lock_path, token).await
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
            self.inner
                .refresh_token(lock_path, owner_id, time_ns)
                .await
        }

        async fn remove_token(
            &self,
            lock_path: &str,
            owner_id: &str,
        ) -> PathLockResult<bool> {
            if self.busy_next_remove.swap(false, Ordering::SeqCst) {
                self.busy_remove_count.fetch_add(1, Ordering::SeqCst);
                return Err(PathLockError::Busy {
                    lock_path: lock_path.to_string(),
                    operation: "remove".to_string(),
                });
            }
            if self.return_false_next_remove.swap(false, Ordering::SeqCst) {
                return Ok(false);
            }
            if self.fail_next_remove.swap(false, Ordering::SeqCst) {
                return Err(PathLockError::Io("injected remove failure".to_string()));
            }
            self.inner.remove_token(lock_path, owner_id).await
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

        async fn remove_token(&self, lock_path: &str, owner_id: &str) -> PathLockResult<bool> {
            self.inner.remove_token(lock_path, owner_id).await
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
        let second_manager =
            PathLockManager::new(fs.clone(), provider, PathLockConfig::default());
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
        ));
        let mgr = PathLockManager::new(fs.clone(), provider, PathLockConfig::default());
        let lease = mgr
            .acquire_tree("/data/delete-me", Duration::ZERO, None)
            .await
            .unwrap();

        fs.remove_all("/data/delete-me").await.unwrap();

        mgr.release(&lease).await.unwrap();
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
            mgr.acquire_exact("/data/file.txt", Duration::ZERO, None).await,
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
        let capability = (
            exact.lease.lease_ref.as_str(),
            exact.ownership_ref.as_str(),
        );

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
            .read_token("/data/sub/.path.ovlock")
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
    async fn handoff_and_adopt() {
        let mgr = make_manager().await;
        let lease = mgr
            .acquire_tree("/data/sub", Duration::from_secs(1), None)
            .await
            .unwrap();
        let handoff = mgr.to_handoff(&lease);
        assert_eq!(handoff.lease_ref.as_deref(), Some(lease.lease.lease_ref.as_str()));
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
        let producer = PathLockManager::new(fs.clone(), provider.clone(), PathLockConfig::default());
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
    }

    #[tokio::test]
    async fn adopt_is_not_replayable() {
        // Adopting the same handoff twice must not create two capabilities for the
        // same lock. First adopt migrates the entry; the replay must be rejected.
        let mgr = make_manager().await;
        let lease = mgr
            .acquire_tree("/data/sub", Duration::from_secs(1), None)
            .await
            .unwrap();
        let handoff = mgr.to_handoff(&lease);
        mgr.handoff(&lease).await.unwrap();
        let _first = mgr.adopt(&handoff).await.unwrap();
        assert!(matches!(
            mgr.adopt(&handoff).await,
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
        let _root = mgr
            .acquire_tree("/", Duration::ZERO, None)
            .await
            .unwrap();
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
    async fn busy_cleanup_is_retried() {
        let fs = Arc::new(MemFileSystem::new());
        fs.mkdir("/data", 0o755).await.unwrap();
        let provider = Arc::new(FailNextRemoveProvider::with_busy_remove());
        provider
            .inner
            .try_create_token(
                "/data/file.txt/.path.ovlock",
                &LockToken {
                    owner_id: "stale-owner".to_string(),
                    time_ns: 1,
                    lock_type: PathLockKind::Tree,
                },
            )
            .await
            .unwrap();
        let mgr = PathLockManager::new(
            fs,
            provider.clone(),
            PathLockConfig {
                lock_expire_secs: 1.0,
                ..PathLockConfig::default()
            },
        );

        let lease = mgr
            .acquire_tree("/data/file.txt", Duration::from_millis(200), None)
            .await
            .unwrap();

        assert_eq!(provider.busy_remove_count.load(Ordering::SeqCst), 1);
        mgr.release(&lease).await.unwrap();
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
}
