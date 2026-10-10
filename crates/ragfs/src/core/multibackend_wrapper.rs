//! Primary filesystem wrapper that submits V2 multi-write metadata events.

use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use uuid::Uuid;

use super::context::{FsContext, FS_CTX};
use super::encryption_wrapper::EncryptionWrappedFS;
use super::errors::{Error, Result};
use super::filesystem::{normalize_prefix_path, FileSystem};
use super::types::{
    BackendRole, FileInfo, GlobPage, GrepOptions, GrepResult, ListSortBy, SortOrder, TreeEntry,
    WriteFlag,
};
use crate::core::internal_names::is_multiwrite_internal_path;
use crate::metrics::{RagfsMetric, RagfsMetricValue};
use crate::multibackend::catch_up::CatchUpTarget;
use crate::multibackend::meta::{
    current_required_ctx, DefaultFsContextResolver, FsContextResolver, MetadataStore,
    MultiWriteWorker,
};
use crate::multibackend::migration::{FullDataImporter, ProtocolDetector};
use crate::multibackend::model::{
    PartitionState, PartitionsManifest, PendingEvent, PendingEventKind, ProtocolState,
    ProtocolStatus, ProtocolVersion, SegmentEventType, SegmentManifest,
};
use crate::multibackend::provider::{bootstrap_account, MultiWriteProvider};
use crate::multibackend::runtime::{read_active_accounts, MultiWriteRuntime};

const COPY_CHUNK_SIZE: usize = 8 * 1024 * 1024;

/// One configured primary or backup filesystem.
pub struct BackendEntry {
    /// Logical backend name.
    pub name: String,
    /// Backend role.
    pub role: BackendRole,
    /// Filesystem handle, including encryption when configured.
    pub backend: Arc<dyn FileSystem>,
    /// Optional raw filesystem handle.
    pub raw_backend: Option<Arc<dyn FileSystem>>,
}

pub(crate) struct Inner {
    primary: BackendEntry,
    runtime: MultiWriteRuntime,
    ctx_resolver: Arc<dyn FsContextResolver>,
}

/// Filesystem wrapper that commits primary operations before queuing V2 events.
#[derive(Clone)]
pub struct MultiWriteWrappedFS {
    pub(crate) inner: Arc<Inner>,
    metadata_store: Arc<MetadataStore>,
    metadata_provider: Arc<dyn MultiWriteProvider>,
}

/// Builder for one V2 multi-write wrapper and its flush runtime.
pub struct MultiWriteWrappedFSBuilder {
    primary_backend: Arc<dyn FileSystem>,
    primary_raw_backend: Option<Arc<dyn FileSystem>>,
    backup_entries: Vec<BackendEntry>,
    metadata_store: Option<Arc<MetadataStore>>,
    metadata_provider: Option<Arc<dyn MultiWriteProvider>>,
    initial_partitions: u32,
    checkpoint_interval: Duration,
    ctx_resolver: Arc<dyn FsContextResolver>,
}

impl MultiWriteWrappedFSBuilder {
    /// Attach the raw primary backend used by internal operations and physical copies.
    pub fn with_primary_raw_backend(mut self, primary_raw_backend: Arc<dyn FileSystem>) -> Self {
        self.primary_raw_backend = Some(primary_raw_backend);
        self
    }

    /// Attach configured backup handles for later catch-up workers.
    pub fn with_backups(mut self, backup_entries: Vec<BackendEntry>) -> Self {
        self.backup_entries = backup_entries;
        self
    }

    /// Attach the metadata store used for account initialization and routing.
    pub fn with_metadata_store(mut self, metadata_store: Arc<MetadataStore>) -> Self {
        self.metadata_store = Some(metadata_store);
        self
    }

    /// Attach the provider used by the background flush worker.
    pub fn with_metadata_provider(
        mut self,
        metadata_provider: Arc<dyn MultiWriteProvider>,
    ) -> Self {
        self.metadata_provider = Some(metadata_provider);
        self
    }

    /// Set the initial metadata partition count for newly observed accounts.
    pub fn initial_partitions(mut self, initial_partitions: u32) -> Self {
        self.initial_partitions = initial_partitions;
        self
    }

    /// Set the interval between checkpoint discovery rounds.
    pub fn checkpoint_interval(mut self, checkpoint_interval: Duration) -> Self {
        self.checkpoint_interval = checkpoint_interval;
        self
    }

    /// Configure the resolver for mount-relative foreground paths.
    pub fn ctx_resolver(mut self, ctx_resolver: Arc<dyn FsContextResolver>) -> Self {
        self.ctx_resolver = ctx_resolver;
        self
    }

    /// Build the wrapper after asynchronously starting its V2 runtime.
    pub async fn build(self) -> Result<MultiWriteWrappedFS> {
        let fs = self.build_inactive().await?;
        fs.activate();
        Ok(fs)
    }

    /// Build the wrapper while keeping its V2 runtime inactive.
    pub(crate) async fn build_inactive(self) -> Result<MultiWriteWrappedFS> {
        let metadata_store = self
            .metadata_store
            .ok_or_else(|| Error::config("multi-write metadata store is required"))?;
        let metadata_provider = self
            .metadata_provider
            .ok_or_else(|| Error::config("multi-write metadata provider is required"))?;
        let backup_names: Vec<String> = self
            .backup_entries
            .iter()
            .map(|entry| entry.name.clone())
            .collect();
        let catch_up_targets = self
            .backup_entries
            .iter()
            .map(|entry| CatchUpTarget {
                backend_id: entry.name.clone(),
                backend: entry.backend.clone(),
            })
            .collect();
        let protocol = ProtocolDetector::new(metadata_store.clone())
            .detect()
            .await?;
        let configured_backups = backup_names.iter().collect::<BTreeSet<_>>();
        for account in read_active_accounts(self.primary_backend.as_ref()).await? {
            let path = metadata_store.paths().account_manifest(&account)?.0;
            let manifest: PartitionsManifest = match metadata_store.read_json(&path).await {
                Ok(manifest) => manifest,
                Err(Error::NotFound(_)) => continue,
                Err(error) => return Err(error),
            };
            for &partition_id in manifest.partitions.keys() {
                let path = metadata_store
                    .paths()
                    .segment_manifest(&account, partition_id)?
                    .0;
                let segments: SegmentManifest = metadata_store.read_json(&path).await?;
                segments.validate()?;
                let persisted = segments.backend_states.keys().collect::<BTreeSet<_>>();
                if persisted != configured_backups {
                    return Err(Error::not_supported(format!(
                        "backup membership changed for account {account}"
                    )));
                }
            }
            bootstrap_account(
                metadata_provider.as_ref(),
                metadata_store.as_ref(),
                &account,
                &manifest,
            )
            .await?;
        }
        let runtime = MultiWriteRuntime::prepare(
            metadata_store.clone(),
            metadata_provider.clone(),
            self.initial_partitions,
            backup_names.clone(),
            Some(self.primary_backend.clone()),
            catch_up_targets,
            self.checkpoint_interval,
        )
        .await;
        let primary = BackendEntry {
            name: "primary".to_string(),
            role: BackendRole::Primary,
            backend: self.primary_backend,
            raw_backend: self.primary_raw_backend,
        };
        let fs = MultiWriteWrappedFS {
            inner: Arc::new(Inner {
                primary,
                runtime,
                ctx_resolver: self.ctx_resolver,
            }),
            metadata_store: metadata_store.clone(),
            metadata_provider: metadata_provider.clone(),
        };
        if protocol.status != ProtocolStatus::Stable {
            let importer_primary: Arc<dyn FileSystem> = Arc::new(fs.clone());
            let importer = FullDataImporter::new(
                importer_primary,
                metadata_store,
                metadata_provider,
                self.initial_partitions,
                backup_names,
            );
            fs.inner.runtime.set_import_worker(async move {
                let result = importer.run().await;
                if let Err(error) = &result {
                    tracing::error!(error = %error, "multi-write full-data import failed");
                }
                result
            });
        }
        Ok(fs)
    }
}

impl MultiWriteWrappedFS {
    /// Start building a wrapper around the supplied primary filesystem.
    pub fn builder(primary_backend: Arc<dyn FileSystem>) -> MultiWriteWrappedFSBuilder {
        MultiWriteWrappedFSBuilder {
            primary_backend,
            primary_raw_backend: None,
            backup_entries: Vec::new(),
            metadata_store: None,
            metadata_provider: None,
            initial_partitions: 16,
            checkpoint_interval: Duration::from_secs(86_400),
            ctx_resolver: Arc::new(DefaultFsContextResolver),
        }
    }

    /// Release prepared runtime work after the wrapper becomes reachable.
    pub(crate) fn activate(&self) {
        self.inner.runtime.activate();
    }

    /// Return the raw primary backend for mount-level internal-name operations.
    pub(crate) fn primary_raw_backend(&self) -> Option<Arc<dyn FileSystem>> {
        self.inner.primary.raw_backend.clone()
    }

    /// Return whether the primary encryption wrapper owns PathLock acquisition.
    pub(crate) fn encryption_handles_pathlock(&self) -> bool {
        let any = self.inner.primary.backend.as_ref() as &dyn std::any::Any;
        any.downcast_ref::<EncryptionWrappedFS>().is_some()
    }

    /// Return one while the flush worker is active and zero after it exits.
    pub(crate) fn background_task_count(&self) -> usize {
        usize::from(self.inner.runtime.is_running())
    }

    /// Read and validate one complete V2 multi-write metrics snapshot.
    pub(crate) async fn metrics(&self) -> Result<Vec<RagfsMetric>> {
        let protocol_path = self.metadata_store.paths().mount_protocol().0;
        let protocol: ProtocolState = self.metadata_store.read_json(&protocol_path).await?;
        protocol.validate()?;
        let mut lag = BTreeMap::<(String, u32), u64>::new();
        for account in self.metadata_store.initialized_accounts().await? {
            let path = self.metadata_store.paths().account_manifest(&account)?.0;
            let manifest: PartitionsManifest = self.metadata_store.read_json(&path).await?;
            manifest.validate()?;
            for (&partition_id, entry) in &manifest.partitions {
                if entry.state != PartitionState::Stable {
                    continue;
                }
                let scope = crate::multibackend::model::ScopeKey {
                    account_id: account.clone(),
                    partition_id,
                    epoch: manifest.epoch,
                };
                let segments = self.metadata_provider.read_manifest(&scope).await?;
                segments.validate()?;
                let tail = segments.next_seq - 1;
                for (backend, state) in segments.backend_states {
                    *lag.entry((backend, partition_id)).or_default() += tail - state.synced_seq;
                }
            }
        }
        let mut metrics = vec![
            RagfsMetric {
                name: "ragfs_multiwrite_background_tasks".into(),
                labels: BTreeMap::new(),
                value: RagfsMetricValue::Gauge(self.background_task_count() as f64),
            },
            RagfsMetric {
                name: "ragfs_multiwrite_pending_events".into(),
                labels: BTreeMap::new(),
                value: RagfsMetricValue::Gauge(self.inner.runtime.pending_event_count() as f64),
            },
        ];
        for (worker, label) in [
            (MultiWriteWorker::Flush, "flush"),
            (MultiWriteWorker::Checkpoint, "checkpoint"),
            (MultiWriteWorker::CatchUp, "catch_up"),
            (MultiWriteWorker::Gc, "gc"),
        ] {
            metrics.push(RagfsMetric::counter(
                "ragfs_multiwrite_errors_total",
                &[("worker", label)],
                self.metadata_store.worker_error_count(worker),
                1.0,
            ));
        }
        metrics.push(RagfsMetric {
            name: "ragfs_multiwrite_protocol_version".into(),
            labels: BTreeMap::from([("version".into(), "v2".into())]),
            value: RagfsMetricValue::Gauge(u64::from(
                protocol.protocol_version == ProtocolVersion::V2,
            ) as f64),
        });
        for (status, value) in [
            (
                "migrating",
                u64::from(protocol.status == ProtocolStatus::Migrating),
            ),
            (
                "stable",
                u64::from(protocol.status == ProtocolStatus::Stable),
            ),
        ] {
            metrics.push(RagfsMetric {
                name: "ragfs_multiwrite_protocol_status".into(),
                labels: BTreeMap::from([("status".into(), status.into())]),
                value: RagfsMetricValue::Gauge(value as f64),
            });
        }
        metrics.extend(
            lag.into_iter()
                .map(|((backend, partition), value)| RagfsMetric {
                    name: "ragfs_multiwrite_backend_lag_events".into(),
                    labels: BTreeMap::from([
                        ("backend".into(), backend),
                        ("partition".into(), partition.to_string()),
                    ]),
                    value: RagfsMetricValue::Gauge(value as f64),
                }),
        );
        Ok(metrics)
    }

    /// Resolve one account context and reject disagreement between path and task.
    fn resolve_context(&self, path: &str) -> Result<FsContext> {
        let path_ctx = self.inner.ctx_resolver.resolve(path)?;
        match current_required_ctx() {
            Ok(ctx) if !ctx.account_id().trim().is_empty() => {
                if ctx.account_id() != path_ctx.account_id() {
                    return Err(Error::invalid_path(format!(
                        "path account '{}' conflicts with context account '{}'",
                        path_ctx.account_id(),
                        ctx.account_id()
                    )));
                }
                Ok(ctx)
            }
            _ => Ok(path_ctx),
        }
    }

    /// Build one canonical V2 event from a foreground mount-relative path.
    fn pending_event(
        &self,
        path: &str,
        kind: SegmentEventType,
        destination_path: Option<&str>,
    ) -> Result<Option<PendingEvent>> {
        let ctx = self.resolve_context(path)?;
        let account_id = ctx.account_id().to_string();
        if account_id == "_system" {
            return Ok(None);
        }
        let mut path = logical_path(&account_id, path)?;
        let mut destination_path = destination_path
            .map(|destination| logical_path(&account_id, destination))
            .transpose()?;
        let mut kind = kind;
        let source_internal = is_multiwrite_internal_path(&path);
        let destination_internal = destination_path
            .as_deref()
            .is_some_and(is_multiwrite_internal_path);
        match (kind, source_internal, destination_internal) {
            (SegmentEventType::MoveTree, true, false) => {
                path = destination_path
                    .take()
                    .ok_or_else(|| Error::invalid_operation("move event requires a destination"))?;
                kind = SegmentEventType::RemoveTree;
            }
            (SegmentEventType::MoveTree, false, true) => {
                kind = SegmentEventType::RemoveTree;
                destination_path = None;
            }
            (_, true, _) | (_, _, true) => return Ok(None),
            _ => {}
        }
        Ok(Some(PendingEvent {
            operation_id: Uuid::new_v4(),
            account_id,
            kind: PendingEventKind::Data(kind),
            path,
            destination_path,
            route_hint: None,
        }))
    }

    /// Submit one prebuilt event after its primary operation has succeeded.
    fn submit_event(&self, event: Option<PendingEvent>) {
        if let Some(event) = event {
            self.inner.runtime.submit(event);
        }
    }

    /// Copy raw primary bytes to another primary path and queue one write event.
    pub async fn copy_within_primary(&self, src_path: &str, dst_path: &str) -> Result<bool> {
        if normalize_prefix_path(src_path) == normalize_prefix_path(dst_path) {
            return Ok(true);
        }
        let Some(primary_raw) = self.inner.primary.raw_backend.clone() else {
            return Ok(false);
        };
        if !primary_raw.exists(src_path).await {
            return Ok(false);
        }
        let ctx = self.resolve_context(src_path)?;
        logical_path(ctx.account_id(), dst_path)?;
        let event = self.pending_event(dst_path, SegmentEventType::Write, None)?;
        copy_raw_primary_state(primary_raw, src_path, dst_path, &ctx).await?;
        self.submit_event(event);
        Ok(true)
    }
}

/// Convert a wrapper path into one canonical account-owned logical path.
fn logical_path(account_id: &str, path: &str) -> Result<String> {
    let normalized = normalize_prefix_path(path);
    let logical = if normalized.starts_with('/') {
        format!("/local{normalized}")
    } else {
        format!("/local/{normalized}")
    };
    let account_root = format!("/local/{account_id}");
    if logical != account_root
        && !logical
            .strip_prefix(&account_root)
            .is_some_and(|suffix| suffix.starts_with('/'))
    {
        return Err(Error::invalid_path(format!(
            "path does not belong to account '{account_id}': {logical}"
        )));
    }
    Ok(logical)
}

/// Copy one complete raw file in bounded chunks under the request context.
async fn copy_raw_primary_state(
    primary_raw: Arc<dyn FileSystem>,
    source_path: &str,
    destination_path: &str,
    ctx: &FsContext,
) -> Result<()> {
    FS_CTX
        .scope(ctx.clone(), async {
            let source = primary_raw.stat(source_path).await?;
            if source.is_dir {
                return Err(Error::IsADirectory(source_path.to_string()));
            }
            primary_raw
                .ensure_parent_dirs(destination_path, 0o755)
                .await?;
            if source.size == 0 {
                if primary_raw.exists(destination_path).await {
                    return primary_raw.truncate(destination_path, 0).await;
                }
                return primary_raw.create(destination_path).await;
            }
            let mut offset = 0;
            while offset < source.size {
                let size = (source.size - offset).min(COPY_CHUNK_SIZE as u64);
                let chunk = primary_raw.read(source_path, offset, size).await?;
                if chunk.is_empty() {
                    return Err(Error::internal(format!(
                        "short read while copying raw primary file: {source_path}"
                    )));
                }
                let flag = if offset == 0 {
                    WriteFlag::Create
                } else {
                    WriteFlag::None
                };
                primary_raw
                    .write(destination_path, &chunk, offset, flag)
                    .await?;
                offset += chunk.len() as u64;
            }
            Ok(())
        })
        .await
}

#[async_trait]
impl FileSystem for MultiWriteWrappedFS {
    /// Create a primary file and queue a write event after success.
    async fn create(&self, path: &str) -> Result<()> {
        let event = self.pending_event(path, SegmentEventType::Write, None)?;
        self.inner.primary.backend.create(path).await?;
        self.submit_event(event);
        Ok(())
    }

    /// Create a primary directory without producing a file-state event.
    async fn mkdir(&self, path: &str, mode: u32) -> Result<()> {
        self.inner.primary.backend.mkdir(path, mode).await
    }

    /// Remove a primary file and queue a remove event after success.
    async fn remove(&self, path: &str) -> Result<()> {
        let event = self.pending_event(path, SegmentEventType::Remove, None)?;
        self.inner.primary.backend.remove(path).await?;
        self.submit_event(event);
        Ok(())
    }

    /// Remove a primary tree and queue a directory removal event after success.
    async fn remove_all(&self, path: &str) -> Result<()> {
        let event = self
            .pending_event(path, SegmentEventType::RemoveTree, None)?
            .map(|mut event| {
                if event.path == format!("/local/{}", event.account_id) {
                    event.kind = PendingEventKind::DeleteAccount;
                }
                event
            });
        self.inner.primary.backend.remove_all(path).await?;
        self.submit_event(event);
        Ok(())
    }

    /// Read bytes directly from the primary backend.
    async fn read(&self, path: &str, offset: u64, size: u64) -> Result<Vec<u8>> {
        self.inner.primary.backend.read(path, offset, size).await
    }

    /// Write primary bytes and queue a write event after success.
    async fn write(&self, path: &str, data: &[u8], offset: u64, flags: WriteFlag) -> Result<u64> {
        let event = self.pending_event(path, SegmentEventType::Write, None)?;
        let written = self
            .inner
            .primary
            .backend
            .write(path, data, offset, flags)
            .await?;
        self.submit_event(event);
        Ok(written)
    }

    /// List one directory directly from the primary backend.
    async fn read_dir(
        &self,
        path: &str,
        offset: Option<usize>,
        limit: Option<usize>,
        sort_by: Option<ListSortBy>,
        sort_order: Option<SortOrder>,
    ) -> Result<Vec<FileInfo>> {
        self.inner
            .primary
            .backend
            .read_dir(path, offset, limit, sort_by, sort_order)
            .await
    }

    /// List internal entries directly from the primary backend.
    async fn read_internal_dir(&self, path: &str) -> Result<Vec<FileInfo>> {
        self.inner.primary.backend.read_internal_dir(path).await
    }

    /// Read metadata directly from the primary backend.
    async fn stat(&self, path: &str) -> Result<FileInfo> {
        self.inner.primary.backend.stat(path).await
    }

    /// Rename a primary path and queue one move-tree event after success.
    async fn rename(&self, old_path: &str, new_path: &str) -> Result<()> {
        let event = self.pending_event(old_path, SegmentEventType::MoveTree, Some(new_path))?;
        self.inner
            .primary
            .backend
            .rename(old_path, new_path)
            .await?;
        self.submit_event(event);
        Ok(())
    }

    /// Change primary permissions and queue a write event after success.
    async fn chmod(&self, path: &str, mode: u32) -> Result<()> {
        let event = self.pending_event(path, SegmentEventType::Write, None)?;
        self.inner.primary.backend.chmod(path, mode).await?;
        self.submit_event(event);
        Ok(())
    }

    /// Truncate a primary file and queue a write event after success.
    async fn truncate(&self, path: &str, size: u64) -> Result<()> {
        let event = self.pending_event(path, SegmentEventType::Write, None)?;
        self.inner.primary.backend.truncate(path, size).await?;
        self.submit_event(event);
        Ok(())
    }

    /// Create primary parent directories without producing a file-state event.
    async fn ensure_parent_dirs(&self, path: &str, mode: u32) -> Result<()> {
        self.inner
            .primary
            .backend
            .ensure_parent_dirs(path, mode)
            .await
    }

    /// Search content directly on the primary backend.
    async fn grep(
        &self,
        path: &str,
        pattern: &str,
        options: GrepOptions<'_>,
    ) -> Result<GrepResult> {
        self.inner
            .primary
            .backend
            .grep(path, pattern, options)
            .await
    }

    /// Traverse a tree directly on the primary backend.
    async fn tree_directory(
        &self,
        path: &str,
        show_hidden: bool,
        node_limit: Option<usize>,
        level_limit: Option<usize>,
        offset: Option<usize>,
        sort_by: Option<ListSortBy>,
        sort_order: Option<SortOrder>,
        directories_only: bool,
    ) -> Result<Vec<TreeEntry>> {
        self.inner
            .primary
            .backend
            .tree_directory(
                path,
                show_hidden,
                node_limit,
                level_limit,
                offset,
                sort_by,
                sort_order,
                directories_only,
            )
            .await
    }

    /// Match paths directly on the primary backend.
    async fn glob_directory(
        &self,
        path: &str,
        pattern: &str,
        show_hidden: bool,
        page_size: Option<usize>,
        level_limit: Option<usize>,
        continuation_token: Option<String>,
    ) -> Result<GlobPage> {
        self.inner
            .primary
            .backend
            .glob_directory(
                path,
                pattern,
                show_hidden,
                page_size,
                level_limit,
                continuation_token,
            )
            .await
    }
}
