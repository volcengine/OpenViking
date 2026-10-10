//! Backup catch-up workers and current-state reconciliation primitives.

use std::collections::{BTreeMap, BTreeSet, HashSet};
use std::future::Future;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use rand::Rng;
use serde_json::Value;
use tokio::sync::watch;
use tracing::{debug, warn};

use crate::core::context::{FsContext, FsContextInner, FS_CTX};
use crate::core::errors::{Error, Result};
use crate::core::filesystem::FileSystem;
use crate::core::internal_names::is_multiwrite_internal_path;
use crate::core::types::{FileInfo, ListSortBy, SortOrder, WriteFlag};
use crate::lock::OwnedPathLockLease;
use crate::multibackend::constants::{MULTIWRITE_MOUNT_PREFIX, SYSTEM_DIR};
use crate::multibackend::meta::{MetadataStore, MultiWriteWorker};
use crate::multibackend::model::{
    FileState, PartitionState, PartitionsManifest, ScopeKey, SegmentEventType, SegmentRecord,
};
use crate::multibackend::provider::MultiWriteProvider;

#[cfg(test)]
#[path = "tests/test_catch_up.rs"]
mod tests;

/// One configured backup handle owned by its catch-up worker.
pub struct CatchUpTarget {
    /// Stable backend id stored in partition progress.
    pub backend_id: String,
    /// Encrypted business filesystem handle.
    pub backend: Arc<dyn FileSystem>,
}

/// Parsed checkpoint state accepted by CatchUpWorker from the future reader.
#[derive(Clone)]
pub struct CheckpointSnapshot {
    /// Last sequence represented by this complete checkpoint.
    pub checkpoint_to_seq: u64,
    /// Materialized file states already decoded by the checkpoint reader.
    pub file_states: Vec<FileState>,
    /// Directory prefixes requiring current-state reconciliation.
    pub directory_prefixes: Vec<String>,
}

/// Typed outcome of reading the latest checkpoint publication.
pub enum CheckpointReadResult {
    /// No published checkpoint fits the requested sequence window.
    NoCheckpoint,
    /// One complete validated checkpoint snapshot.
    Snapshot(CheckpointSnapshot),
    /// Immutable files changed during a reader/GC race; reread latest.
    RetryLatestCheckpoint,
}

/// Consumer boundary implemented by the future checkpoint reader.
#[async_trait]
pub trait CheckpointConsumer: Send + Sync {
    /// Return the latest usable parsed checkpoint inside the fixed tail.
    async fn read_latest(
        &self,
        scope: &ScopeKey,
        synced_seq: u64,
        tail_seq: u64,
    ) -> Result<CheckpointReadResult>;
}

struct EmptyCheckpointConsumer;

#[async_trait]
impl CheckpointConsumer for EmptyCheckpointConsumer {
    /// Return no checkpoint until T-011 supplies a reader.
    async fn read_latest(
        &self,
        _scope: &ScopeKey,
        _synced_seq: u64,
        _tail_seq: u64,
    ) -> Result<CheckpointReadResult> {
        Ok(CheckpointReadResult::NoCheckpoint)
    }
}

/// One backup worker that consumes V2 metadata without blocking primary writes.
pub struct CatchUpWorker {
    backend_id: String,
    primary: Arc<dyn FileSystem>,
    backup: Arc<dyn FileSystem>,
    store: Arc<MetadataStore>,
    provider: Arc<dyn MultiWriteProvider>,
    checkpoint_consumer: Arc<dyn CheckpointConsumer>,
    cancellation: Option<watch::Receiver<bool>>,
}

/// One Exact PathLock lease protecting a backup catch-up worker.
pub struct CatchUpLease {
    store: Arc<MetadataStore>,
    lease: OwnedPathLockLease,
    valid: AtomicBool,
}

enum CatchUpFailure {
    Cancelled,
    Failed(Error),
}

impl From<Error> for CatchUpFailure {
    /// Preserve ordinary failures while propagating explicit cancellation separately.
    fn from(error: Error) -> Self {
        Self::Failed(error)
    }
}

type CatchUpResult<T> = std::result::Result<T, CatchUpFailure>;

impl CatchUpLease {
    /// Acquire the mount-level Exact lease for one logical backup id.
    async fn acquire(store: Arc<MetadataStore>, backend_id: &str) -> Result<Self> {
        let path =format!("{MULTIWRITE_MOUNT_PREFIX}/{SYSTEM_DIR}/.multiwrite.catch-up.{backend_id}");
        let manager = store.pathlock_manager();
        let lease = manager
            .acquire_exact(
                &path,
                manager.default_lock_timeout().max(Duration::from_secs(5)),
                None,
            )
            .await?;
        Ok(Self {
            store,
            lease,
            valid: AtomicBool::new(true),
        })
    }

    /// Refresh the lease and return false for every non-refreshed result.
    async fn refresh(&self) -> Result<bool> {
        let result = self.store.pathlock_manager().refresh(&self.lease).await;
        let refreshed = matches!(result.as_deref(), Ok("refreshed"));
        if !refreshed {
            self.valid.store(false, Ordering::SeqCst);
        }
        result.map(|_| refreshed).map_err(Error::from)
    }

    /// Return whether every explicit refresh has retained the lease.
    fn is_valid(&self) -> bool {
        self.valid.load(Ordering::SeqCst)
    }

    /// Release this worker's Exact lease.
    async fn release(&self) -> Result<()> {
        self.store
            .pathlock_manager()
            .release(&self.lease)
            .await
            .map_err(Error::from)
    }
}

impl CatchUpWorker {
    /// Create one worker for an encrypted primary and backup handle.
    pub fn new(
        backend_id: String,
        primary: Arc<dyn FileSystem>,
        backup: Arc<dyn FileSystem>,
        store: Arc<MetadataStore>,
        provider: Arc<dyn MultiWriteProvider>,
    ) -> Self {
        Self {
            backend_id,
            primary,
            backup,
            store,
            provider,
            checkpoint_consumer: Arc::new(EmptyCheckpointConsumer),
            cancellation: None,
        }
    }

    /// Attach the parsed-checkpoint consumer supplied by T-011.
    pub fn with_checkpoint_consumer(
        mut self,
        checkpoint_consumer: Arc<dyn CheckpointConsumer>,
    ) -> Self {
        self.checkpoint_consumer = checkpoint_consumer;
        self
    }

    /// Attach a runtime cancellation receiver for interruptible catch-up rounds.
    pub(crate) fn with_cancellation(mut self, cancellation: watch::Receiver<bool>) -> Self {
        self.cancellation = Some(cancellation);
        self
    }

    /// Acquire the backup lease, execute one bounded round, and release it.
    pub async fn run_once(&self) -> Result<()> {
        let outcome: CatchUpResult<()> = async {
            let lease =
                Arc::new(CatchUpLease::acquire(self.store.clone(), &self.backend_id).await?);
            debug!(backend = %self.backend_id, "multi-write catch-up lease acquired");
            let operation = self.run_round(&lease).await;
            let release = lease.release().await;
            match (operation, release) {
                (Ok(()), Ok(())) => Ok(()),
                (Err(CatchUpFailure::Cancelled), Ok(())) => Err(CatchUpFailure::Cancelled),
                (Err(CatchUpFailure::Failed(error)), _) => Err(CatchUpFailure::Failed(error)),
                (_, Err(error)) => Err(CatchUpFailure::Failed(error)),
            }
        }
        .await;
        match outcome {
            Ok(()) => Ok(()),
            Err(CatchUpFailure::Cancelled) => Err(Error::internal("catch-up was cancelled")),
            Err(CatchUpFailure::Failed(error)) => {
                self.store.record_worker_error(MultiWriteWorker::CatchUp);
                Err(error)
            }
        }
    }

    /// Wait until runtime cancellation or remain pending for standalone workers.
    async fn cancelled(&self) {
        let Some(mut cancellation) = self.cancellation.clone() else {
            std::future::pending::<()>().await;
            return;
        };
        while !*cancellation.borrow() {
            if cancellation.changed().await.is_err() {
                return;
            }
        }
    }

    /// Reconcile system state, registry membership, and one fixed partition window.
    async fn run_round(&self, lease: &Arc<CatchUpLease>) -> CatchUpResult<()> {
        let guarded = LeaseGuardedFileSystem {
            inner: self.backup.clone(),
            lease: lease.clone(),
        };
        self.reconcile_with_retry(&guarded, &lease, SYSTEM_DIR, "/_system", true)
            .await?;
        let accounts = self.read_accounts().await?;
        self.remove_extra_accounts(&guarded, &accounts).await?;

        for account in accounts {
            let path = self.store.paths().account_manifest(&account)?.0;
            let manifest: PartitionsManifest = match self.store.read_json(&path).await {
                Ok(manifest) => manifest,
                Err(Error::NotFound(_)) => continue,
                Err(error) => {
                    self.store.record_worker_error(MultiWriteWorker::CatchUp);
                    warn!(backend = %self.backend_id, account = %account, error = %error,
                        "multi-write catch-up account metadata unavailable");
                    continue;
                }
            };
            if let Err(error) = manifest.validate() {
                self.store.record_worker_error(MultiWriteWorker::CatchUp);
                warn!(backend = %self.backend_id, account = %account, error = %error,
                    "multi-write catch-up account metadata invalid");
                continue;
            }
            for (&partition_id, partition) in &manifest.partitions {
                if partition.state != PartitionState::Stable {
                    continue;
                }
                let scope = ScopeKey {
                    account_id: account.clone(),
                    partition_id,
                    epoch: manifest.epoch,
                };
                if let Err(failure) = self.reconcile_scope(&guarded, lease, &scope).await {
                    let error = match failure {
                        CatchUpFailure::Cancelled => return Err(CatchUpFailure::Cancelled),
                        CatchUpFailure::Failed(Error::NotFound(_)) => continue,
                        CatchUpFailure::Failed(error) => error,
                    };
                    if !lease.is_valid() {
                        return Err(error.into());
                    }
                    self.store.record_worker_error(MultiWriteWorker::CatchUp);
                    warn!(
                        backend = %self.backend_id,
                        account = %account,
                        partition = partition_id,
                        error = %error,
                        "multi-write catch-up partition failed"
                    );
                }
            }
        }
        Ok(())
    }

    /// Read sorted account ids from the encrypted mount-level registry.
    async fn read_accounts(&self) -> Result<Vec<String>> {
        let bytes = self
            .with_context(SYSTEM_DIR,self.primary.read("/_system/accounts.json", 0, 0))
            .await?;
        let value: Value = serde_json::from_slice(&bytes)?;
        let accounts = value
            .get("accounts")
            .and_then(Value::as_object)
            .ok_or_else(|| Error::Serialization("accounts.json lacks an accounts object".into()))?;
        let mut ids = accounts.keys().cloned().collect::<Vec<_>>();
        ids.sort();
        Ok(ids)
    }

    /// Remove backup account roots that are absent from the primary registry.
    async fn remove_extra_accounts(
        &self,
        backup: &LeaseGuardedFileSystem,
        accounts: &[String],
    ) -> Result<()> {
        let expected = accounts.iter().map(String::as_str).collect::<HashSet<_>>();
        let entries = self
            .with_context(SYSTEM_DIR, backup.read_internal_dir("/"))
            .await?;
        for entry in entries {
            if entry.is_dir && entry.name != SYSTEM_DIR && !expected.contains(entry.name.as_str()) {
                let path = format!("/{}", entry.name);
                self.with_context(&entry.name, backup.remove_all(&path))
                    .await?;
            }
        }
        Ok(())
    }

    /// Consume one fixed `(synced_seq, tail]` window and advance after convergence.
    async fn reconcile_scope(
        &self,
        backup: &LeaseGuardedFileSystem,
        lease: &CatchUpLease,
        scope: &ScopeKey,
    ) -> CatchUpResult<()> {
        let manifest = self.provider.read_manifest(scope).await?;
        let state = manifest
            .backend_states
            .get(&self.backend_id)
            .ok_or_else(|| {
                Error::invalid_operation(format!("unknown backend id: {}", self.backend_id))
            })?;
        let tail = manifest.next_seq - 1;
        debug!(
            backend = %self.backend_id,
            account = %scope.account_id,
            partition = scope.partition_id,
            synced_seq = state.synced_seq,
            tail_seq = tail,
            lag = tail - state.synced_seq,
            "multi-write catch-up partition snapshot"
        );
        if state.synced_seq == tail {
            return Ok(());
        }
        let records = self
            .provider
            .read_committed_head(scope, state.synced_seq + 1, tail + 1)
            .await;
        let records = match records {
            Ok(records) if continuous_records(&records, state.synced_seq + 1, tail + 1) => {
                Some(records)
            }
            _ => self
                .provider
                .read_committed_range(scope, state.synced_seq + 1, tail + 1)
                .await
                .ok()
                .filter(|records| continuous_records(records, state.synced_seq + 1, tail + 1)),
        };
        let Some(records) = records else {
            return self
                .reconcile_checkpoint(backup, lease, scope, state.synced_seq, tail)
                .await;
        };
        let progress = self
            .reconcile_records(backup, lease, scope, state.synced_seq, tail, records)
            .await?;
        self.advance_progress(lease, scope, state.synced_seq, progress)
            .await?;
        Ok(())
    }

    /// Reconcile ordinary paths first, then delayed exact and prefix directory paths.
    async fn reconcile_records(
        &self,
        backup: &LeaseGuardedFileSystem,
        lease: &CatchUpLease,
        scope: &ScopeKey,
        synced_seq: u64,
        tail: u64,
        records: Vec<SegmentRecord>,
    ) -> CatchUpResult<u64> {
        let (mut ordinary, mut delayed_exact, mut prefixes) =
            (BTreeMap::new(), BTreeMap::new(), BTreeMap::new());
        for record in records {
            match record.event_type {
                SegmentEventType::Write | SegmentEventType::Remove => {
                    let path = self
                        .store
                        .paths()
                        .backend_path(&scope.account_id, &record.path)?;
                    insert_earliest(&mut ordinary, path, record.seq);
                }
                SegmentEventType::RemoveTree => {
                    let path = self
                        .store
                        .paths()
                        .backend_path(&scope.account_id, &record.path)?;
                    insert_earliest(&mut prefixes, path, record.seq);
                }
                SegmentEventType::MoveTree => {
                    let source = self
                        .store
                        .paths()
                        .backend_path(&scope.account_id, &record.path)?;
                    let destination = self.store.paths().backend_path(
                        &scope.account_id,
                        record.destination_path.as_deref().ok_or_else(|| {
                            Error::Serialization("move marker lacks destination".to_string())
                        })?,
                    )?;
                    let (exact, dirty) = self
                        .with_context(
                            &scope.account_id,
                            classify_move_paths(backup, &source, &destination),
                        )
                        .await?;
                    for path in exact {
                        insert_earliest(&mut delayed_exact, path, record.seq);
                    }
                    for path in dirty {
                        insert_earliest(&mut prefixes, path, record.seq);
                    }
                }
            }
        }
        let mut failed_from = None;
        for (paths, prefix) in [(ordinary, false), (delayed_exact, false), (prefixes, true)] {
            for (path, seq) in paths {
                match self
                    .reconcile_with_retry(backup, lease, &scope.account_id, &path, prefix)
                    .await
                {
                    Ok(()) => {}
                    Err(CatchUpFailure::Cancelled) => return Err(CatchUpFailure::Cancelled),
                    Err(CatchUpFailure::Failed(_)) if !lease.is_valid() => {
                        return Err(Error::internal("catch-up lease was not refreshed").into());
                    }
                    Err(CatchUpFailure::Failed(_)) => {
                        failed_from = Some(failed_from.map_or(seq, |failed: u64| failed.min(seq)));
                    }
                }
            }
        }
        Ok(failed_from.map_or(tail, |seq| seq.saturating_sub(1).max(synced_seq)))
    }

    /// Refresh the lease immediately before a monotonic progress update.
    async fn advance_progress(
        &self,
        lease: &CatchUpLease,
        scope: &ScopeKey,
        previous: u64,
        progress: u64,
    ) -> Result<()> {
        if progress == previous {
            return Ok(());
        }
        if !lease.refresh().await? {
            return Err(Error::internal("catch-up lease was not refreshed"));
        }
        self.provider
            .advance_backend_state(scope, &self.backend_id, progress)
            .await
    }

    /// Reconcile one path with fixed exponential delays and bounded jitter.
    async fn reconcile_with_retry(
        &self,
        backup: &LeaseGuardedFileSystem,
        lease: &CatchUpLease,
        account_id: &str,
        path: &str,
        prefix: bool,
    ) -> CatchUpResult<()> {
        let mut last_error = None;
        for attempt in 0..=3 {
            let operation = if prefix {
                self.with_context(
                    account_id,
                    reconcile_prefix(self.primary.as_ref(), backup, path),
                )
                .await
            } else {
                self.with_context(
                    account_id,
                    reconcile_file(self.primary.as_ref(), backup, path),
                )
                .await
            };
            match operation {
                Ok(()) => return Ok(()),
                Err(error) => last_error = Some(error),
            }
            if !lease.is_valid() || attempt == 3 {
                break;
            }
            let jitter = rand::thread_rng().gen_range(0..=50);
            let delay_ms = (1u64 << attempt) * 1_000 + jitter;
            warn!(
                backend = %self.backend_id,
                account = %account_id,
                path = %path,
                retry = attempt + 1,
                delay_ms,
                error = %last_error.as_ref().unwrap(),
                "multi-write catch-up path retry"
            );
            tokio::select! {
                _ = tokio::time::sleep(Duration::from_millis(delay_ms)) => {}
                _ = self.cancelled() => return Err(CatchUpFailure::Cancelled),
            }
        }
        Err(CatchUpFailure::Failed(last_error.unwrap()))
    }

    /// Consume one already parsed checkpoint and advance only after full convergence.
    async fn reconcile_checkpoint(
        &self,
        backup: &LeaseGuardedFileSystem,
        lease: &CatchUpLease,
        scope: &ScopeKey,
        synced_seq: u64,
        tail: u64,
    ) -> CatchUpResult<()> {
        let snapshot = match self
            .checkpoint_consumer
            .read_latest(scope, synced_seq, tail)
            .await?
        {
            CheckpointReadResult::Snapshot(snapshot) => snapshot,
            CheckpointReadResult::NoCheckpoint => {
                return Err(Error::invalid_operation("segment window is not continuous").into());
            }
            CheckpointReadResult::RetryLatestCheckpoint => {
                return Err(Error::invalid_operation("retry latest checkpoint").into());
            }
        };
        if snapshot.checkpoint_to_seq <= synced_seq || snapshot.checkpoint_to_seq > tail {
            return Err(Error::Serialization(
                "checkpoint sequence is outside the fixed catch-up window".to_string(),
            )
            .into());
        }
        let mut paths = BTreeSet::new();
        for state in snapshot.file_states {
            state.validate(scope)?;
            if state.latest_seq.seq > snapshot.checkpoint_to_seq {
                return Err(Error::Serialization(
                    "checkpoint file state exceeds checkpoint sequence".to_string(),
                )
                .into());
            }
            paths.insert(
                self.store
                    .paths()
                    .backend_path(&scope.account_id, &state.path)?,
            );
        }
        for path in paths {
            self.reconcile_with_retry(backup, lease, &scope.account_id, &path, false)
                .await?;
        }
        let mut prefixes = BTreeSet::new();
        for prefix in snapshot.directory_prefixes {
            prefixes.insert(
                self.store
                    .paths()
                    .backend_path(&scope.account_id, &prefix)?,
            );
        }
        for prefix in prefixes {
            self.reconcile_with_retry(backup, lease, &scope.account_id, &prefix, true)
                .await?;
        }
        self.advance_progress(lease, scope, synced_seq, snapshot.checkpoint_to_seq)
            .await?;
        if snapshot.checkpoint_to_seq == tail {
            return Ok(());
        }
        let records = self
            .provider
            .read_committed_range(scope, snapshot.checkpoint_to_seq + 1, tail + 1)
            .await?;
        if !continuous_records(&records, snapshot.checkpoint_to_seq + 1, tail + 1) {
            return Err(Error::invalid_operation(
                "post-checkpoint segment window is not continuous",
            )
            .into());
        }
        let progress = self
            .reconcile_records(
                backup,
                lease,
                scope,
                snapshot.checkpoint_to_seq,
                tail,
                records,
            )
            .await?;
        self.advance_progress(lease, scope, snapshot.checkpoint_to_seq, progress)
            .await?;
        Ok(())
    }

    /// Run one filesystem future with cache bypass and automatic PathLock disabled.
    async fn with_context<T>(
        &self,
        account_id: &str,
        future: impl Future<Output = Result<T>>,
    ) -> Result<T> {
        let context: FsContext = Arc::new(
            FsContextInner::new(account_id)
                .with_bypass_cache(true)
                .with_auto_pathlock_disabled(),
        );
        FS_CTX.scope(context, future).await
    }
}

/// Insert one path while retaining its earliest dependent sequence.
fn insert_earliest(paths: &mut BTreeMap<String, u64>, path: String, seq: u64) {
    paths
        .entry(path)
        .and_modify(|current| *current = (*current).min(seq))
        .or_insert(seq);
}

struct LeaseGuardedFileSystem {
    inner: Arc<dyn FileSystem>,
    lease: Arc<CatchUpLease>,
}

impl LeaseGuardedFileSystem {
    /// Reject the next backup mutation unless the lease refreshes literally.
    async fn before_write(&self) -> Result<()> {
        if self.lease.refresh().await? {
            Ok(())
        } else {
            Err(Error::internal("catch-up lease was not refreshed"))
        }
    }
}

#[async_trait]
impl FileSystem for LeaseGuardedFileSystem {
    /// Refresh the catch-up lease before creating a backup file.
    async fn create(&self, path: &str) -> Result<()> {
        self.before_write().await?;
        self.inner.create(path).await
    }

    /// Refresh the catch-up lease before creating a backup directory.
    async fn mkdir(&self, path: &str, mode: u32) -> Result<()> {
        self.before_write().await?;
        self.inner.mkdir(path, mode).await
    }

    /// Refresh the catch-up lease before removing a backup file.
    async fn remove(&self, path: &str) -> Result<()> {
        self.before_write().await?;
        self.inner.remove(path).await
    }

    /// Refresh the catch-up lease before removing a backup subtree.
    async fn remove_all(&self, path: &str) -> Result<()> {
        self.before_write().await?;
        self.inner.remove_all(path).await
    }

    /// Read bytes from the guarded backup without mutating it.
    async fn read(&self, path: &str, offset: u64, size: u64) -> Result<Vec<u8>> {
        self.inner.read(path, offset, size).await
    }

    /// Refresh the catch-up lease before writing backup bytes.
    async fn write(&self, path: &str, data: &[u8], offset: u64, flags: WriteFlag) -> Result<u64> {
        self.before_write().await?;
        self.inner.write(path, data, offset, flags).await
    }

    /// List guarded backup entries without mutating them.
    async fn read_dir(
        &self,
        path: &str,
        offset: Option<usize>,
        limit: Option<usize>,
        sort_by: Option<ListSortBy>,
        sort_order: Option<SortOrder>,
    ) -> Result<Vec<FileInfo>> {
        self.inner
            .read_dir(path, offset, limit, sort_by, sort_order)
            .await
    }

    /// Read guarded backup metadata without mutating it.
    async fn stat(&self, path: &str) -> Result<FileInfo> {
        self.inner.stat(path).await
    }

    /// Refresh the catch-up lease before renaming a backup path.
    async fn rename(&self, old_path: &str, new_path: &str) -> Result<()> {
        self.before_write().await?;
        self.inner.rename(old_path, new_path).await
    }

    /// Refresh the catch-up lease before changing a backup mode.
    async fn chmod(&self, path: &str, mode: u32) -> Result<()> {
        self.before_write().await?;
        self.inner.chmod(path, mode).await
    }
}

/// Return whether records exactly cover one requested half-open sequence range.
fn continuous_records(records: &[SegmentRecord], from: u64, to: u64) -> bool {
    records.len() as u64 == to - from
        && records
            .iter()
            .enumerate()
            .all(|(index, record)| record.seq == from + index as u64)
}

/// Classify move source and destination by the backup's current source type.
pub async fn classify_move_paths(
    backup: &dyn FileSystem,
    source: &str,
    destination: &str,
) -> Result<(HashSet<String>, HashSet<String>)> {
    let paths = HashSet::from([source.to_string(), destination.to_string()]);
    match backup.stat(source).await {
        Ok(info) if !info.is_dir => Ok((paths, HashSet::new())),
        Ok(_) | Err(Error::NotFound(_)) => Ok((HashSet::new(), paths)),
        Err(error) => Err(error),
    }
}

/// Reconcile one file or directory prefix from primary state into a backup.
pub async fn reconcile_prefix(
    primary: &dyn FileSystem,
    backup: &dyn FileSystem,
    prefix: &str,
) -> Result<()> {
    if is_catch_up_internal_path(prefix) {
        return Ok(());
    }
    let root = match primary.stat(prefix).await {
        Ok(root) => root,
        Err(Error::NotFound(_)) => {
            return remove_stale_paths(backup, prefix, &HashSet::new()).await;
        }
        Err(error) => return Err(error),
    };
    if !root.is_dir {
        return copy_file_state(primary, backup, prefix).await;
    }

    let entries = primary
        .tree_directory(prefix, true, None, None, None, None, None, false)
        .await?;
    ensure_directory(backup, prefix, root.mode).await?;
    let mut current = HashSet::from([prefix.to_string()]);
    for entry in entries
        .iter()
        .filter(|entry| !is_catch_up_internal_path(&entry.path))
    {
        current.insert(entry.path.clone());
        if entry.info.is_dir {
            ensure_directory(backup, &entry.path, entry.info.mode).await?;
        } else {
            copy_file_state(primary, backup, &entry.path).await?;
        }
    }

    remove_stale_paths(backup, prefix, &current).await
}

/// Reconcile one exact path from the primary's current state.
pub async fn reconcile_file(
    primary: &dyn FileSystem,
    backup: &dyn FileSystem,
    path: &str,
) -> Result<()> {
    if is_catch_up_internal_path(path) {
        return Ok(());
    }
    match primary.stat(path).await {
        Ok(info) if info.is_dir => reconcile_prefix(primary, backup, path).await,
        Ok(_) => copy_file_state(primary, backup, path).await,
        Err(Error::NotFound(_)) => remove_if_present(backup, path).await,
        Err(error) => Err(error),
    }
}

/// Remove stale business paths while preserving internal files and their ancestors.
async fn remove_stale_paths(
    backup: &dyn FileSystem,
    prefix: &str,
    current: &HashSet<String>,
) -> Result<()> {
    match backup.stat(prefix).await {
        Ok(info) if !info.is_dir => {
            return if current.contains(prefix) || is_multiwrite_internal_path(prefix) {
                Ok(())
            } else {
                remove_if_present(backup, prefix).await
            };
        }
        Ok(_) => {}
        Err(Error::NotFound(_)) => return Ok(()),
        Err(error) => return Err(error),
    }
    let entries = match backup
        .tree_directory(prefix, true, None, None, None, None, None, false)
        .await
    {
        Ok(entries) => entries,
        Err(Error::NotFound(_)) => return Ok(()),
        Err(error) => return Err(error),
    };
    let protected = entries
        .iter()
        .filter(|entry| is_catch_up_internal_path(&entry.path))
        .flat_map(|entry| Path::new(&entry.path).ancestors().skip(1))
        .filter_map(Path::to_str)
        .map(str::to_string)
        .collect::<HashSet<_>>();
    let mut stale = std::iter::once(prefix.to_string())
        .chain(entries.into_iter().map(|entry| entry.path))
        .filter(|path| {
            !is_catch_up_internal_path(path) && !current.contains(path) && !protected.contains(path)
        })
        .collect::<Vec<_>>();
    stale.sort_by_key(|path| std::cmp::Reverse(path.matches('/').count()));
    for path in stale {
        remove_if_present(backup, &path).await?;
    }
    Ok(())
}

/// Recognize internal paths in either logical or mount-relative path space.
fn is_catch_up_internal_path(path: &str) -> bool {
    is_multiwrite_internal_path(path)
        || is_multiwrite_internal_path(&format!("{MULTIWRITE_MOUNT_PREFIX}/{}",path.trim_start_matches('/')))
}

/// Ensure one backup path is a directory with the requested mode.
async fn ensure_directory(backup: &dyn FileSystem, path: &str, mode: u32) -> Result<()> {
    match backup.stat(path).await {
        Ok(info) if info.is_dir => {}
        Ok(_) => {
            backup.remove(path).await?;
            backup.mkdir(path, mode).await?;
        }
        Err(Error::NotFound(_)) => {
            backup.ensure_parent_dirs(path, 0o755).await?;
            backup.mkdir(path, mode).await?;
        }
        Err(error) => return Err(error),
    }
    Ok(())
}

/// Replace one backup file with the primary bytes.
async fn copy_file_state(
    primary: &dyn FileSystem,
    backup: &dyn FileSystem,
    path: &str,
) -> Result<()> {
    if backup.stat(path).await.is_ok_and(|current| current.is_dir) {
        backup.remove_all(path).await?;
    }
    let bytes = match primary.read(path, 0, 0).await {
        Ok(bytes) => bytes,
        Err(Error::NotFound(_)) => return remove_if_present(backup, path).await,
        Err(error) => return Err(error),
    };
    backup.ensure_parent_dirs(path, 0o755).await?;
    backup.write(path, &bytes, 0, WriteFlag::Create).await?;
    Ok(())
}

/// Remove one backup path while accepting an already converged absence.
async fn remove_if_present(backup: &dyn FileSystem, path: &str) -> Result<()> {
    let result = match backup.stat(path).await {
        Ok(info) if info.is_dir => backup.remove_all(path).await,
        Ok(_) => backup.remove(path).await,
        Err(Error::NotFound(_)) => return Ok(()),
        Err(error) => return Err(error),
    };
    match result {
        Ok(()) | Err(Error::NotFound(_)) => Ok(()),
        Err(error) => Err(error),
    }
}
