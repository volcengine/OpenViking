use super::*;

impl PathLockManager {
    /// Create a borrowed view of an owned lease.
    pub fn as_borrowed(&self, lease: &OwnedPathLockLease) -> BorrowedPathLockLease {
        BorrowedPathLockLease {
            lease: lease.lease.clone(),
        }
    }

    /// Export a handoff ref from an owned lease.
    pub fn to_handoff(&self, lease: &OwnedPathLockLease) -> PathLockHandoffRef {
        PathLockHandoffRef {
            holder_id: Some(lease.lease.lease_ref.clone()),
            lease_ref: Some(lease.lease.lease_ref.clone()),
            handle_mode: Some(self.provider.handle_mode()),
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

    /// Transfer an existing live source holder. Recovery and historic coverage
    /// reconstruction belong to the caller, followed by explicit reacquisition.
    pub async fn adopt(&self, handoff: &PathLockHandoffRef) -> PathLockResult<OwnedPathLockLease> {
        if handoff.owner_id.is_empty() || handoff.lock_paths.is_empty() {
            return Err(PathLockError::InvalidRequest(
                "handoff owner_id and lock_paths must not be empty".to_string(),
            ));
        }
        for request in &handoff.covered_paths {
            normalize_logical_path(&request.path)?;
        }
        if let Some(source) = &handoff.holder_id {
            if !super::codec::LockTokenCodec::valid_id(&handoff.owner_id)
                || !super::codec::LockTokenCodec::valid_id(source)
                || handoff.covered_paths.is_empty()
                || handoff.handle_mode.is_none()
                || Self::normalize_requests(&handoff.covered_paths)? != handoff.covered_paths
                || handoff.covered_paths.len() != handoff.lock_paths.len()
                || handoff.lease_ref.as_ref() != Some(source)
            {
                return Err(PathLockError::InvalidRequest(
                    "invalid holder handoff".into(),
                ));
            }
            // A local producer must have handed off before losing its capability.
            if let Some(owner) = self.lease_registry.resolve(source) {
                let registry = owner.registry.lock().await;
                if let Some(entry) = registry.entries.get(source) {
                    if !entry.pending_handoff
                        || entry.lease.owner_id != handoff.owner_id
                        || entry.lease.covered_paths != handoff.covered_paths
                        || entry.lease.lock_paths != handoff.lock_paths
                    {
                        return Err(PathLockError::HandoffFailed(
                            "handoff does not match parked lease".into(),
                        ));
                    }
                }
            }
            if handoff.handle_mode != Some(self.provider.handle_mode()) {
                return Err(PathLockError::HandoffRecoveryRequired {
                    reason: HandoffRecoveryReason::ProviderChanged,
                });
            }
            for (path, request) in handoff.lock_paths.iter().zip(&handoff.covered_paths) {
                let expected = match self.provider.handle_mode() {
                    PathLockHandleMode::LogicalPath => request.path.clone(),
                    PathLockHandleMode::LockPath => {
                        self.resolver.resolve_exact_lock_path(&request.path).await?
                    }
                };
                if path != &expected {
                    return Err(PathLockError::InvalidRequest(
                        "handoff coverage does not match handles".into(),
                    ));
                }
            }
            let owned = self
                .acquire_batch_for_owner(
                    &handoff.covered_paths,
                    Duration::ZERO,
                    None,
                    Some(&handoff.owner_id),
                    Some(source),
                )
                .await?;
            if let Some(owner) = self.lease_registry.resolve(source) {
                let mut registry = owner.registry.lock().await;
                if registry
                    .entries
                    .get(source)
                    .is_some_and(|e| e.pending_handoff)
                {
                    self.lease_registry.unpublish(source, &owner)?;
                    registry.rollback_insert(source);
                }
            }
            return Ok(owned);
        }

        let legacy_handoff = handoff.covered_paths.is_empty();
        let handle_mode = self.provider.handle_mode();
        if !legacy_handoff && handoff.covered_paths.len() != handoff.lock_paths.len() {
            return Err(PathLockError::InvalidRequest(
                "handoff lock_paths and covered_paths must have equal lengths".to_string(),
            ));
        }
        // Historic payloads have no transferable holder capability. Classify the
        // recovery reason without reading tokens or reconstructing coverage here.
        let source_mode = match handoff.handle_mode {
            Some(mode) => mode,
            None => {
                let is_file = |lp: &str| {
                    let name = lp.rsplit('/').next().unwrap_or("");
                    name == PATH_LOCK_FILE || name.starts_with(EXACT_LOCK_FILE_PREFIX)
                };
                if handoff.lock_paths.iter().all(|lp| is_file(lp)) {
                    PathLockHandleMode::LockPath
                } else if handoff.lock_paths.iter().any(|lp| is_file(lp)) {
                    return Err(PathLockError::InvalidRequest(
                        "handoff mixes logical and lock-file handles".to_string(),
                    ));
                } else if legacy_handoff {
                    return Err(PathLockError::HandoffRecoveryRequired {
                        reason: HandoffRecoveryReason::CoverageMissing,
                    });
                } else if handoff.lock_paths.iter().zip(&handoff.covered_paths).all(
                    |(lp, request)| {
                        normalize_logical_path(&request.path).is_ok_and(|path| lp == path)
                    },
                ) {
                    PathLockHandleMode::LogicalPath
                } else {
                    return Err(PathLockError::InvalidRequest(
                        "cannot infer handoff handle representation".to_string(),
                    ));
                }
            }
        };
        if source_mode != handle_mode {
            return Err(PathLockError::HandoffRecoveryRequired {
                reason: HandoffRecoveryReason::ProviderChanged,
            });
        }
        Err(PathLockError::HandoffRecoveryRequired {
            reason: if legacy_handoff {
                HandoffRecoveryReason::CoverageMissing
            } else {
                HandoffRecoveryReason::TokenUnavailable
            },
        })
    }

}
