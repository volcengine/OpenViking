use super::*;

impl PathLockManager {
    pub(super) async fn refresh_entry_with(
        provider: &Arc<dyn PathLockProvider>,
        registry: &mut OwnerRegistry,
        owner_id: &str,
        lease_ref: &str,
        validity: Duration,
    ) -> &'static str {
        let paths = registry.entries[lease_ref].lease.lock_paths.clone();
        let started_at = Instant::now();
        let now = Self::now_ns();
        let ttl = (validity.as_nanos() as f64 / LEASE_VALIDITY_RATIO) as u128;
        let results = provider
            .refresh_holders(&paths, owner_id, lease_ref, now, now.saturating_sub(ttl))
            .await;
        registry.apply_refresh_results(lease_ref, &paths, results, started_at, validity)
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
    pub(super) async fn release_lease_paths_with(
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
            let result = provider
                .mutate_holder(
                    &lock_path,
                    &owner.owner_id,
                    lease_ref,
                    Self::now_ns(),
                    0,
                    true,
                )
                .await;

            match result {
                Ok(released) => {
                    if !released {
                        warn!(%lease_ref, owner_id = %owner.owner_id, lock_path = %lock_path,
                            "pathlock holder was already absent at release");
                    }
                    if owner_registry
                        .entries
                        .get(lease_ref)
                        .is_some_and(|entry| entry.lease.lock_paths.iter().all(|p| p == &lock_path))
                    {
                        lease_registry.unpublish(lease_ref, &owner)?;
                    }
                    owner_registry.remove_path(lease_ref, &lock_path);
                }
                Err(release_error) => {
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

}
