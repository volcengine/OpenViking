//! Garbage collection for versioned multi-write metadata.

use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::Duration;

use crate::core::errors::{Error, Result};
use crate::multibackend::constants::SEGMENT_FILE_EXTENSION;
use crate::multibackend::meta::{MetadataStore, MultiWriteWorker};
use crate::multibackend::model::{is_checkpoint_directory_name, CheckpointsManifest, DirectoryEvent, LatestCheckpoint,PartitionState, PartitionsManifest, ProtocolStatus, ScopeKey, SegmentManifest,};
use crate::multibackend::provider::MultiWriteProvider;

struct ScopeSnapshot { latest: Option<LatestCheckpoint>, segments: SegmentManifest}

/// Collects obsolete segment and checkpoint metadata after publication.
pub struct MetadataGc {
    store: Arc<MetadataStore>,
    provider: Arc<dyn MultiWriteProvider>,
}

impl MetadataGc {
    /// Create a collector over the shared metadata store and segment provider.
    pub fn new(store: Arc<MetadataStore>, provider: Arc<dyn MultiWriteProvider>) -> Self {
        Self { store, provider }
    }

    /// Collect one scope while treating concurrent account deletion as complete.
    pub async fn collect_scope(&self, scope: &ScopeKey) -> Result<()> {
        match self.collect_scope_inner(scope).await {
            Err(Error::NotFound(_)) => Ok(()),
            Err(error) => {
                self.store.record_worker_error(MultiWriteWorker::Gc);
                Err(error)
            }
            result => result,
        }
    }

    /// Apply account safety checks before collecting one Stable scope.
    async fn collect_scope_inner(&self, scope: &ScopeKey) -> Result<()> {
        if self.store.protocol_status().await? != ProtocolStatus::Stable {
            return Ok(());
        }
        scope.validate()?;
        let account = self.store.update_partitions_manifest(&scope.account_id,
            |manifest| Ok(manifest.clone())).await?;
        if account.partitions.values().any(|entry| entry.state != PartitionState::Stable)
            || account.epoch != scope.epoch
            || !account.partitions.contains_key(&scope.partition_id)
        {
            return Ok(());
        }
        let stable = stable_partitions(&account);
        let positions_complete =
            directory_positions_complete(&account.directory_events, &stable, account.epoch);
        let retained = if positions_complete {
            self.collect_completed_events(&scope.account_id, &account, &stable).await?
        } else {
            Some(account.directory_events.clone())
        };
        let Some(retained) = retained else {
            return Ok(());
        };
        let allow_segments =
            positions_complete && directory_positions_complete(&retained, &stable, account.epoch);
        let latest = self.read_latest(scope).await?;
        if allow_segments {
            self.collect_segments(scope, latest.as_ref(), &retained).await?;
        }
        self.collect_checkpoints(scope).await
    }

    /// Remove completed events only if their account identity and content remain unchanged.
    async fn collect_completed_events(&self, account_id: &str,
        account: &PartitionsManifest, stable: &[u32]) -> Result<Option<Vec<DirectoryEvent>>> {
        let mut snapshots = BTreeMap::new();
        for &partition_id in stable {
            let scope = ScopeKey { account_id: account_id.to_string(),
                partition_id, epoch: account.epoch };
            snapshots.insert(partition_id, self.read_scope_snapshot(&scope).await?);
        }
        let completed = account.directory_events.iter()
            .filter(|event| event_is_complete(event, &snapshots))
            .cloned().collect::<Vec<_>>();
        let account_id = account_id.to_string();
        let expected_epoch = account.epoch;
        let expected_stable = stable.to_vec();
        self.store.update_partitions_manifest(&account_id, move |current| {
                if current.epoch != expected_epoch
                    || stable_partitions(current) != expected_stable
                {
                    return Ok(None);
                }
                current.directory_events.retain(|event|
                    !completed.iter().any(|candidate| candidate == event));
                Ok(Some(current.directory_events.clone()))
            }).await
    }

    /// Read a checkpoint pointer briefly and pair it with segment progress.
    async fn read_scope_snapshot(&self, scope: &ScopeKey) -> Result<ScopeSnapshot> {
        let latest = self.read_latest(scope).await?;
        let segments = self.provider.read_manifest(scope).await?;
        segments.validate()?;
        Ok(ScopeSnapshot { latest, segments })
    }

    /// Read the latest checkpoint while holding only its pointer Exact lock.
    async fn read_latest(&self, scope: &ScopeKey) -> Result<Option<LatestCheckpoint>> {
        let path = self.store.paths().checkpoints_manifest(
            &scope.account_id, scope.partition_id)?.0;
        let lease = self.store.pathlock_manager().acquire_exact(
            &path,
   self.store.pathlock_manager().default_lock_timeout().max(Duration::from_secs(5)),
            None,
        ).await?;
        let operation = async {
            let pointer: CheckpointsManifest = self.store.read_json(&path).await?;
            pointer.validate()?;
            Ok(pointer.latest_checkpoint)
        }.await;
        let release = self.store.pathlock_manager().release(&lease).await.map_err(Error::from);
        finish_with_release(operation, release)
    }

    /// Prune one eligible sealed prefix and remove every unreferenced segment blob.
    async fn collect_segments(&self, scope: &ScopeKey, latest: Option<&LatestCheckpoint>,
        retained: &[DirectoryEvent]) -> Result<()> {
        let manifest = self.provider.read_manifest(scope).await?;
        manifest.validate()?;
        let removable = manifest.segments.iter().take_while(|descriptor| {
            descriptor.segment_to_seq.is_some_and(|to| {
                latest.is_some_and(|checkpoint| checkpoint.checkpoint_to_seq >= to)
                    && manifest.backend_states.values().all(|state| state.synced_seq >= to)
                    && !retained.iter().any(|event| event.positions.iter().any(|position| {
                        position.partition_id == scope.partition_id
                            && position.epoch == scope.epoch
                            && (descriptor.segment_from_seq..=to).contains(&position.seq)
                    }))
            })
        }).map(|descriptor| descriptor.path.clone()).collect::<Vec<_>>();
        if !removable.is_empty() {
            for descriptor in self.provider.prune_sealed(scope, &removable).await? {
                self.remove_segment(scope, &descriptor.path).await?;
            }
        }
        let directory = self.store.paths().segments_dir(
            &scope.account_id, scope.partition_id)?.0;
        for entry in self.store.list_directory(&directory).await? {
            if !entry.is_dir && entry.name.ends_with(SEGMENT_FILE_EXTENSION) {
                self.remove_segment(scope, &entry.name).await?;
            }
        }
        Ok(())
    }

    /// Delete one segment blob while holding its Exact path lock.
    async fn remove_segment(&self, scope: &ScopeKey, name: &str) -> Result<()> {
        let directory = self.store.paths().segments_dir(
            &scope.account_id, scope.partition_id)?.0;
        let path = format!("{directory}/{name}");
        let lease = self.store.pathlock_manager().acquire_exact(
            &path,
   self.store.pathlock_manager().default_lock_timeout().max(Duration::from_secs(5)),
            None,
        ).await?;
        let operation = async {
            let manifest = self.provider.read_manifest(scope).await?;
            if manifest.segments.iter().any(|descriptor| descriptor.path == name) {
                return Ok(());
            }
            self.store.remove_file(&path).await
        }.await;
        let release = self.store.pathlock_manager().release(&lease).await.map_err(Error::from);
        finish_with_release(operation, release)
    }

    /// Delete non-latest checkpoint candidates in manifest, chunks, directory order.
    async fn collect_checkpoints(&self, scope: &ScopeKey) -> Result<()> {
        let directory = self.store.paths().checkpoints_dir(
            &scope.account_id, scope.partition_id)?.0;
        for entry in self.store.list_directory(&directory).await? {
            if !entry.is_dir || !is_checkpoint_directory_name(&entry.name) {
                continue;
            }
            let candidate = format!("{directory}/{}", entry.name);
            let lease = self.store.pathlock_manager().acquire_exact(
                &candidate,
       self.store.pathlock_manager().default_lock_timeout().max(Duration::from_secs(5)),
                None,
            ).await?;
            let operation = async {
                if self.read_latest(scope).await?.as_ref().is_some_and(
                    |latest| latest.path.trim_end_matches('/') == entry.name)
                {
                    return Ok(());
                }
                self.store.remove_all(&candidate).await
            }.await;
            let release = self.store.pathlock_manager().release(&lease).await.map_err(Error::from);
            finish_with_release(operation, release)?;
        }
        Ok(())
    }
}

/// Return Stable partition identifiers in deterministic order.
fn stable_partitions(manifest: &PartitionsManifest) -> Vec<u32> {
    manifest.partitions.iter().filter_map(|(&partition_id, entry)| {
        (entry.state == PartitionState::Stable).then_some(partition_id)
    }).collect()
}

/// Return whether every event has one current-epoch position per Stable partition.
fn directory_positions_complete(
    events: &[DirectoryEvent],
    stable: &[u32],
    epoch: u64,
) -> bool {
    events.iter().all(|event| stable.iter().all(|partition_id| {
        event.positions.iter().any(|position| {
            position.partition_id == *partition_id && position.epoch == epoch
        })
    }))
}

/// Return whether every marker is absorbed and applied by every current backend.
fn event_is_complete(
    event: &DirectoryEvent,
    snapshots: &BTreeMap<u32, ScopeSnapshot>,
) -> bool {
    event.positions.iter().all(|position| snapshots.get(&position.partition_id)
        .is_some_and(|snapshot| {
            snapshot.latest.as_ref().is_some_and(
                |latest| latest.checkpoint_to_seq >= position.seq)
                && snapshot.segments.backend_states.values().all(
                    |state| state.synced_seq >= position.seq)
        }))
}

/// Preserve an operation failure while requiring lock release to succeed.
fn finish_with_release<T>(operation: Result<T>, release: Result<()>) -> Result<T> {
    match (operation, release) {
        (Ok(value), Ok(())) => Ok(value),
        (Ok(_), Err(error)) | (Err(error), Ok(())) => Err(error),
        (Err(error), Err(release_error)) => Err(Error::internal(format!(
            "{error}; lock release failed: {release_error}"
        ))),
    }
}
