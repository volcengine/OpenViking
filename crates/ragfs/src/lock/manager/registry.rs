use super::*;

/// Internal registry entry for an active lease.
#[derive(Debug, Clone)]
pub(super) struct LeaseEntry {
    pub(super) lease: PathLockLease,
    pub(super) ownership_ref: String,
    /// True once handoff() has parked this lease for a consumer to adopt.
    /// The entry stays in the registry and keeps refreshing, but the original
    /// ownership_ref can no longer operate it.
    pub(super) pending_handoff: bool,
    lock_kinds: HashMap<String, PathLockKind>,
    last_active_at: Instant,
    /// Irreversible; retain the capability and references until cleanup finishes.
    pub(super) invalidation: Option<LeaseInvalidation>,
}

/// The first irreversible authorization revocation, retained through cleanup.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) struct LeaseInvalidation {
    pub(super) at: Instant,
    pub(super) reason: InvalidationReason,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum InvalidationReason {
    TokenLost,
    ConfirmationDeadlineExceeded,
    ReleaseFailed,
}

impl LeaseEntry {
    /// Keep the first cause and timestamp, including during cleanup retries.
    pub(super) fn invalidate(&mut self, at: Instant, reason: InvalidationReason, paths: &[&str]) {
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
pub(super) struct TokenHealth {
    pub(super) last_confirmed_at: Instant,
    pub(super) consecutive_failures: u64,
}

#[derive(Debug, Default)]
pub(super) struct OwnerRegistry {
    pub(super) entries: HashMap<String, LeaseEntry>,
    pub(super) lock_refs: HashMap<String, usize>,
    pub(super) token_health: HashMap<String, TokenHealth>,
}

impl OwnerRegistry {
    /// Register one owned lease and increment its local token references.
    pub(super) fn insert(
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
                    .entry(format!("{lease_ref}\0{lock_path}"))
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
    pub(super) fn get_by_ref(&self, lease_ref: &str) -> Option<&LeaseEntry> {
        self.entries.get(lease_ref)
    }

    /// Remove one newly inserted lease without touching provider state.
    pub(super) fn rollback_insert(&mut self, lease_ref: &str) -> Option<LeaseEntry> {
        let entry = self.entries.remove(lease_ref)?;
        for path in &entry.lease.lock_paths {
            self.token_health.remove(&format!("{lease_ref}\0{path}"));
        }
        self.decrement_refs(&entry.lease.lock_paths);
        Some(entry)
    }

    /// True when the entry exists, is owned (not parked for handoff) and its
    /// ownership_ref matches. Used to gate acquisition/refresh against a stale
    /// capability under the same write guard that performs the mutation.
    pub(super) fn capability_matches(&self, lease_ref: &str, ownership_ref: &str) -> bool {
        self.entries.get(lease_ref).is_some_and(|entry| {
            !entry.pending_handoff
                && entry.invalidation.is_none()
                && entry.ownership_ref == ownership_ref
        })
    }

    /// Remove one committed lock path from a lease and its local reference count.
    pub(super) fn remove_path(&mut self, lease_ref: &str, lock_path: &str) {
        self.token_health
            .remove(&format!("{lease_ref}\0{lock_path}"));
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
    pub(super) fn mark_pending_handoff(&mut self, lease_ref: &str, ownership_ref: &str) -> PathLockResult<()> {
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

    pub(super) fn invalidate_expired(&mut self, now: Instant, validity: Duration) {
        let expired: Vec<_> = self
            .token_health
            .iter()
            .filter(|(_, health)| {
                now.saturating_duration_since(health.last_confirmed_at) >= validity
            })
            .filter_map(|(key, _)| {
                key.split_once('\0')
                    .map(|(lease, path)| (lease.to_owned(), path.to_owned()))
            })
            .collect();
        for (lease, path) in expired {
            if let Some(entry) = self.entries.get_mut(&lease) {
                entry.invalidate(
                    now,
                    InvalidationReason::ConfirmationDeadlineExceeded,
                    &[&path],
                );
            }
        }
    }

    /// Both heartbeat and explicit refresh use this state transition.
    pub(super) fn apply_refresh_results(
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
            let Some(health) = self.token_health.get_mut(&format!("{lease_ref}\0{path}")) else {
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
        if !lost_paths.is_empty() {
            self.entries.get_mut(lease_ref).unwrap().invalidate(
                now,
                InvalidationReason::TokenLost,
                &lost_paths.iter().map(String::as_str).collect::<Vec<_>>(),
            );
        }
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
pub(super) struct OwnerState {
    pub(super) owner_id: String,
    pub(super) registry: TokioMutex<OwnerRegistry>,
}

impl OwnerState {
    /// Create unpublished state for one owner.
    pub(super) fn new(owner_id: String) -> Self {
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
pub(super) struct LeaseRegistry {
    index: StdMutex<LeaseIndex>,
}

impl LeaseRegistry {
    /// Lock the short-lived index, recovering data if another task panicked.
    fn lock(&self) -> MutexGuard<'_, LeaseIndex> {
        self.index
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    /// Resolve one lease to its owner state without awaiting owner work.
    pub(super) fn resolve(&self, lease_ref: &str) -> Option<Arc<OwnerState>> {
        self.lock().by_lease.get(lease_ref).cloned()
    }

    /// Resolve one owner to its live state without awaiting owner work.
    pub(super) fn resolve_owner(&self, owner_id: &str) -> Option<Arc<OwnerState>> {
        self.lock().by_owner.get(owner_id).and_then(Weak::upgrade)
    }

    /// Resolve or create the canonical live state for one owner under the index mutex.
    pub(super) fn resolve_or_create_owner(&self, owner_id: &str) -> Arc<OwnerState> {
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
    pub(super) fn publish(&self, lease_ref: String, owner: Arc<OwnerState>) -> PathLockResult<()> {
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
    pub(super) fn unpublish(&self, lease_ref: &str, owner: &Arc<OwnerState>) -> PathLockResult<()> {
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
    /// Snapshot published leases without holding the index across async work.
    pub(super) fn snapshot(&self) -> Vec<(String, Arc<OwnerState>)> {
        self.lock()
            .by_lease
            .iter()
            .map(|(lease_ref, owner)| (lease_ref.clone(), owner.clone()))
            .collect()
    }

    /// Count currently published leases under the short index lock.
    pub(super) fn active_count(&self) -> usize {
        self.lock().by_lease.len()
    }

    /// Remove owner index entries whose weak state no longer has a live lease.
    pub(super) fn prune(&self) {
        self.lock()
            .by_owner
            .retain(|_, owner| owner.strong_count() > 0);
    }
}
