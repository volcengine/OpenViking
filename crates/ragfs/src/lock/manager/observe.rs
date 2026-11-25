use super::*;

impl PathLockManager {
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
                    if (if ignore_stale {
                        token.live_kind(now_ns.saturating_sub(self.lock_expire_ns()))
                    } else {
                        Some(token.lock_type)
                    }) == Some(PathLockKind::Tree)
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

    /// Observe only this account's local leases and acquisition counters.
    pub async fn observe_account(&self, account_id: &str) -> PathLockObserveSnapshot {
        let account = self
            .account_metrics
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .get(&Some(account_id.to_owned()))
            .cloned();
        let Some(account) = account else {
            return PathLockObserveSnapshot {
                active_locks: self.lease_registry.active_account_count(account_id),
                waiting_locks: 0,
                stale_locks_removed: 0,
                conflicts: Vec::new(),
            };
        };
        let metrics = account.counters();
        PathLockObserveSnapshot {
            active_locks: self.lease_registry.active_account_count(account_id),
            waiting_locks: account.waiting.load(AtomicOrdering::Relaxed),
            stale_locks_removed: metrics.stale_tokens_removed,
            conflicts: metrics
                .recent_conflicts
                .iter()
                .map(|(_, c)| c.clone())
                .collect(),
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
        let accounts: Vec<_> = self
            .account_metrics
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .values()
            .cloned()
            .collect();
        metrics.waiting_lock_count = 0;
        metrics.wait_duration_ns = 0;
        metrics.stale_tokens_removed = 0;
        metrics.conflict_count = 0;
        let mut conflicts = Vec::new();
        for account in accounts {
            let counters = account.counters();
            metrics.waiting_lock_count += account.waiting.load(AtomicOrdering::Relaxed);
            metrics.wait_duration_ns = metrics
                .wait_duration_ns
                .saturating_add(counters.wait_duration_ns);
            metrics.stale_tokens_removed += counters.stale_tokens_removed;
            metrics.conflict_count += counters.conflict_count;
            conflicts.extend(counters.recent_conflicts.iter().cloned());
        }
        // Sequence numbers preserve runtime ordering across account buckets.
        conflicts.sort_unstable_by_key(|(sequence, _)| *sequence);
        let start = conflicts.len().saturating_sub(MAX_RECENT_CONFLICTS);
        metrics.recent_conflicts = conflicts.into_iter().skip(start).map(|(_, c)| c).collect();
        self.provider.update_metrics(&mut metrics);
        metrics
    }
}
