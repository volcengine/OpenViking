//! Multi-write wrapper — routes operations across primary and backup backends.
//!
//! Implements `MultiWriteWrappedFS` which handles:
//! - Write fanout to primary + backup backends (sync/async)
//! - Read routing with priority-based fallback chain
//! - Redirect policy evaluation
//! - Exclude policy filtering
//! - `.redirect.json` / `.sync_log.json` metadata management

use std::collections::{HashMap, HashSet};
use std::future::Future;
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use regex::Regex;
use serde_json::Value;
use tokio::sync::{Mutex, Notify};

use super::context::{FsContext, FS_CTX};
use super::errors::{Error, Result};
use super::filesystem::{normalize_prefix_path, relative_match_file, FileSystem};
use super::types::{
    BackendRole, BackendSyncState, FileInfo, GrepResult, OperationItemConfig,
    RedirectEntry, RedirectPolicy, SyncLogEntry, SyncOp, SyncType, TreeEntry, WriteFlag,
};
use crate::multibackend::meta::{
    current_required_ctx, file_name, parent_dir, DefaultFsContextResolver, FsContextResolver,
    MetaStateStore, PathSerializer, MULTIWRITE_INTERNAL_NAMES,
};

mod retry;
mod routing;
#[cfg(test)]
mod tests;

/// Default chunk size used when copying file state from primary to backup.
const DEFAULT_COPY_CHUNK_SIZE: usize = 8 * 1024 * 1024;
/// Default retry loop interval.
const DEFAULT_RETRY_INTERVAL: Duration = Duration::from_secs(30);
/// Default retry backoff base in milliseconds.
const DEFAULT_RETRY_BACKOFF_BASE_MS: u64 = 1000;
/// Default maximum retries per file per retry round.
const DEFAULT_MAX_RETRIES_PER_ROUND: usize = 3;
/// Default failure threshold before a target is quarantined.
const DEFAULT_QUARANTINE_AFTER_FAILURES: u32 = 9;
/// Default wait timeout when shutting down background tasks.
const DEFAULT_SHUTDOWN_WAIT: Duration = Duration::from_secs(5);

#[derive(Debug)]
struct SyncFanoutTaskResult {
    backend: String,
    result: Result<()>,
}

#[derive(Clone)]
enum BackupWriteOp {
    Replay(SyncOp),
    WriteFile {
        data: Arc<Vec<u8>>,
        offset: u64,
        flags: WriteFlag,
    },
    EnsureParentDirs {
        mode: u32,
    },
}

impl BackupWriteOp {
    /// Apply this backup-side operation under the provided request context.
    async fn apply(
        &self,
        inner: &Inner,
        backup: Arc<dyn FileSystem>,
        path: &str,
        ctx: &FsContext,
    ) -> Result<()> {
        match self {
            Self::Replay(op) => {
                op.replay(inner.primary().backend.clone(), backup, path, ctx)
                    .await
            }
            Self::WriteFile {
                data,
                offset,
                flags,
            } => {
                let data = Arc::clone(data);
                let offset = *offset;
                let flags = *flags;
                FS_CTX
                    .scope(ctx.clone(), async move {
                        backup.ensure_parent_dirs(path, 0o755).await?;
                        backup.write(path, data.as_slice(), offset, flags).await?;
                        Ok(())
                    })
                    .await
            }
            Self::EnsureParentDirs { mode } => {
                let mode = *mode;
                FS_CTX
                    .scope(ctx.clone(), async move {
                        backup.ensure_parent_dirs(path, mode).await
                    })
                    .await
            }
        }
    }
}

/// Cloned target data passed to fanout strategies without borrowing `Inner`.
#[derive(Clone)]
struct FanoutTarget {
    name: String,
    backend: Arc<dyn FileSystem>,
}

/// Effective sync work item with resolved target backend names.
pub(crate) struct SyncWorkEntry {
    pub(crate) file_path: String,
    pub(crate) entry: SyncLogEntry,
    pub(crate) targets: Vec<String>,
}

#[derive(Clone, Copy)]
enum ReadRouteSource {
    Backup,
    Primary,
    Redirect,
    Miss,
}

/// Builder-style sync mode configuration.
pub enum SyncMode {
    /// Synchronous fanout requiring backup acknowledgement.
    Sync {
        /// Minimum backup acknowledgements required for a successful write.
        ack_count: usize,
        /// Maximum time to wait for backup acknowledgements, in milliseconds.
        timeout_ms: u64,
    },
    /// Asynchronous fanout with background retry.
    Async,
}

/// A backend entry within the multi-write wrapper.
pub struct BackendEntry {
    /// Logical name (globally unique)
    pub name: String,
    /// Role: Primary or Backup
    pub role: BackendRole,
    /// The backend filesystem handle (may be encrypted)
    pub backend: Arc<dyn FileSystem>,
    /// Optional raw backend handle used by primary verbatim copy fast-paths.
    pub raw_backend: Option<Arc<dyn FileSystem>>,
    /// Operations this backend participates in (only for Backup)
    pub operations: Vec<OperationItemConfig>,
    /// Exclude policies (only for Backup)
    pub excludes: Vec<RedirectPolicy>,
}

impl BackendEntry {
    /// Check if this backend participates in read operations.
    fn participates_in_read(&self) -> bool {
        self.operations.iter().any(|op| op.operation == "read")
    }

    /// Check if this backend participates in write operations.
    /// Backups default to write-enabled when operations is empty.
    fn participates_in_write(&self) -> bool {
        if self.operations.is_empty() {
            true
        } else {
            self.operations.iter().any(|op| op.operation == "write")
        }
    }

    /// Get read priority (lower = higher priority). Returns None if not read-enabled.
    fn read_priority(&self) -> Option<u32> {
        self.operations
            .iter()
            .find(|op| op.operation == "read")
            .map(|op| op.priority)
    }

    /// Convert this backend entry into a fanout target.
    fn fanout_target(&self) -> FanoutTarget {
        FanoutTarget {
            name: self.name.clone(),
            backend: self.backend.clone(),
        }
    }
}

/// File policy trait — shared by redirects and excludes.
pub trait FilePolicy {
    /// Check if this policy matches the given file.
    fn matches(&self, path: &str, size: u64) -> bool;
}

impl FilePolicy for RedirectPolicy {
    fn matches(&self, path: &str, size: u64) -> bool {
        match self {
            RedirectPolicy::FileOverSizePolicy { max_size_mb, .. } => {
                let max_bytes = max_size_mb * 1024 * 1024;
                size > max_bytes
            }
            RedirectPolicy::FileExtensionPolicy { extensions, .. } => {
                let name = file_name(path);
                extensions.iter().any(|ext_pattern| {
                    if let Ok(re) = Regex::new(ext_pattern) {
                        re.is_match(name)
                    } else {
                        name.ends_with(ext_pattern.as_str())
                    }
                })
            }
        }
    }
}

impl SyncOp {
    /// Replay this operation on one backup backend using the original request semantics.
    async fn replay(
        &self,
        primary: Arc<dyn FileSystem>,
        backup: Arc<dyn FileSystem>,
        file_path: &str,
        ctx: &FsContext,
    ) -> Result<()> {
        match self {
            SyncOp::SyncFile { size } => {
                let size = *size;
                copy_current_primary_state(primary, backup, file_path, size, ctx).await
            }
            SyncOp::Create => {
                FS_CTX
                    .scope(ctx.clone(), async { backup.create(file_path).await })
                    .await
            }
            SyncOp::Mkdir { mode } => {
                let mode = *mode;
                FS_CTX
                    .scope(ctx.clone(), async { backup.mkdir(file_path, mode).await })
                    .await
            }
            SyncOp::Remove => {
                match FS_CTX
                    .scope(ctx.clone(), async { backup.remove(file_path).await })
                    .await
                {
                    Ok(()) | Err(Error::NotFound(_)) => Ok(()),
                    Err(e) => Err(e),
                }
            }
            SyncOp::RemoveAll => {
                match FS_CTX
                    .scope(ctx.clone(), async { backup.remove_all(file_path).await })
                    .await
                {
                    Ok(()) | Err(Error::NotFound(_)) => Ok(()),
                    Err(e) => Err(e),
                }
            }
            SyncOp::Rename { to } => {
                let to = to.clone();
                FS_CTX
                    .scope(ctx.clone(), async {
                        backup.ensure_parent_dirs(&to, 0o755).await?;
                        backup.rename(file_path, &to).await
                    })
                    .await
            }
            SyncOp::Chmod { mode } => {
                let mode = *mode;
                FS_CTX
                    .scope(ctx.clone(), async { backup.chmod(file_path, mode).await })
                    .await
            }
        }
    }
}

/// Copy one file between two filesystem handles in bounded-size chunks.
async fn copy_file_state(
    source: Arc<dyn FileSystem>,
    source_path: &str,
    destination: Arc<dyn FileSystem>,
    destination_path: &str,
    size: u64,
    ctx: &FsContext,
) -> Result<()> {
    FS_CTX
        .scope(ctx.clone(), async {
            destination
                .ensure_parent_dirs(destination_path, 0o755)
                .await?;
            if size == 0 {
                if destination.exists(destination_path).await {
                    return destination.truncate(destination_path, 0).await;
                }
                return destination.create(destination_path).await;
            }

            let mut offset = 0u64;
            while offset < size {
                let chunk_len = (size - offset).min(DEFAULT_COPY_CHUNK_SIZE as u64);
                let chunk = source.read(source_path, offset, chunk_len).await?;
                if chunk.is_empty() {
                    if offset == 0 {
                        if destination.exists(destination_path).await {
                            destination.truncate(destination_path, 0).await?;
                        } else {
                            destination.create(destination_path).await?;
                        }
                    }
                    break;
                }
                let flag = if offset == 0 {
                    WriteFlag::Create
                } else {
                    WriteFlag::None
                };
                destination
                    .write(destination_path, &chunk, offset, flag)
                    .await?;
                offset = offset.saturating_add(chunk.len() as u64);
            }
            Ok(())
        })
        .await
}

/// Copy the current file state from primary to backup in bounded-size chunks.
async fn copy_current_primary_state(
    primary: Arc<dyn FileSystem>,
    backup: Arc<dyn FileSystem>,
    file_path: &str,
    size: u64,
    ctx: &FsContext,
) -> Result<()> {
    copy_file_state(primary, file_path, backup, file_path, size, ctx).await
}

/// Copy one file within the primary raw backend without decrypting and re-encrypting bytes.
async fn copy_raw_primary_state(
    primary_raw: Arc<dyn FileSystem>,
    source_path: &str,
    destination_path: &str,
    ctx: &FsContext,
) -> Result<()> {
    let raw_size = FS_CTX
        .scope(ctx.clone(), async {
            let source_info = primary_raw.stat(source_path).await?;
            if source_info.is_dir {
                return Err(Error::IsADirectory(source_path.to_string()));
            }
            Ok::<u64, Error>(source_info.size)
        })
        .await?;

    copy_file_state(
        primary_raw.clone(),
        source_path,
        primary_raw,
        destination_path,
        raw_size,
        ctx,
    )
    .await
}

/// Inner state shared via Arc for async spawn and retry_loop.
pub(crate) struct Inner {
    /// All backend entries (primary at index 0)
    backends: Vec<BackendEntry>,
    /// Index of the primary backend
    primary_idx: usize,
    /// Sync type: Async or Sync.
    sync_type: SyncType,
    /// Minimum backup ack count for sync mode
    write_ack_count: usize,
    /// Timeout for waiting backup ack in sync mode (ms)
    write_ack_timeout_ms: u64,
    /// Semaphore for async write concurrency control
    write_sem: Option<Arc<tokio::sync::Semaphore>>,
    /// Primary redirect policies
    redirects: Vec<RedirectPolicy>,
    /// Metadata store (encrypted via primary_backend)
    pub(crate) meta_store: MetaStateStore,
    /// Per-path serialization queues
    path_queues: PathSerializer,
    /// Directories that currently have outstanding retry work.
    pending_dirs: Mutex<HashSet<String>>,
    /// Retry loop interval.
    retry_interval: Duration,
    /// Base retry backoff in milliseconds.
    pub(crate) retry_backoff_base_ms: u64,
    /// Maximum retry attempts for one target in one round.
    pub(crate) max_retry_per_round: usize,
    /// Failure threshold before quarantining one target.
    quarantine_after_failures: u32,
    /// Number of background tasks currently in flight.
    background_tasks: AtomicUsize,
    /// Notifier fired when background task count reaches zero.
    idle_notify: Notify,
    /// Read route hit metrics.
    read_backup_hits: AtomicU64,
    read_primary_hits: AtomicU64,
    read_redirect_hits: AtomicU64,
    read_misses: AtomicU64,
    /// Cancellation flag for the background retry loop.
    retry_cancelled: AtomicBool,
    /// Wake-up signal used to stop retry_loop promptly on drop.
    retry_shutdown: Notify,
}

/// Multi-write wrapped filesystem.
pub struct MultiWriteWrappedFS {
    pub(crate) inner: Arc<Inner>,
}

/// Builder for `MultiWriteWrappedFS`.
pub struct MultiWriteWrappedFSBuilder {
    primary_backend: Arc<dyn FileSystem>,
    primary_raw_backend: Option<Arc<dyn FileSystem>>,
    backup_entries: Vec<BackendEntry>,
    redirects: Vec<RedirectPolicy>,
    sync_mode: SyncMode,
    write_concurrency: Option<usize>,
    retry_interval: Duration,
    retry_backoff_base_ms: u64,
    max_retry_per_round: usize,
    quarantine_after_failures: u32,
    ctx_resolver: Arc<dyn FsContextResolver>,
}

impl MultiWriteWrappedFSBuilder {
    /// Attach the raw primary backend so the wrapper can do verbatim primary copies.
    pub fn with_primary_raw_backend(mut self, primary_raw_backend: Arc<dyn FileSystem>) -> Self {
        self.primary_raw_backend = Some(primary_raw_backend);
        self
    }

    /// Set backup backend entries on the builder.
    pub fn with_backups(mut self, backup_entries: Vec<BackendEntry>) -> Self {
        self.backup_entries = backup_entries;
        self
    }

    /// Set redirect policies for the primary backend.
    pub fn with_redirects(mut self, redirects: Vec<RedirectPolicy>) -> Self {
        self.redirects = redirects;
        self
    }

    /// Select the sync mode used by write fanout.
    pub fn sync_mode(mut self, sync_mode: SyncMode) -> Self {
        self.sync_mode = sync_mode;
        self
    }

    /// Set the maximum number of concurrent async backup writes.
    pub fn write_concurrency(mut self, write_concurrency: Option<usize>) -> Self {
        self.write_concurrency = write_concurrency;
        self
    }

    /// Configure retry loop interval.
    pub fn retry_interval(mut self, retry_interval: Duration) -> Self {
        self.retry_interval = retry_interval;
        self
    }

    /// Configure retry backoff base duration in milliseconds.
    pub fn retry_backoff_base_ms(mut self, retry_backoff_base_ms: u64) -> Self {
        self.retry_backoff_base_ms = retry_backoff_base_ms;
        self
    }

    /// Configure the maximum number of retries per round.
    pub fn max_retry_per_round(mut self, max_retry_per_round: usize) -> Self {
        self.max_retry_per_round = max_retry_per_round.max(1);
        self
    }

    /// Configure quarantine threshold for one path/backup pair.
    pub fn quarantine_after_failures(mut self, quarantine_after_failures: u32) -> Self {
        self.quarantine_after_failures = quarantine_after_failures.max(1);
        self
    }

    /// Accept the legacy read-route cache TTL option as a no-op.
    pub fn read_route_cache_ttl(self, _read_route_cache_ttl: Duration) -> Self {
        self
    }

    /// Configure the context resolver used by retry and admin background paths.
    pub fn ctx_resolver(mut self, ctx_resolver: Arc<dyn FsContextResolver>) -> Self {
        self.ctx_resolver = ctx_resolver;
        self
    }

    /// Build the multi-write wrapper and start the retry loop when needed.
    pub fn build(self) -> Result<MultiWriteWrappedFS> {
        let mut backends = Vec::new();
        backends.push(BackendEntry {
            name: "primary".to_string(),
            role: BackendRole::Primary,
            backend: self.primary_backend.clone(),
            raw_backend: self.primary_raw_backend.clone(),
            operations: Vec::new(),
            excludes: Vec::new(),
        });
        backends.extend(self.backup_entries);

        let (sync_type, write_ack_count, write_ack_timeout_ms) = match self.sync_mode {
            SyncMode::Sync {
                ack_count,
                timeout_ms,
            } => (SyncType::Sync, ack_count, timeout_ms),
            SyncMode::Async => (SyncType::Async, usize::MAX, 0),
        };

        let write_sem = self
            .write_concurrency
            .filter(|&n| n > 0)
            .map(|n| Arc::new(tokio::sync::Semaphore::new(n)));

        let meta_store = MetaStateStore::new(self.primary_backend, self.ctx_resolver);

        let inner = Arc::new(Inner {
            backends,
            primary_idx: 0,
            sync_type,
            write_ack_count,
            write_ack_timeout_ms,
            write_sem,
            redirects: self.redirects,
            meta_store,
            path_queues: PathSerializer::new(),
            pending_dirs: Mutex::new(HashSet::new()),
            retry_interval: self.retry_interval,
            retry_backoff_base_ms: self.retry_backoff_base_ms,
            max_retry_per_round: self.max_retry_per_round,
            quarantine_after_failures: self.quarantine_after_failures,
            background_tasks: AtomicUsize::new(0),
            idle_notify: Notify::new(),
            read_backup_hits: AtomicU64::new(0),
            read_primary_hits: AtomicU64::new(0),
            read_redirect_hits: AtomicU64::new(0),
            read_misses: AtomicU64::new(0),
            retry_cancelled: AtomicBool::new(false),
            retry_shutdown: Notify::new(),
        });

        // Start retry_loop if there are write-enabled backups.
        if inner.write_backups().next().is_some() {
            inner.background_task_started();
            tokio::spawn(Inner::retry_loop(Arc::clone(&inner)));
        }

        Ok(MultiWriteWrappedFS { inner })
    }
}

impl MultiWriteWrappedFS {
    /// Start building a multi-write wrapper from a primary backend.
    pub fn builder(primary_backend: Arc<dyn FileSystem>) -> MultiWriteWrappedFSBuilder {
        MultiWriteWrappedFSBuilder {
            primary_backend,
            primary_raw_backend: None,
            backup_entries: Vec::new(),
            redirects: Vec::new(),
            sync_mode: SyncMode::Async,
            write_concurrency: None,
            retry_interval: DEFAULT_RETRY_INTERVAL,
            retry_backoff_base_ms: DEFAULT_RETRY_BACKOFF_BASE_MS,
            max_retry_per_round: DEFAULT_MAX_RETRIES_PER_ROUND,
            quarantine_after_failures: DEFAULT_QUARANTINE_AFTER_FAILURES,
            ctx_resolver: Arc::new(DefaultFsContextResolver),
        }
    }
}

impl Inner {
    /// Build the per-path/per-backend queue key used by both fanout and retry.
    fn backup_queue_key(path: &str, backup_name: &str) -> String {
        format!("{}\0{}", path, backup_name)
    }

    /// Iterate over write-enabled backup entries.
    fn write_backups(&self) -> impl Iterator<Item = &BackendEntry> {
        self.backends[self.primary_idx + 1..]
            .iter()
            .filter(|be| be.participates_in_write())
    }

    /// Resolve write-enabled backup targets after applying exclude policies.
    fn write_targets(&self, path: &str, size: u64) -> Vec<FanoutTarget> {
        self.write_backups()
            .filter(|be| !self.is_excluded(be, path, size))
            .map(BackendEntry::fanout_target)
            .collect()
    }

    /// Resolve explicitly named backup targets.
    fn named_targets(&self, target_names: &[String]) -> Vec<FanoutTarget> {
        target_names
            .iter()
            .filter_map(|name| self.backup_by_name(name))
            .map(BackendEntry::fanout_target)
            .collect()
    }

    /// Execute one backup write synchronously so redirect visibility only appears
    /// after at least one target has durably materialized the file contents.
    async fn write_first_target(
        inner: &Arc<Inner>,
        path: &str,
        target: &FanoutTarget,
        ctx: &FsContext,
        op: BackupWriteOp,
    ) -> Result<()> {
        let queue_key = Self::backup_queue_key(path, &target.name);
        let fs = target.backend.clone();
        let ack_ctx = ctx.clone();

        inner
            .path_queues
            .with_path_lock(&queue_key, || async move {
                op.apply(inner, fs, path, ctx).await
            })
            .await?;

        inner
            .update_backup_acked_seq(path, &target.name, &ack_ctx)
            .await
    }

    /// Resolve effective target backend names for sync/retry work.
    pub(crate) fn target_backend_names(
        &self,
        redirect_meta: &super::types::RedirectMeta,
        file_name: &str,
        file_path: &str,
        sync_entry: &SyncLogEntry,
    ) -> Vec<String> {
        if let Some(redir) = redirect_meta.entries.get(file_name) {
            return redir.targets.clone();
        }
        let policy_size = self.retry_policy_size(sync_entry);
        self.write_backups()
            .filter(|be| !self.is_excluded(be, file_path, policy_size))
            .map(|be| be.name.clone())
            .collect()
    }

    /// Iterate over read-enabled backup entries sorted by priority.
    fn read_backups_sorted(&self) -> Vec<&BackendEntry> {
        let mut read_backups: Vec<&BackendEntry> = self.backends[self.primary_idx + 1..]
            .iter()
            .filter(|be| be.participates_in_read())
            .collect();
        read_backups.sort_by_key(|be| be.read_priority().unwrap_or(u32::MAX));
        read_backups
    }

    /// Get the primary backend entry.
    pub(crate) fn primary(&self) -> &BackendEntry {
        &self.backends[self.primary_idx]
    }

    /// Get a backup entry by name.
    fn backup_by_name(&self, name: &str) -> Option<&BackendEntry> {
        self.backends.iter().find(|be| be.name == name)
    }

    /// Check if a file should be excluded from a backup.
    fn is_excluded(&self, backup: &BackendEntry, path: &str, size: u64) -> bool {
        backup
            .excludes
            .iter()
            .any(|policy| policy.matches(path, size))
    }

    /// Check if a file matches any redirect policy.
    fn check_redirect(&self, path: &str, size: u64) -> Option<Vec<String>> {
        for policy in &self.redirects {
            if policy.matches(path, size) {
                let targets = match policy {
                    RedirectPolicy::FileOverSizePolicy { target, .. } => target.clone(),
                    RedirectPolicy::FileExtensionPolicy { target, .. } => target.clone(),
                };
                return targets;
            }
        }
        None
    }

    /// Resolve persisted redirect targets for one already-written file path.
    async fn redirect_targets_for_path(
        &self,
        path: &str,
        ctx: &FsContext,
    ) -> Result<Option<Vec<String>>> {
        let dir = parent_dir(path);
        let name = file_name(path);
        let redirect_meta = self.meta_store.get_redirect_meta(&dir, ctx).await?;
        Ok(redirect_meta
            .entries
            .get(name)
            .map(|entry| entry.targets.clone()))
    }

    /// Generate and persist the next sequence number.
    async fn next_seq(&self) -> Result<u64> {
        self.meta_store.next_seq().await
    }

    /// Resolve a file size for retry-time policy decisions.
    fn retry_policy_size(&self, sync_entry: &SyncLogEntry) -> u64 {
        match &sync_entry.op {
            SyncOp::SyncFile { size } => *size,
            _ => 0,
        }
    }

    /// Execute the primary-write, sync-log and backup-fanout pipeline.
    async fn execute_write<R, P, PFut>(
        inner: &Arc<Self>,
        path: String,
        size: u64,
        sync_op: Option<SyncOp>,
        backup_op: Option<BackupWriteOp>,
        primary_fn: P,
    ) -> Result<R>
    where
        P: FnOnce(Arc<dyn FileSystem>) -> PFut + Send,
        PFut: Future<Output = Result<R>> + Send,
    {
        let ctx = current_required_ctx()?;
        inner.invalidate_read_route(&path).await;

        let prepared_entry = if let Some(entry) = sync_op {
            let dir = parent_dir(&path);
            let name = file_name(&path).to_string();
            let seq = inner.next_seq().await?;
            inner
                .meta_store
                .update_dir_meta(&dir, &ctx, {
                    let name = name.clone();
                    move |_redirect, sync_log| {
                        sync_log.entries.insert(name, SyncLogEntry::new(seq, entry));
                        Ok(())
                    }
                })
                .await?;
            Some((dir, name, seq))
        } else {
            None
        };

        let result = match FS_CTX
            .scope(ctx.clone(), primary_fn(inner.primary().backend.clone()))
            .await
        {
            Ok(result) => result,
            Err(err) => {
                if let Some((dir, name, seq)) = prepared_entry.as_ref() {
                    let _ = inner
                        .meta_store
                        .update_dir_meta(dir, &ctx, {
                            let name = name.clone();
                            let seq = *seq;
                            move |_redirect, sync_log| {
                                let should_remove =
                                    sync_log.entries.get(&name).is_some_and(|entry| {
                                        entry.latest_seq == seq && !entry.is_primary_committed()
                                    });
                                if should_remove {
                                    sync_log.entries.remove(&name);
                                }
                                Ok(())
                            }
                        })
                        .await;
                }
                return Err(err);
            }
        };

        if let Some((dir, name, seq)) = prepared_entry {
            inner
                .meta_store
                .update_dir_meta(&dir, &ctx, move |_redirect, sync_log| {
                    let entry = sync_log.entries.get_mut(&name).ok_or_else(|| {
                        Error::internal(format!(
                            "prepared sync log entry missing while committing '{}'",
                            name
                        ))
                    })?;
                    if entry.latest_seq != seq {
                        return Err(Error::internal(format!(
                            "prepared sync log entry seq mismatch while committing '{}'",
                            name
                        )));
                    }
                    entry.mark_primary_committed();
                    Ok(())
                })
                .await?;
            inner.mark_pending_dir(&dir).await;
        }

        let dir = parent_dir(&path);
        let fanout_result = if let Some(backup_op) = backup_op {
            Inner::fanout_write(inner, &path, size, ctx.clone(), backup_op).await
        } else {
            Ok(())
        };
        inner.refresh_pending_dir(&dir, &ctx).await?;
        fanout_result?;
        Ok(result)
    }

    /// Execute a write that may be redirected away from the primary backend.
    async fn execute_write_with_redirect<P, PFut>(
        inner: &Arc<Self>,
        path: String,
        size: u64,
        sync_op: SyncOp,
        backup_op: BackupWriteOp,
        primary_fn: P,
    ) -> Result<u64>
    where
        P: FnOnce(Arc<dyn FileSystem>) -> PFut + Send,
        PFut: Future<Output = Result<u64>> + Send,
    {
        let ctx = current_required_ctx()?;
        inner.invalidate_read_route(&path).await;

        if let Some(targets) = inner.check_redirect(&path, size) {
            let dir = parent_dir(&path);
            let name = file_name(&path).to_string();
            let seq = inner.next_seq().await?;
            let resolved_targets = inner.named_targets(&targets);
            let first_target = resolved_targets.first().cloned().ok_or_else(|| {
                Error::internal(format!(
                    "redirect path '{}' resolved no writable targets",
                    path
                ))
            })?;
            Inner::write_first_target(inner, &path, &first_target, &ctx, backup_op.clone()).await?;
            let targets_clone = targets.clone();
            let first_target_name = first_target.name.clone();
            inner
                .meta_store
                .update_dir_meta(&dir, &ctx, move |redirect, sync_log| {
                    redirect.entries.insert(
                        name.clone(),
                        RedirectEntry {
                            targets: targets_clone.clone(),
                        },
                    );
                    let mut committed = SyncLogEntry::committed(seq, sync_op);
                    committed
                        .backends
                        .insert(first_target_name.clone(), BackendSyncState::acked(seq));
                    sync_log.entries.insert(name, committed);
                    Ok(())
                })
                .await?;

            let remaining_targets: Vec<FanoutTarget> =
                resolved_targets.into_iter().skip(1).collect();
            if !remaining_targets.is_empty() {
                inner.mark_pending_dir(&dir).await;
                Inner::fanout_async(inner, &path, remaining_targets, &ctx, backup_op).await;
            }
            inner.refresh_pending_dir(&dir, &ctx).await?;
            return Ok(size);
        }

        Inner::execute_write(
            inner,
            path,
            size,
            Some(sync_op),
            Some(backup_op),
            primary_fn,
        )
        .await
    }

    /// Fanout a write operation to all write-enabled backups.
    /// Takes `&Arc<Inner>` so spawned tasks can clone the Arc for acked_seq updates.
    /// `ctx` is required for encrypted backup backends and acked_seq updates.
    async fn fanout_write(
        inner: &Arc<Inner>,
        path: &str,
        size: u64,
        ctx: FsContext,
        op: BackupWriteOp,
    ) -> Result<()> {
        Inner::fanout_targets(inner, path, inner.write_targets(path, size), ctx, op).await
    }

    /// Fanout a write operation to explicitly named backup targets (used by redirect path).
    /// Resolves names to BackendEntry references, then delegates to sync/async state machine.
    async fn fanout_write_to_targets(
        inner: &Arc<Inner>,
        path: &str,
        target_names: &[String],
        ctx: FsContext,
        op: BackupWriteOp,
    ) -> Result<()> {
        Inner::fanout_targets(inner, path, inner.named_targets(target_names), ctx, op).await
    }

    /// Fanout to already resolved targets using the configured sync mode.
    async fn fanout_targets(
        inner: &Arc<Inner>,
        path: &str,
        targets: Vec<FanoutTarget>,
        ctx: FsContext,
        op: BackupWriteOp,
    ) -> Result<()> {
        if targets.is_empty() {
            if matches!(inner.sync_type, SyncType::Sync)
                && inner.write_ack_count > 0
                && inner.write_backups().next().is_some()
            {
                return Err(Error::SyncWriteQuorum {
                    succeeded: 0,
                    required: inner.write_ack_count,
                    attempted: 0,
                    failures: Vec::new(),
                });
            }
            return Ok(());
        }

        match inner.sync_type {
            SyncType::Sync => Inner::fanout_sync(inner, path, &targets, &ctx, op).await,
            SyncType::Async => {
                Inner::fanout_async(inner, path, targets, &ctx, op).await;
                Ok(())
            }
        }
    }

    /// Synchronous fanout: execute writes in parallel, wait for quorum.
    async fn fanout_sync(
        inner: &Arc<Inner>,
        path: &str,
        targets: &[FanoutTarget],
        ctx: &FsContext,
        op: BackupWriteOp,
    ) -> Result<()> {
        let ack_count = inner.write_ack_count.min(targets.len());
        let timeout = if inner.write_ack_timeout_ms > 0 {
            Some(Duration::from_millis(inner.write_ack_timeout_ms))
        } else {
            None
        };

        let path_owned = path.to_string();
        let ctx = Some(ctx.clone());

        // Launch parallel tasks for all backup writes.
        let mut handles = Vec::new();
        for target in targets {
            let fs = target.backend.clone();
            let name = target.name.clone();
            let path = path_owned.clone();
            let inner = Arc::clone(inner);
            let ctx = ctx.clone();
            let op_clone = op.clone();
            let queue_key = Self::backup_queue_key(&path, &name);

            handles.push(tokio::spawn(async move {
                // Wrap in FS_CTX.scope so encrypted backends can access account_id.
                let exec = inner.path_queues.with_path_lock(&queue_key, || async {
                    op_clone
                        .apply(&inner, fs, &path, ctx.as_ref().unwrap())
                        .await
                });

                let result = if let Some(timeout) = timeout {
                    match tokio::time::timeout(timeout, exec).await {
                        Ok(Ok(())) => Ok(()),
                        Ok(Err(e)) => Err(e),
                        Err(_) => Err(Error::timeout(format!(
                            "backup '{}' timed out waiting for sync acknowledgement",
                            name
                        ))),
                    }
                } else {
                    exec.await
                };

                // Update acked_seq on success.
                if result.is_ok() {
                    if let Some(ref ctx) = ctx {
                        let _ = inner.update_backup_acked_seq(&path, &name, ctx).await;
                    }
                }

                SyncFanoutTaskResult {
                    backend: name,
                    result,
                }
            }));
        }

        let results = futures::future::join_all(handles).await;

        let mut successes = 0usize;
        let mut failures = Vec::new();

        for result in results {
            match result {
                Ok(SyncFanoutTaskResult {
                    backend: _,
                    result: Ok(()),
                }) => {
                    successes += 1;
                }
                Ok(SyncFanoutTaskResult {
                    backend,
                    result: Err(e),
                }) => {
                    failures.push(super::errors::SyncWriteFailureDetail {
                        backend,
                        kind: e.kind_name().to_string(),
                        message: e.to_string(),
                    });
                }
                Err(e) => {
                    failures.push(super::errors::SyncWriteFailureDetail {
                        backend: "<spawn>".to_string(),
                        kind: "join_error".to_string(),
                        message: e.to_string(),
                    });
                }
            }
        }

        if successes >= ack_count {
            Ok(())
        } else {
            Err(Error::SyncWriteQuorum {
                succeeded: successes,
                required: ack_count,
                attempted: targets.len(),
                failures,
            })
        }
    }

    /// Asynchronous fanout: spawn background tasks that update acked_seq on completion.
    /// Uses per-path serialization to prevent out-of-order application on backup backends.
    async fn fanout_async(
        inner: &Arc<Inner>,
        path: &str,
        targets: Vec<FanoutTarget>,
        ctx: &FsContext,
        op: BackupWriteOp,
    ) {
        let path_owned = path.to_string();
        let sem = inner.write_sem.clone();

        for target in targets {
            let fs = target.backend.clone();
            let name = target.name.clone();
            let path = path_owned.clone();
            let ctx = ctx.clone();
            let sem = sem.clone();
            let inner = Arc::clone(inner);
            let op_clone = op.clone();
            let queue_key = Self::backup_queue_key(&path, &name);
            inner.background_task_started();

            tokio::spawn(async move {
                {
                    // Per (path, backup) serialization preserves FIFO without blocking other backups.
                    let result = inner
                        .path_queues
                        .with_path_lock(&queue_key, || async {
                            let _permit = if let Some(ref sem) = sem {
                                sem.acquire().await.ok()
                            } else {
                                None
                            };

                            op_clone.apply(&inner, fs, &path, &ctx).await
                        })
                        .await;

                    // Update acked_seq on successful write.
                    if result.is_ok() {
                        let _ = inner.update_backup_acked_seq(&path, &name, &ctx).await;
                    }
                }
                inner.background_task_finished();
            });
        }
    }

    /// Record rename metadata and migrate redirect state when needed.
    async fn record_rename_meta(
        &self,
        old_path: &str,
        new_path: &str,
        ctx: &FsContext,
    ) -> Result<()> {
        let source_dir = parent_dir(old_path);
        let target_dir = parent_dir(new_path);
        let old_name = file_name(old_path).to_string();
        let new_name = file_name(new_path).to_string();
        let seq = self.next_seq().await?;
        let rename_op = SyncOp::Rename {
            to: new_path.to_string(),
        };

        if source_dir == target_dir {
            self.meta_store
                .update_dir_meta(&source_dir, ctx, move |redirect, sync_log| {
                    sync_log
                        .entries
                        .insert(old_name.clone(), SyncLogEntry::committed(seq, rename_op));
                    if let Some(redirect_entry) = redirect.entries.remove(&old_name) {
                        redirect.entries.insert(new_name, redirect_entry);
                    }
                    Ok(())
                })
                .await
        } else {
            self.meta_store
                .update_dual_dir_meta(
                    &source_dir,
                    &target_dir,
                    ctx,
                    move |src_redirect, src_sync_log, tgt_redirect, _tgt_sync_log| {
                        src_sync_log
                            .entries
                            .insert(old_name.clone(), SyncLogEntry::committed(seq, rename_op));
                        if let Some(redirect_entry) = src_redirect.entries.remove(&old_name) {
                            tgt_redirect.entries.insert(new_name, redirect_entry);
                        }
                        Ok(())
                    },
                )
                .await
        }
    }
}

/// Return true when `path` falls under `exclude_path` (including itself).
fn is_excluded_grep_path(path: &str, exclude_path: Option<&str>) -> bool {
    let Some(exclude_path) = exclude_path.map(normalize_prefix_path) else {
        return false;
    };
    let normalized_path = normalize_prefix_path(path);
    normalized_path == exclude_path
        || normalized_path
            .strip_prefix(&exclude_path)
            .is_some_and(|suffix| suffix.starts_with('/'))
}

// ── FileSystem trait implementation ──

impl Drop for MultiWriteWrappedFS {
    /// Signal retry_loop to exit when the wrapper is unmounted or dropped.
    fn drop(&mut self) {
        self.inner.retry_cancelled.store(true, Ordering::SeqCst);
        self.inner.retry_shutdown.notify_waiters();
    }
}

impl MultiWriteWrappedFS {
    /// Stop background retry work and wait for in-flight async fanout to drain.
    pub async fn shutdown(&self) -> Result<()> {
        self.inner.retry_cancelled.store(true, Ordering::SeqCst);
        self.inner.retry_shutdown.notify_waiters();
        self.inner.wait_idle(DEFAULT_SHUTDOWN_WAIT).await
    }

    /// Copy one primary-resident file to a new path while preserving physical primary bytes.
    pub async fn copy_within_primary(&self, src_path: &str, dst_path: &str) -> Result<bool> {
        if normalize_prefix_path(src_path) == normalize_prefix_path(dst_path) {
            return Ok(true);
        }

        let ctx = current_required_ctx()?;
        let inner = &self.inner;
        let source_path = src_path.to_string();
        let destination_path = dst_path.to_string();

        let source_exists_on_primary = FS_CTX
            .scope(ctx.clone(), async {
                inner.primary().backend.exists(&source_path).await
            })
            .await;
        if !source_exists_on_primary {
            return Ok(false);
        }

        let source_size = FS_CTX
            .scope(ctx.clone(), async {
                let source_info = inner.primary().backend.stat(&source_path).await?;
                if source_info.is_dir {
                    return Err(Error::IsADirectory(source_path.clone()));
                }
                Ok::<u64, Error>(source_info.size)
            })
            .await?;

        if inner
            .check_redirect(&destination_path, source_size)
            .is_some()
        {
            return Ok(false);
        }

        let Some(primary_raw_backend) = inner.primary().raw_backend.clone() else {
            return Ok(false);
        };

        let primary_destination = destination_path.clone();
        Inner::execute_write(
            inner,
            destination_path,
            source_size,
            Some(SyncOp::SyncFile { size: source_size }),
            Some(BackupWriteOp::Replay(SyncOp::SyncFile {
                size: source_size,
            })),
            move |_ignored_fs| {
                let primary_raw_backend = primary_raw_backend.clone();
                let primary_source = source_path.clone();
                let primary_destination = primary_destination.clone();
                let primary_ctx = ctx.clone();
                async move {
                    copy_raw_primary_state(
                        primary_raw_backend,
                        &primary_source,
                        &primary_destination,
                        &primary_ctx,
                    )
                    .await?;
                    Ok(())
                }
            },
        )
        .await?;

        Ok(true)
    }

    /// Execute one non-redirecting write-like operation through the shared multi-write pipeline.
    async fn execute_simple_write<R, P, PFut>(
        &self,
        path: &str,
        size: u64,
        sync_op: Option<SyncOp>,
        primary_fn: P,
    ) -> Result<R>
    where
        R: Send + 'static,
        P: FnOnce(Arc<dyn FileSystem>, String) -> PFut + Send,
        PFut: Future<Output = Result<R>> + Send,
    {
        let path_owned = path.to_string();
        let primary_path = path_owned.clone();
        let backup_op = sync_op.clone().map(BackupWriteOp::Replay);
        Inner::execute_write(
            &self.inner,
            path_owned,
            size,
            sync_op,
            backup_op,
            move |fs| primary_fn(fs, primary_path),
        )
        .await
    }
}

#[async_trait]
impl FileSystem for MultiWriteWrappedFS {
    async fn create(&self, path: &str) -> Result<()> {
        self.execute_simple_write(path, 0, Some(SyncOp::Create), |fs, path| async move {
            fs.create(&path).await
        })
        .await
    }

    async fn mkdir(&self, path: &str, mode: u32) -> Result<()> {
        self.execute_simple_write(
            path,
            0,
            Some(SyncOp::Mkdir { mode }),
            move |fs, path| async move { fs.mkdir(&path, mode).await },
        )
        .await
    }

    async fn remove(&self, path: &str) -> Result<()> {
        self.execute_simple_write(path, 0, Some(SyncOp::Remove), |fs, path| async move {
            fs.remove(&path).await
        })
        .await
    }

    async fn remove_all(&self, path: &str) -> Result<()> {
        self.execute_simple_write(path, 0, Some(SyncOp::RemoveAll), |fs, path| async move {
            fs.remove_all(&path).await
        })
        .await
    }

    async fn read(&self, path: &str, offset: u64, size: u64) -> Result<Vec<u8>> {
        if let Some(fs) = self.inner.resolve_read_backend(path).await {
            return fs.read(path, offset, size).await;
        }
        Err(Error::not_found(path))
    }

    async fn write(&self, path: &str, data: &[u8], offset: u64, flags: WriteFlag) -> Result<u64> {
        let inner = &self.inner;
        let data_len = data.len() as u64;
        let path_owned = path.to_string();
        let backup_op = BackupWriteOp::WriteFile {
            data: Arc::new(data.to_vec()),
            offset,
            flags,
        };
        let d = data.to_vec();
        let primary_path = path_owned.clone();
        Inner::execute_write_with_redirect(
            inner,
            path_owned,
            data_len,
            SyncOp::SyncFile { size: data_len },
            backup_op,
            move |fs: Arc<dyn FileSystem>| async move {
                fs.write(&primary_path, &d, offset, flags).await
            },
        )
        .await
    }

    async fn read_dir(&self, path: &str) -> Result<Vec<FileInfo>> {
        let inner = &self.inner;
        let mut entries = inner.primary().backend.read_dir(path).await?;

        // Filter multi-write internal names.
        entries.retain(|e| !MULTIWRITE_INTERNAL_NAMES.contains(&e.name.as_str()));

        // Merge redirect entries so users can see redirected files in listings.
        let ctx =
            current_required_ctx().or_else(|_| inner.meta_store.ctx_resolver().resolve(path))?;

        if let Ok(redirect_meta) = inner.meta_store.get_redirect_meta(path, &ctx).await {
            for (name, redirect_entry) in &redirect_meta.entries {
                if !entries.iter().any(|e| &e.name == name) {
                    let virtual_path = if path == "/" {
                        format!("/{}", name)
                    } else {
                        format!("{}/{}", path.trim_end_matches('/'), name)
                    };
                    entries.push(
                        inner
                            .redirect_file_info(&virtual_path, name, redirect_entry)
                            .await,
                    );
                }
            }
        }

        Ok(entries)
    }

    async fn stat(&self, path: &str) -> Result<FileInfo> {
        if let Some(fs) = self.inner.resolve_read_backend(path).await {
            return fs.stat(path).await;
        }
        Err(Error::not_found(path))
    }

    async fn rename(&self, old_path: &str, new_path: &str) -> Result<()> {
        let ctx = current_required_ctx()?;
        let inner = &self.inner;
        let old_owned = old_path.to_string();
        let new_owned = new_path.to_string();
        let source_dir = parent_dir(&old_owned);
        let redirect_targets = inner.redirect_targets_for_path(&old_owned, &ctx).await?;
        inner.invalidate_read_route(&old_owned).await;
        inner.invalidate_read_route(&new_owned).await;

        let primary_has_old = inner.primary().backend.exists(&old_owned).await;
        if primary_has_old {
            FS_CTX
                .scope(ctx.clone(), async {
                    inner.primary().backend.rename(&old_owned, &new_owned).await
                })
                .await?;
        } else if redirect_targets.is_none() {
            return Err(Error::NotFound(old_owned));
        }

        inner
            .record_rename_meta(&old_owned, &new_owned, &ctx)
            .await?;
        inner.mark_pending_dir(&source_dir).await;

        let fanout_path = old_owned.clone();
        let backup_op = BackupWriteOp::Replay(SyncOp::Rename {
            to: new_owned.clone(),
        });
        if let Some(targets) = redirect_targets {
            Inner::fanout_write_to_targets(inner, &fanout_path, &targets, ctx.clone(), backup_op)
                .await?;
        } else {
            Inner::fanout_write(inner, &fanout_path, 0, ctx.clone(), backup_op).await?;
        }
        inner.refresh_pending_dir(&source_dir, &ctx).await?;

        Ok(())
    }

    async fn chmod(&self, path: &str, mode: u32) -> Result<()> {
        self.execute_simple_write(
            path,
            0,
            Some(SyncOp::Chmod { mode }),
            move |fs, path| async move { fs.chmod(&path, mode).await },
        )
        .await
    }

    async fn truncate(&self, path: &str, size: u64) -> Result<()> {
        self.execute_simple_write(
            path,
            size,
            Some(SyncOp::SyncFile { size }),
            move |fs, path| async move { fs.truncate(&path, size).await },
        )
        .await
    }

    async fn ensure_parent_dirs(&self, path: &str, mode: u32) -> Result<()> {
        let path_owned = path.to_string();
        let primary_path = path_owned.clone();
        Inner::execute_write(
            &self.inner,
            path_owned,
            0,
            None,
            Some(BackupWriteOp::EnsureParentDirs { mode }),
            move |fs| async move { fs.ensure_parent_dirs(&primary_path, mode).await },
        )
        .await
    }

    async fn grep(
        &self,
        path: &str,
        pattern: &str,
        recursive: bool,
        case_insensitive: bool,
        node_limit: Option<usize>,
        exclude_path: Option<&str>,
        level_limit: Option<usize>,
    ) -> Result<GrepResult> {
        let inner = &self.inner;
        let path_owned = path.to_string();
        let pattern_owned = pattern.to_string();
        let exclude_owned = exclude_path.map(|s| s.to_string());

        let mut result = inner
            .primary()
            .backend
            .grep(
                &path_owned,
                &pattern_owned,
                recursive,
                case_insensitive,
                node_limit,
                exclude_owned.as_deref(),
                level_limit,
            )
            .await?;

        // Filter out multi-write internal metadata files from grep results.
        result.matches.retain(|m| {
            let file_name = m.file.rsplit('/').next().unwrap_or(&m.file);
            !MULTIWRITE_INTERNAL_NAMES.contains(&file_name)
        });
        result.count = result.matches.len();

        // For redirect files, also grep in target backends.
        let ctx = current_required_ctx()
            .or_else(|_| inner.meta_store.ctx_resolver().resolve(&path_owned))?;

        let search_dir = if inner
            .primary()
            .backend
            .stat(&path_owned)
            .await
            .map(|s| s.is_dir)
            .unwrap_or(false)
        {
            path_owned.clone()
        } else {
            parent_dir(&path_owned)
        };

        if recursive && search_dir == path_owned {
            let redirect_entries = self
                .tree_directory(&path_owned, true, None, level_limit)
                .await?;
            for entry in redirect_entries {
                if node_limit.is_some_and(|limit| result.count >= limit) {
                    break;
                }
                if entry.info.is_dir
                    || entry.extra.get("redirect").and_then(Value::as_bool) != Some(true)
                {
                    continue;
                }
                if is_excluded_grep_path(&entry.path, exclude_owned.as_deref()) {
                    continue;
                }
                let Some(read_backend) = inner.resolve_read_backend(&entry.path).await else {
                    continue;
                };
                let rel_path = relative_match_file(&path_owned, &entry.path);
                let target_result = match read_backend
                    .grep(
                        &entry.path,
                        &pattern_owned,
                        false,
                        case_insensitive,
                        node_limit.map(|limit| limit.saturating_sub(result.count)),
                        None,
                        None,
                    )
                    .await
                {
                    Ok(found) => found,
                    Err(_) => continue,
                };
                for m in target_result.matches {
                    if node_limit.is_some_and(|limit| result.count >= limit) {
                        break;
                    }
                    result.add_match(rel_path.clone(), m.line, m.content);
                }
            }
            return Ok(result);
        }

        if let Ok(redirect_meta) = inner.meta_store.get_redirect_meta(&search_dir, &ctx).await {
            for (name, redirect_entry) in &redirect_meta.entries {
                for target_name in &redirect_entry.targets {
                    if let Some(be) = inner.backup_by_name(target_name) {
                        let redirect_path = if search_dir == "/" {
                            format!("/{}", name)
                        } else {
                            format!("{}/{}", search_dir, name)
                        };
                        if let Ok(target_result) = be
                            .backend
                            .grep(
                                &redirect_path,
                                &pattern_owned,
                                false,
                                case_insensitive,
                                node_limit,
                                None,
                                None,
                            )
                            .await
                        {
                            let rel_path = relative_match_file(&path_owned, &redirect_path);
                            for m in target_result.matches {
                                if node_limit.is_some_and(|limit| result.count >= limit) {
                                    break;
                                }
                                result.add_match(rel_path.clone(), m.line, m.content);
                            }
                        }
                    }
                }
            }
        }

        Ok(result)
    }

    async fn tree_directory(
        &self,
        path: &str,
        show_hidden: bool,
        node_limit: Option<usize>,
        level_limit: Option<usize>,
    ) -> Result<Vec<TreeEntry>> {
        let base = normalize_prefix_path(path);
        let mut entries = self
            .inner
            .primary()
            .backend
            .tree_directory(path, show_hidden, node_limit, level_limit)
            .await?;

        entries.retain(|e| {
            let name = file_name(&e.path);
            !MULTIWRITE_INTERNAL_NAMES.contains(&name)
        });

        let ctx = current_required_ctx()
            .or_else(|_| self.inner.meta_store.ctx_resolver().resolve(&base))?;
        let mut seen_paths: HashSet<String> = entries.iter().map(|e| e.path.clone()).collect();
        let mut dir_paths = vec![base.clone()];
        for entry in &entries {
            if entry.info.is_dir {
                let dir = normalize_prefix_path(&entry.path);
                if !dir_paths.iter().any(|p| p == &dir) {
                    dir_paths.push(dir);
                }
            }
        }

        for dir in dir_paths {
            let redirect_meta = match self.inner.meta_store.get_redirect_meta(&dir, &ctx).await {
                Ok(meta) => meta,
                Err(_) => continue,
            };
            for (name, redirect_entry) in redirect_meta.entries {
                let virtual_path = if dir == "/" {
                    format!("/{}", name)
                } else {
                    format!("{}/{}", dir, name)
                };
                if seen_paths.contains(&virtual_path) {
                    continue;
                }
                let rel_path = if base == "/" {
                    virtual_path.trim_start_matches('/').to_string()
                } else {
                    virtual_path
                        .strip_prefix(&base)
                        .unwrap_or(&virtual_path)
                        .trim_start_matches('/')
                        .to_string()
                };
                let mut extra = HashMap::new();
                extra.insert("redirect".to_string(), Value::Bool(true));
                entries.push(TreeEntry {
                    path: virtual_path.clone(),
                    rel_path,
                    info: self
                        .inner
                        .redirect_file_info(&virtual_path, &name, &redirect_entry)
                        .await,
                    extra,
                });
                seen_paths.insert(virtual_path);
            }
        }

        Ok(entries)
    }
}
