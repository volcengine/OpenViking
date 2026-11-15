use super::*;

/// Lock-poll backoff constants used by conflict-retry loops.
///
/// Keep the first retry no faster than the previous fixed 50 ms poll, then
/// spread waiters with bounded jitter while backing off to 500 ms.
const INITIAL_POLL_INTERVAL_MS: u64 = 50;
const MAX_POLL_INTERVAL_MS: u64 = 500;
const POLL_BACKOFF_NUMERATOR: u64 = 3;
const POLL_BACKOFF_DENOMINATOR: u64 = 2;
const POLL_JITTER_PERCENT: u64 = 20;
/// CAS retries for one holder mutation before reporting retryable contention.
const HOLDER_CAS_ATTEMPTS: usize = 16;

impl PathLockManager {
    pub(super) fn poll_interval_bounds(attempt: u32) -> (u64, u64) {
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

    pub(super) fn poll_interval_for_attempt(attempt: u32) -> Duration {
        let (lower_ms, upper_ms) = Self::poll_interval_bounds(attempt);
        Duration::from_millis(rand::thread_rng().gen_range(lower_ms..=upper_ms))
    }

    pub(super) fn retry_delay(attempt: u32, remaining: Duration) -> Duration {
        Self::poll_interval_for_attempt(attempt).min(remaining)
    }
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

impl PathLockManager {
    /// Return whether an acquire-loop error should be retried within the wait budget.
    pub(super) fn is_retryable_error(error: &PathLockError) -> bool {
        matches!(
            error,
            PathLockError::Conflict { .. }
                | PathLockError::Busy { .. }
                | PathLockError::EmptyToken { .. }
        )
    }

    /// Preserve both errors and mark a failed rollback as non-retryable.
    pub(super) fn rollback_error(error: PathLockError, rollback_error: PathLockError) -> PathLockError {
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
        self.acquire_batch_for_owner(requests, timeout, owner_capability, None, None)
            .await
    }

    /// Explicit recovery after the caller validates durable owner and coverage.
    /// Always acquire a fresh holder; no local ownership capability is reused.
    pub async fn reacquire_batch(
        &self,
        requests: &[PathLockRequest],
        owner_id: &str,
        timeout: Duration,
    ) -> PathLockResult<OwnedPathLockLease> {
        if !super::codec::LockTokenCodec::valid_id(owner_id) {
            return Err(PathLockError::InvalidRequest(
                "invalid recovery owner_id".into(),
            ));
        }
        self.acquire_batch_for_owner(requests, timeout, None, Some(owner_id), None)
            .await
    }

    pub(super) async fn acquire_batch_for_owner(
        &self,
        requests: &[PathLockRequest],
        timeout: Duration,
        owner_capability: Option<(&str, &str)>,
        recovered_owner: Option<&str>,
        source: Option<&str>,
    ) -> PathLockResult<OwnedPathLockLease> {
        if requests.is_empty() {
            return Err(PathLockError::InvalidRequest(
                "lock request batch must not be empty".to_string(),
            ));
        }
        let sorted = Self::normalize_requests(requests)?;

        let owner = match recovered_owner {
            Some(owner) => self.lease_registry.resolve_or_create_owner(owner),
            None => self.resolve_owner_state(owner_capability)?,
        };
        let owner_id = owner.owner_id.clone();
        let holder_id = Self::new_owner_id();
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
            let acquired_lock_paths = match self
                .try_acquire_batch_once(&sorted, &owner_id, &holder_id, source)
                .await
            {
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
            if source_invalid || confirmed_at.elapsed() >= Self::lease_validity(&self.config) {
                let error = PathLockError::InvalidRequest(
                    "pathlock lease expired during acquisition".to_string(),
                );
                let rollback = self
                    .rollback_holders(&acquired_lock_paths, &owner_id, &holder_id, source)
                    .await;
                break match rollback {
                    Ok(()) => Err(error),
                    Err(rollback_error) => Err(Self::rollback_error(error, rollback_error)),
                };
            }
            let lease = PathLockLease {
                lease_ref: holder_id.clone(),
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
                    .rollback_holders(&acquired_lock_paths, &owner_id, &holder_id, source)
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
                    .rollback_holders(&acquired_lock_paths, &owner_id, &holder_id, source)
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
        holder_id: &str,
        source: Option<&str>,
    ) -> PathLockResult<Vec<AtomicAcquisition>> {
        let now_ns = Self::now_ns();
        match self
            .provider
            .try_acquire_holders_atomic(
                requests,
                owner_id,
                holder_id,
                now_ns,
                now_ns.saturating_sub(self.lock_expire_ns()),
                source,
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
            // Classify an unavailable source before conflict checking. CAS below
            // revalidates every source against the exact token being replaced.
            if let Some(source) = source {
                for request in requests {
                    let path = self.resolver.resolve_exact_lock_path(&request.path).await?;
                    validate_handoff_source(
                        self.provider.read_token(&path).await?.as_ref(),
                        owner_id,
                        source,
                        request.kind,
                        now_ns.saturating_sub(self.lock_expire_ns()),
                    )?;
                }
            }
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
                            .try_acquire_holder(
                                &lock_path,
                                owner_id,
                                holder_id,
                                PathLockKind::Exact,
                                source,
                            )
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
                            .try_acquire_holder(
                                &lock_path,
                                owner_id,
                                holder_id,
                                PathLockKind::Tree,
                                source,
                            )
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
            if let Err(rollback_error) = self
                .rollback_holders(&acquired, owner_id, holder_id, source)
                .await
            {
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
            if let Err(rollback_error) = self
                .rollback_holders(&acquired, owner_id, holder_id, source)
                .await
            {
                return Err(Self::rollback_error(error, rollback_error));
            }
            return Err(error);
        }
        Ok(acquired)
    }

    /// Remove the new holder and restore a replaced handoff source, so a failed
    /// adoption leaves the producer's parked lease renewable.
    async fn rollback_holders(
        &self,
        acquired: &[AtomicAcquisition],
        owner: &str,
        holder: &str,
        source: Option<&str>,
    ) -> PathLockResult<()> {
        let mut first_error = None;
        for item in acquired.iter().rev() {
            let restored = match (&item.change, source) {
                (AcquisitionChange::Upgraded { previous, .. }, Some(source))
                    if previous.owner_id == owner =>
                {
                    previous.holders.iter().find(|h| h.id == source).cloned()
                }
                _ => None,
            };
            let result = match restored {
                Some(restored) => {
                    self.provider
                        .replace_holder(
                            &item.handle,
                            owner,
                            holder,
                            restored,
                            Self::now_ns().saturating_sub(self.lock_expire_ns()),
                        )
                        .await
                }
                None => self
                    .provider
                    .mutate_holder(&item.handle, owner, holder, Self::now_ns(), 0, true)
                    .await
                    .map(|_| ()),
            };
            if let Err(error) = result {
                first_error.get_or_insert(error);
            }
        }
        first_error.map_or(Ok(()), Err)
    }

    /// Add one holder with CAS. Lost races re-read the token, so a concurrent
    /// same-owner refresh or release never fails acquisition.
    async fn try_acquire_holder(
        &self,
        path: &str,
        owner: &str,
        holder: &str,
        kind: PathLockKind,
        source: Option<&str>,
    ) -> PathLockResult<AcquisitionChange> {
        for _ in 0..HOLDER_CAS_ATTEMPTS {
            let now = Self::now_ns();
            let stale_before = now.saturating_sub(self.lock_expire_ns());
            let mut replacement = LockToken::held(owner, holder, kind, now);
            let previous = self.provider.read_token(path).await?;
            if let Some(source) = source {
                validate_handoff_source(previous.as_ref(), owner, source, kind, stale_before)?;
            }
            let Some(previous) = previous else {
                match self.provider.try_create_token(path, &replacement).await {
                    Ok(()) => return Ok(AcquisitionChange::Created { replacement }),
                    Err(PathLockError::Conflict { .. }) => continue,
                    Err(error) => return Err(error),
                }
            };
            if previous.owner_id != owner && previous.live_kind(stale_before).is_some() {
                return Err(PathLockError::Conflict {
                    lock_path: path.into(),
                    owner: previous.owner_id,
                    kind: previous.lock_type,
                });
            }
            if previous.owner_id == owner {
                replacement = previous.clone();
                replacement.add_holder(holder, kind, now, stale_before, source);
            }
            if self
                .provider
                .compare_and_write_token(path, &previous, &replacement)
                .await?
            {
                return Ok(AcquisitionChange::Upgraded {
                    previous,
                    replacement,
                });
            }
        }
        Err(PathLockError::Busy {
            lock_path: path.into(),
            operation: "acquire holder".into(),
        })
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
                    if token.live_kind(now_ns.saturating_sub(self.lock_expire_ns()))
                        == Some(PathLockKind::Tree)
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

}
