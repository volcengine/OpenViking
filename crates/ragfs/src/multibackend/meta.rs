//! Multi-write metadata management.

use std::collections::BTreeMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use serde::{de::DeserializeOwned, Serialize};

use crate::core::context::{FsContext, FsContextInner, FS_CTX};
use crate::core::errors::{Error, Result};
use crate::core::filesystem::FileSystem;
use crate::core::types::{FileInfo, WriteFlag};
use crate::lock::PathLockManager;
use crate::multibackend::constants::{MULTIWRITE_MOUNT_PREFIX, SYSTEM_DIR, VBUCKETS};
use crate::multibackend::model::{
    BackendState, CheckpointsManifest, PartitionEntry, PartitionManifest, PartitionState,
    PartitionsManifest, ProtocolState, ProtocolStatus, ScopeKey, SegmentManifest,
};
use crate::multibackend::router::{build_initial_routes, MultiWritePaths};

/// Trait for resolving `FsContext` from a filesystem path.
///
/// Implementations extract `account_id` from the path (e.g. `/local/{account_id}/...`).
pub trait FsContextResolver: Send + Sync {
    /// Recover `FsContext` from a normalized path.
    /// Returns an error if the path cannot be resolved to a valid context.
    fn resolve(&self, path: &str) -> Result<FsContext>;
}

/// Default resolver that extracts `account_id` from `/local/{account_id}/...` paths.
pub struct DefaultFsContextResolver;

impl FsContextResolver for DefaultFsContextResolver {
    fn resolve(&self, path: &str) -> Result<FsContext> {
        let parts: Vec<&str> = path.trim_start_matches('/').split('/').collect();
        // Path format: /local/{account_id}/...
        if parts.len() >= 2 && parts[0] == MULTIWRITE_MOUNT_PREFIX.trim_start_matches('/') && !parts[1].is_empty(){
            Ok(Arc::new(FsContextInner::new(parts[1].to_string())))
        } else {
            Err(Error::internal(format!(
                "cannot resolve FsContext from path: {}",
                path
            )))
        }
    }
}

/// Resolver that extracts `account_id` from mount-relative paths such as `/{account_id}/...`.
pub struct RelativePathFsContextResolver;

impl FsContextResolver for RelativePathFsContextResolver {
    fn resolve(&self, path: &str) -> Result<FsContext> {
        let normalized = path.trim_start_matches('/');
        let account_id = normalized.split('/').next().unwrap_or_default();
        if account_id.is_empty() {
            return Err(Error::internal(format!(
                "cannot resolve FsContext from path: {path}"
            )));
        }
        Ok(Arc::new(FsContextInner::new(account_id.to_string())))
    }
}

/// Closed worker labels used by the multi-write error counter.
#[derive(Clone, Copy)]
pub(crate) enum MultiWriteWorker {
    Flush,
    Checkpoint,
    CatchUp,
    Gc,
}

/// Primary filesystem store for V2 multi-write metadata.
pub struct MetadataStore {
    primary: Arc<dyn FileSystem>,
    pathlock_manager: Arc<PathLockManager>,
    paths: MultiWritePaths,
    worker_errors: [AtomicU64; 4],
}

impl MetadataStore {
    /// Create a V2 metadata store for one logical mount prefix.
    pub fn new(
        primary: Arc<dyn FileSystem>,
        pathlock_manager: Arc<PathLockManager>,
        mount_prefix: &str,
    ) -> Result<Self> {
        Ok(Self {
            primary,
            pathlock_manager,
            paths: MultiWritePaths::new(mount_prefix)?,
            worker_errors: std::array::from_fn(|_| AtomicU64::new(0)),
        })
    }

    /// Record one final failure for the selected worker.
    pub(crate) fn record_worker_error(&self, worker: MultiWriteWorker) {
        self.worker_errors[worker as usize].fetch_add(1, Ordering::Relaxed);
    }

    /// Return the final failure count for the selected worker.
    pub(crate) fn worker_error_count(&self, worker: MultiWriteWorker) -> u64 {
        self.worker_errors[worker as usize].load(Ordering::Relaxed)
    }

    /// Return the logical-to-backend path mapper owned by this store.
    pub fn paths(&self) -> &MultiWritePaths {
        &self.paths
    }

    /// List account directories currently visible on the primary filesystem.
    pub(crate) async fn initialized_accounts(&self) -> Result<Vec<String>> {
        let mut accounts = Vec::new();
        for entry in self.primary.read_internal_dir("/").await? {
            if !entry.is_dir || entry.name == SYSTEM_DIR {
                continue;
            }
            let manifest = self.paths.account_manifest(&entry.name)?.1;
            match self.primary.stat(&manifest).await {
                Ok(_) => accounts.push(entry.name),
                Err(Error::NotFound(_)) => {}
                Err(error) => return Err(error),
            }
        }
        accounts.sort();
        Ok(accounts)
    }

    /// Return whether the primary account root still exists.
    pub(crate) async fn account_exists(&self, account_id: &str) -> Result<bool> {
        let root = self
            .paths
            .raw_backend_path(&format!("{MULTIWRITE_MOUNT_PREFIX}/{account_id}"))?;
        match self.primary.stat(&root).await {
            Ok(_) => Ok(true),
            Err(Error::NotFound(_)) => Ok(false),
            Err(error) => Err(error),
        }
    }

    /// Return the path lock manager used by V2 metadata operations.
    pub(crate) fn pathlock_manager(&self) -> &PathLockManager {
        &self.pathlock_manager
    }

    /// Build the filesystem context used for one logical metadata path.
    fn metadata_context(&self, logical_path: &str) -> Result<FsContext> {
        let parts: Vec<&str> = logical_path.trim_start_matches('/').split('/').collect();
        if parts.len() >= 2 && parts[0] == MULTIWRITE_MOUNT_PREFIX.trim_start_matches('/') && !parts[1].is_empty(){
            return Ok(Arc::new(
                FsContextInner::new(parts[1].to_string())
                    .with_bypass_cache(true)
                    .with_auto_pathlock_disabled(),
            ));
        }
        Err(Error::internal(format!(
            "cannot resolve metadata FsContext from path: {logical_path}"
        )))
    }

    /// Read one complete binary value from a canonical logical path.
    pub(crate) async fn read_bytes(&self, logical_path: &str) -> Result<Vec<u8>> {
        let backend_path = self.paths.raw_backend_path(logical_path)?;
        let context = self.metadata_context(logical_path)?;
        FS_CTX
            .scope(context, self.primary.read(&backend_path, 0, 0))
            .await
    }

    /// Read and validate the current V2 protocol status.
    pub(crate) async fn protocol_status(&self) -> Result<ProtocolStatus> {
        let state: ProtocolState = self.read_json(&self.paths.mount_protocol().0).await?;
        state.validate()?;
        Ok(state.status)
    }

    /// Replace protocol bytes under an Exact lock when the current value matches.
    pub(crate) async fn compare_protocol(
        &self,
        expected: &[u8],
        replacement: &[u8],
    ) -> Result<bool> {
        let logical_path = self.paths.mount_protocol().0;
        let lease = self
            .pathlock_manager
            .acquire_exact(
                &logical_path,
                self.pathlock_manager
                    .default_lock_timeout()
                    .max(std::time::Duration::from_secs(5)),
                None,
            )
            .await?;
        let operation = async {
            match self.read_bytes(&logical_path).await {
                Ok(current) if current == expected => {
                    self.write_bytes(&logical_path, replacement).await?;
                    Ok(true)
                }
                Ok(_) | Err(Error::NotFound(_)) => Ok(false),
                Err(error) => Err(error),
            }
        }
        .await;
        let release = self
            .pathlock_manager
            .release(&lease)
            .await
            .map_err(Error::from);
        merge_operation_and_cleanup(operation, release)
    }

    /// Read and deserialize one JSON value from a canonical logical path.
    pub async fn read_json<T>(&self, logical_path: &str) -> Result<T>
    where
        T: DeserializeOwned,
    {
        let bytes = self.read_bytes(logical_path).await?;
        serde_json::from_slice(&bytes).map_err(Error::from)
    }

    /// Write complete binary bytes to a canonical logical path.
    pub(crate) async fn write_bytes(&self, logical_path: &str, bytes: &[u8]) -> Result<()> {
        let backend_path = self.paths.raw_backend_path(logical_path)?;
        let context = self.metadata_context(logical_path)?;
        FS_CTX
            .scope(context, async {
                self.primary
                    .ensure_parent_dirs(&backend_path, 0o755)
                    .await?;
                self.primary
                    .write(&backend_path, bytes, 0, WriteFlag::Create)
                    .await?;
                Ok(())
            })
            .await
    }

    /// Validate and write one JSON value at a logical path.
    pub async fn publish_json<T, F>(&self, logical_path: &str, value: &T, validate: F) -> Result<()>
    where
        T: Serialize,
        F: FnOnce(&T) -> Result<()>,
    {
        let bytes = serde_json::to_vec(value)?;
        validate(value)?;
        self.write_bytes(logical_path, &bytes).await
    }

    /// Create one immutable blob or accept an identical existing value.
    pub async fn write_immutable(&self, logical_path: &str, bytes: &[u8]) -> Result<()> {
        match self.read_bytes(logical_path).await {
            Ok(existing) if existing == bytes => Ok(()),
            Ok(_) => Err(Error::AlreadyExists(format!(
                "immutable metadata differs: {logical_path}"
            ))),
            Err(Error::NotFound(_)) => self.write_bytes(logical_path, bytes).await,
            Err(error) => Err(error),
        }
    }

    /// List one raw metadata directory without public hidden-name filtering.
    pub(crate) async fn list_directory(&self, logical_path: &str) -> Result<Vec<FileInfo>> {
        let backend_path = self.paths.raw_backend_path(logical_path)?;
        self.primary.read_internal_dir(&backend_path).await
    }

    /// Remove one raw metadata file while treating absence as success.
    pub(crate) async fn remove_file(&self, logical_path: &str) -> Result<()> {
        let backend_path = self.paths.raw_backend_path(logical_path)?;
        self.remove_if_present(&backend_path).await
    }

    /// Remove one raw metadata tree while treating absence as success.
    pub(crate) async fn remove_all(&self, logical_path: &str) -> Result<()> {
        let backend_path = self.paths.raw_backend_path(logical_path)?;
        match self.primary.remove_all(&backend_path).await {
            Ok(()) | Err(Error::NotFound(_)) => Ok(()),
            Err(error) => Err(error),
        }
    }

    /// Recheck destructive GC safety while the caller holds the account Exact lock.
    pub(crate) async fn gc_scope_is_safe(&self, scope: &ScopeKey) -> Result<bool> {
        let path = self.paths.account_manifest(&scope.account_id)?.0;
        let manifest: PartitionsManifest = self.read_json(&path).await?;
        manifest.validate()?;
        scope.validate_against_manifest(&manifest)?;
        let stable = manifest
            .partitions
            .iter()
            .filter_map(|(&id, entry)| (entry.state == PartitionState::Stable).then_some(id))
            .collect::<Vec<_>>();
        Ok(stable.len() == manifest.partitions.len()
            && manifest.directory_events.iter().all(|event| {
                stable.iter().all(|id| {
                    event.positions.iter().any(|position| {
                        position.partition_id == *id && position.epoch == manifest.epoch
                    })
                })
            }))
    }

    /// Initialize one account under an Exact manifest lock and return its manifest.
    pub async fn initialize_account(
        &self,
        account_id: &str,
        partition_count: u32,
        backup_names: &[String],
    ) -> Result<PartitionsManifest> {
        let logical_manifest = self.paths.account_manifest(account_id)?.0;
        let lease = self
            .pathlock_manager
            .acquire_exact(
                &logical_manifest,
                self.pathlock_manager
                    .default_lock_timeout()
                    .max(std::time::Duration::from_secs(5)),
                None,
            )
            .await?;
        let result = self
            .initialize_account_locked(account_id, partition_count, backup_names)
            .await;
        let release = self
            .pathlock_manager
            .release(&lease)
            .await
            .map_err(Error::from);
        merge_operation_and_cleanup(result, release)
    }

    /// Update one partitions manifest while holding its Exact lock.
    pub(crate) async fn update_partitions_manifest<T, F>(
        &self,
        account_id: &str,
        update: F,
    ) -> Result<T>
    where
        F: FnOnce(&mut PartitionsManifest) -> Result<T>,
    {
        let logical_manifest = self.paths.account_manifest(account_id)?.0;
        let lease = self
            .pathlock_manager
            .acquire_exact(
                &logical_manifest,
                self.pathlock_manager
                    .default_lock_timeout()
                    .max(std::time::Duration::from_secs(5)),
                None,
            )
            .await?;
        let operation = async {
            let mut manifest: PartitionsManifest = self.read_json(&logical_manifest).await?;
            manifest.validate()?;
            let original = manifest.clone();
            let output = update(&mut manifest)?;
            validate_partitions_manifest_update(&original, &manifest)?;
            manifest.validate()?;
            if manifest != original {
                self.publish_json(&logical_manifest, &manifest, PartitionsManifest::validate)
                    .await?;
            }
            Ok(output)
        }
        .await;
        let release = self
            .pathlock_manager
            .release(&lease)
            .await
            .map_err(Error::from);
        merge_operation_and_cleanup(operation, release)
    }

    /// Initialize account metadata while the caller holds the manifest lock.
    async fn initialize_account_locked(
        &self,
        account_id: &str,
        partition_count: u32,
        backup_names: &[String],
    ) -> Result<PartitionsManifest> {
        let logical_manifest = self.paths.account_manifest(account_id)?.0;
        match self
            .read_json::<PartitionsManifest>(&logical_manifest)
            .await
        {
            Ok(manifest) => {
                manifest.validate()?;
                return Ok(manifest);
            }
            Err(Error::NotFound(_)) => {}
            Err(error) => return Err(error),
        }

        let routes = build_initial_routes(partition_count)?;
        let partitions = (0..partition_count)
            .map(|partition_id| {
                (
                    partition_id,
                    PartitionEntry {
                        state: PartitionState::Stable,
                    },
                )
            })
            .collect::<BTreeMap<_, _>>();
        let manifest = PartitionsManifest {
            version: 1,
            epoch: 1,
            vbuckets: VBUCKETS,
            partitions,
            routes,
            directory_events: Vec::new(),
        };
        manifest.validate()?;

        let backend_states = backup_names
            .iter()
            .map(|name| (name.clone(), BackendState { synced_seq: 0 }))
            .collect::<BTreeMap<_, _>>();
        for partition_id in 0..partition_count {
            let partition = PartitionManifest {
                version: 1,
                partition_id,
                epoch: 1,
            };
            self.write_validated_json_immutable(
                &self.paths.partition_manifest(account_id, partition_id)?.0,
                &partition,
                PartitionManifest::validate,
            )
            .await?;

            let segments = SegmentManifest {
                version: 1,
                next_seq: 1,
                segments: Vec::new(),
                backend_states: backend_states.clone(),
            };
            self.write_validated_json_immutable(
                &self.paths.segment_manifest(account_id, partition_id)?.0,
                &segments,
                SegmentManifest::validate,
            )
            .await?;

            let checkpoints = CheckpointsManifest {
                version: 1,
                latest_checkpoint: None,
            };
            self.write_validated_json_immutable(
                &self.paths.checkpoints_manifest(account_id, partition_id)?.0,
                &checkpoints,
                CheckpointsManifest::validate,
            )
            .await?;
        }
        self.write_validated_json_immutable(
            &logical_manifest,
            &manifest,
            PartitionsManifest::validate,
        )
        .await?;
        Ok(manifest)
    }

    /// Validate, serialize, and create one immutable JSON metadata file.
    async fn write_validated_json_immutable<T, F>(
        &self,
        logical_path: &str,
        value: &T,
        validate: F,
    ) -> Result<()>
    where
        T: Serialize,
        F: FnOnce(&T) -> Result<()>,
    {
        let bytes = serde_json::to_vec(value)?;
        validate(value)?;
        self.write_immutable(logical_path, &bytes).await
    }

    /// Remove a temporary file while treating an absent file as clean.
    async fn remove_if_present(&self, backend_path: &str) -> Result<()> {
        match self.primary.remove(backend_path).await {
            Ok(()) | Err(Error::NotFound(_)) => Ok(()),
            Err(error) => Err(error),
        }
    }
}

/// Reject updates that change the fixed partition routing identity.
fn validate_partitions_manifest_update(
    original: &PartitionsManifest,
    updated: &PartitionsManifest,
) -> Result<()> {
    if original.epoch != updated.epoch
        || original.partitions != updated.partitions
        || original.routes != updated.routes
    {
        return Err(Error::invalid_operation(
            "partition routing cannot change after account initialization",
        ));
    }
    Ok(())
}

/// Preserve an operation error while requiring cleanup to succeed.
fn merge_operation_and_cleanup<T>(operation: Result<T>, cleanup: Result<()>) -> Result<T> {
    match (operation, cleanup) {
        (Ok(value), Ok(())) => Ok(value),
        (Ok(_), Err(error)) => Err(error),
        (Err(error), Ok(())) => Err(error),
        (Err(error), Err(cleanup_error)) => Err(Error::internal(format!(
            "{error}; cleanup failed: {cleanup_error}"
        ))),
    }
}

/// Snapshot the current FsContext from the task-local, returning an error if unset.
pub fn current_required_ctx() -> Result<FsContext> {
    FS_CTX
        .try_with(|c| c.clone())
        .map_err(|_| Error::context_missing("FsContext not set in current task"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_default_resolver() {
        let resolver = DefaultFsContextResolver;
        let ctx = resolver
            .resolve("/local/tenant-1/resources/file.txt")
            .unwrap();
        assert_eq!(ctx.account_id(), "tenant-1");
    }

    #[test]
    fn test_default_resolver_invalid_path() {
        let resolver = DefaultFsContextResolver;
        assert!(resolver.resolve("/invalid/path").is_err());
    }
}
