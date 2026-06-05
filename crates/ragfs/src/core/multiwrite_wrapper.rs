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
use std::pin::Pin;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use rand::Rng;
use regex::Regex;
use serde_json::{json, Value};
use tokio::sync::Notify;

use super::context::{FsContext, FS_CTX};
use super::errors::{Error, Result};
use super::filesystem::{normalize_prefix_path, FileSystem};
use super::multiwrite_meta::{
    current_required_ctx, file_name, parent_dir, DefaultFsContextResolver, MetaStateStore,
    PathSerializer,
};
use super::types::{
    BackendRole, BackendSyncState, FileInfo, GrepResult, OperationItemConfig, RedirectEntry,
    RedirectPolicy, SyncLogEntry, SyncOp, SyncType, TreeEntry, WriteFlag,
};

/// Internal file names that are invisible to users.
const INTERNAL_NAMES: &[&str] = &[".path.ovlock", ".sync_log.json", ".redirect.json"];

/// Maximum retries per file per retry_loop round.
const MAX_RETRY_PER_ROUND: usize = 3;
/// Consecutive replay failures before a target is quarantined.
const QUARANTINE_AFTER_FAILURES: u32 = 9;

macro_rules! clone_to_move {
    ($($name:ident),+ $(,)?) => {
        $(let $name = $name.clone();)+
    };
}

type BoxedWriteFuture = Pin<Box<dyn Future<Output = Result<()>> + Send>>;
type BoxedWriteOp = Arc<dyn Fn(Arc<dyn FileSystem>) -> BoxedWriteFuture + Send + Sync>;

/// Convert a typed async write closure into the boxed form used by fanout strategies.
fn boxed_write_op<F, Fut>(op: F) -> BoxedWriteOp
where
    F: Fn(Arc<dyn FileSystem>) -> Fut + Send + Sync + 'static,
    Fut: Future<Output = Result<()>> + Send + 'static,
{
    Arc::new(move |fs| Box::pin(op(fs)))
}

/// Cloned target data passed to fanout strategies without borrowing `Inner`.
#[derive(Clone)]
struct FanoutTarget {
    name: String,
    backend: Arc<dyn FileSystem>,
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

struct WriteOp<R, P> {
    path: String,
    size: u64,
    primary_fn: P,
    backup_fn: BoxedWriteOp,
    sync_op: Option<SyncOp>,
    redirect_eligible: bool,
    redirect_result: Option<R>,
}

/// A backend entry within the multi-write wrapper.
pub struct BackendEntry {
    /// Logical name (globally unique)
    pub name: String,
    /// Role: Primary or Backup
    pub role: BackendRole,
    /// The backend filesystem handle (may be encrypted)
    pub backend: Arc<dyn FileSystem>,
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
        backup: Arc<dyn FileSystem>,
        file_path: &str,
        ctx: &FsContext,
    ) -> Result<()> {
        match self {
            SyncOp::Write {
                offset,
                flags,
                content,
                ..
            } => {
                let content = content.clone();
                let offset = *offset;
                let flags = *flags;
                FS_CTX
                    .scope(ctx.clone(), async {
                        backup.ensure_parent_dirs(file_path, 0o755).await?;
                        backup
                            .write(file_path, &content, offset, flags)
                            .await
                            .map(|_| ())
                    })
                    .await
            }
            SyncOp::Truncate { size, content } => {
                let size = *size;
                let content = content.clone();
                FS_CTX
                    .scope(ctx.clone(), async {
                        backup.ensure_parent_dirs(file_path, 0o755).await?;
                        if backup.exists(file_path).await {
                            backup.truncate(file_path, size).await
                        } else {
                            backup
                                .write(file_path, &content, 0, WriteFlag::Create)
                                .await
                                .map(|_| ())
                        }
                    })
                    .await
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
                    .scope(ctx.clone(), async { backup.rename(file_path, &to).await })
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

/// Inner state shared via Arc for async spawn and retry_loop.
struct Inner {
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
    meta_store: MetaStateStore,
    /// Per-path serialization queues
    path_queues: PathSerializer,
    /// Global sequence counter for sync_log
    seq_counter: AtomicU64,
    /// Cancellation flag for the background retry loop.
    retry_cancelled: AtomicBool,
    /// Wake-up signal used to stop retry_loop promptly on drop.
    retry_shutdown: Notify,
}

/// Multi-write wrapped filesystem.
pub struct MultiWriteWrappedFS {
    inner: Arc<Inner>,
}

/// Builder for `MultiWriteWrappedFS`.
pub struct MultiWriteWrappedFSBuilder {
    primary_backend: Arc<dyn FileSystem>,
    backup_entries: Vec<BackendEntry>,
    redirects: Vec<RedirectPolicy>,
    sync_mode: SyncMode,
    write_concurrency: Option<usize>,
}

impl MultiWriteWrappedFSBuilder {
    /// Add backup backend entries to the builder.
    pub fn add_backups(mut self, backup_entries: Vec<BackendEntry>) -> Self {
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

    /// Build the multi-write wrapper and start the retry loop when needed.
    pub fn build(self) -> Result<MultiWriteWrappedFS> {
        let mut backends = Vec::new();
        backends.push(BackendEntry {
            name: "primary".to_string(),
            role: BackendRole::Primary,
            backend: self.primary_backend.clone(),
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

        let ctx_resolver = Arc::new(DefaultFsContextResolver);
        let meta_store = MetaStateStore::new(self.primary_backend, ctx_resolver);

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
            seq_counter: AtomicU64::new(0),
            retry_cancelled: AtomicBool::new(false),
            retry_shutdown: Notify::new(),
        });

        // Start retry_loop if there are write-enabled backups.
        if inner.write_backups().next().is_some() {
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
            backup_entries: Vec::new(),
            redirects: Vec::new(),
            sync_mode: SyncMode::Async,
            write_concurrency: None,
        }
    }

    /// Collect effective sync work entries under a path using the current request context.
    async fn collect_sync_work(
        &self,
        path: &str,
    ) -> Result<Vec<(String, SyncLogEntry, Vec<String>)>> {
        let ctx = current_required_ctx()?;
        let inner = &self.inner;
        let normalized = normalize_prefix_path(path);
        let path_info = <Self as FileSystem>::stat(self, &normalized).await?;
        let mut dirs = Vec::new();
        let mut seen_dirs = HashSet::new();

        let add_dir = |dirs: &mut Vec<String>, seen_dirs: &mut HashSet<String>, dir: String| {
            if seen_dirs.insert(dir.clone()) {
                dirs.push(dir);
            }
        };

        if path_info.is_dir {
            add_dir(&mut dirs, &mut seen_dirs, normalized.clone());
            for entry in inner
                .primary()
                .backend
                .tree_directory(&normalized, true, None, None)
                .await?
            {
                if entry.info.is_dir {
                    add_dir(
                        &mut dirs,
                        &mut seen_dirs,
                        normalize_prefix_path(&entry.path),
                    );
                }
            }
        } else {
            add_dir(
                &mut dirs,
                &mut seen_dirs,
                normalize_prefix_path(&parent_dir(&normalized)),
            );
        }

        let mut work = Vec::new();
        for dir in dirs {
            let sync_log = inner.meta_store.get_sync_log_meta(&dir, &ctx).await?;
            if sync_log.entries.is_empty() {
                continue;
            }
            let redirect_meta = inner
                .meta_store
                .get_redirect_meta(&dir, &ctx)
                .await
                .unwrap_or_default();

            for (name, sync_entry) in sync_log.entries {
                let file_path = if dir == "/" {
                    format!("/{}", name)
                } else {
                    format!("{}/{}", dir, name)
                };
                if !path_info.is_dir && file_path != normalized {
                    continue;
                }
                let target_backend_names =
                    inner.target_backend_names(&redirect_meta, &name, &file_path, &sync_entry);
                work.push((file_path, sync_entry, target_backend_names));
            }
        }

        Ok(work)
    }

    /// Query effective multi-write sync status under a file or directory path.
    pub async fn system_sync_status(&self, path: &str) -> Result<Value> {
        let work = self.collect_sync_work(path).await?;
        let mut entries = Vec::new();
        let mut pending_target_count = 0usize;

        for (file_path, sync_entry, target_backend_names) in work {
            let mut targets = Vec::new();
            let mut all_synced = true;

            for backend_name in target_backend_names {
                let acked_seq = sync_entry.acked_seq(&backend_name);
                let in_sync = sync_entry.is_in_sync(&backend_name);
                if !in_sync {
                    pending_target_count += 1;
                    all_synced = false;
                }
                let state = sync_entry.backend_state(&backend_name);
                targets.push(json!({
                    "name": backend_name,
                    "acked_seq": acked_seq,
                    "retry_failures": state.map(|state| state.retry_failures).unwrap_or(0),
                    "quarantined": state.map(|state| state.quarantined).unwrap_or(false),
                    "in_sync": in_sync,
                }));
            }

            entries.push(json!({
                "path": file_path,
                "latest_seq": sync_entry.latest_seq,
                "op": serde_json::to_value(&sync_entry.op)?,
                "all_synced": all_synced,
                "targets": targets,
            }));
        }

        entries.sort_by(|a, b| {
            let ap = a.get("path").and_then(Value::as_str).unwrap_or_default();
            let bp = b.get("path").and_then(Value::as_str).unwrap_or_default();
            ap.cmp(bp)
        });

        Ok(json!({
            "path": normalize_prefix_path(path),
            "entry_count": entries.len(),
            "pending_target_count": pending_target_count,
            "entries": entries,
        }))
    }

    /// Manually retry lagging multi-write targets under a file or directory path.
    pub async fn system_sync_retry(&self, path: &str) -> Result<Value> {
        let ctx = current_required_ctx()?;
        let work = self.collect_sync_work(path).await?;
        let mut results = Vec::new();
        let mut retried = 0usize;
        let mut failed = 0usize;
        let mut skipped = 0usize;

        for (file_path, sync_entry, target_backend_names) in work {
            for backend_name in target_backend_names {
                let acked_seq = sync_entry.acked_seq(&backend_name);
                let was_quarantined = sync_entry.is_quarantined(&backend_name);
                if sync_entry.is_in_sync(&backend_name) {
                    skipped += 1;
                    results.push(json!({
                        "path": file_path,
                        "target": backend_name,
                        "status": "skipped",
                        "latest_seq": sync_entry.latest_seq,
                        "acked_seq": acked_seq,
                    }));
                    continue;
                }

                let mut last_error = None;
                let mut success = false;
                for _attempt in 0..MAX_RETRY_PER_ROUND {
                    match self
                        .inner
                        .replay_operation(&file_path, &sync_entry, &backend_name, &ctx)
                        .await
                    {
                        Ok(()) => {
                            success = true;
                            break;
                        }
                        Err(err) => {
                            last_error = Some(err.to_string());
                            tokio::time::sleep(Duration::from_millis(100)).await;
                        }
                    }
                }

                if success {
                    retried += 1;
                    results.push(json!({
                        "path": file_path,
                        "target": backend_name,
                        "status": "retried",
                        "latest_seq": sync_entry.latest_seq,
                        "acked_seq": sync_entry.latest_seq,
                    }));
                } else {
                    self.inner
                        .record_backup_retry_failure(&file_path, &backend_name, &ctx)
                        .await?;
                    failed += 1;
                    results.push(json!({
                        "path": file_path,
                        "target": backend_name,
                        "status": "failed",
                        "latest_seq": sync_entry.latest_seq,
                        "acked_seq": acked_seq,
                        "was_quarantined": was_quarantined,
                        "error": last_error.unwrap_or_else(|| "unknown replay error".to_string()),
                    }));
                }
            }
        }

        Ok(json!({
            "path": normalize_prefix_path(path),
            "retried": retried,
            "failed": failed,
            "skipped": skipped,
            "results": results,
        }))
    }
}

impl Inner {
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

    /// Resolve effective target backend names for sync/retry work.
    fn target_backend_names(
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
    fn primary(&self) -> &BackendEntry {
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

    /// Generate the next sequence number.
    fn next_seq(&self) -> u64 {
        self.seq_counter.fetch_add(1, Ordering::SeqCst) + 1
    }

    /// Stat the first reachable redirect target and return user-visible metadata.
    async fn redirect_file_info(
        &self,
        path: &str,
        name: &str,
        redirect_entry: &RedirectEntry,
    ) -> FileInfo {
        for target_name in &redirect_entry.targets {
            if let Some(be) = self.backup_by_name(target_name) {
                if let Ok(mut info) = be.backend.stat(path).await {
                    info.name = name.to_string();
                    return info;
                }
            }
        }
        FileInfo::new_file(name.to_string(), 0, 0o644)
    }

    /// Resolve a file size for retry-time policy decisions.
    fn retry_policy_size(&self, sync_entry: &SyncLogEntry) -> u64 {
        match &sync_entry.op {
            SyncOp::Write { size, .. } | SyncOp::Truncate { size, .. } => *size,
            _ => 0,
        }
    }

    /// Resolve the read backend for a path using the fallback chain.
    async fn resolve_read_backend(&self, path: &str) -> Option<Arc<dyn FileSystem>> {
        let normalized = normalize_prefix_path(path);

        for backup in self.read_backups_sorted() {
            if backup.backend.exists(&normalized).await {
                return Some(backup.backend.clone());
            }
        }

        if self.primary().backend.exists(&normalized).await {
            return Some(self.primary().backend.clone());
        }

        let dir = parent_dir(&normalized);
        let name = file_name(&normalized).to_string();
        let ctx = current_required_ctx()
            .or_else(|_| self.meta_store.ctx_resolver().resolve(&dir))
            .ok()?;
        if let Ok(redirect_meta) = self.meta_store.get_redirect_meta(&dir, &ctx).await {
            if let Some(entry) = redirect_meta.entries.get(&name) {
                for target_name in &entry.targets {
                    if let Some(be) = self.backup_by_name(target_name) {
                        if be.backend.exists(&normalized).await {
                            return Some(be.backend.clone());
                        }
                    }
                }
            }
        }

        None
    }

    /// Execute the common primary-write, sync-log and backup-fanout pipeline.
    async fn execute_write<R, P, PFut>(inner: &Arc<Self>, op: WriteOp<R, P>) -> Result<R>
    where
        P: FnOnce(Arc<dyn FileSystem>) -> PFut + Send,
        PFut: Future<Output = Result<R>> + Send,
    {
        let ctx = current_required_ctx()?;
        let mut sync_op = op.sync_op;

        if op.redirect_eligible {
            if let Some(targets) = inner.check_redirect(&op.path, op.size) {
                let result = op
                    .redirect_result
                    .ok_or_else(|| Error::internal("redirect result missing".to_string()))?;
                let dir = parent_dir(&op.path);
                let name = file_name(&op.path).to_string();
                let seq = inner.next_seq();
                let targets_clone = targets.clone();
                let entry = sync_op.take();
                inner
                    .meta_store
                    .update_dir_meta(&dir, &ctx, move |redirect, sync_log| {
                        redirect.entries.insert(
                            name.clone(),
                            RedirectEntry {
                                targets: targets_clone.clone(),
                            },
                        );
                        if let Some(op) = entry {
                            sync_log.entries.insert(name, SyncLogEntry::new(seq, op));
                        }
                        Ok(())
                    })
                    .await?;
                Inner::fanout_write_to_targets(inner, &op.path, &targets, ctx, op.backup_fn)
                    .await?;
                return Ok(result);
            }
        }

        let result = FS_CTX
            .scope(
                ctx.clone(),
                (op.primary_fn)(inner.primary().backend.clone()),
            )
            .await?;

        if let Some(entry) = sync_op {
            let dir = parent_dir(&op.path);
            let name = file_name(&op.path).to_string();
            let seq = inner.next_seq();
            inner
                .meta_store
                .update_dir_meta(&dir, &ctx, move |_redirect, sync_log| {
                    sync_log.entries.insert(name, SyncLogEntry::new(seq, entry));
                    Ok(())
                })
                .await?;
        }

        Inner::fanout_write(inner, &op.path, op.size, ctx, op.backup_fn).await?;
        Ok(result)
    }

    /// Fanout a write operation to all write-enabled backups.
    /// Takes `&Arc<Inner>` so spawned tasks can clone the Arc for acked_seq updates.
    /// `ctx` is required for encrypted backup backends and acked_seq updates.
    async fn fanout_write(
        inner: &Arc<Inner>,
        path: &str,
        size: u64,
        ctx: FsContext,
        op: BoxedWriteOp,
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
        op: BoxedWriteOp,
    ) -> Result<()> {
        Inner::fanout_targets(inner, path, inner.named_targets(target_names), ctx, op).await
    }

    /// Fanout to already resolved targets using the configured sync mode.
    async fn fanout_targets(
        inner: &Arc<Inner>,
        path: &str,
        targets: Vec<FanoutTarget>,
        ctx: FsContext,
        op: BoxedWriteOp,
    ) -> Result<()> {
        if targets.is_empty() {
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
        op: BoxedWriteOp,
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

            handles.push(tokio::spawn(async move {
                // Wrap in FS_CTX.scope so encrypted backends can access account_id.
                let exec = async {
                    if let Some(ref ctx) = ctx {
                        FS_CTX.scope(ctx.clone(), op_clone(fs)).await
                    } else {
                        op_clone(fs).await
                    }
                };

                let result = if let Some(timeout) = timeout {
                    match tokio::time::timeout(timeout, exec).await {
                        Ok(Ok(())) => Ok(()),
                        Ok(Err(e)) => Err(format!("{}: {}", name, e)),
                        Err(_) => Err(format!("{}: timeout", name)),
                    }
                } else {
                    match exec.await {
                        Ok(()) => Ok(()),
                        Err(e) => Err(format!("{}: {}", name, e)),
                    }
                };

                // Update acked_seq on success.
                if result.is_ok() {
                    if let Some(ref ctx) = ctx {
                        let _ = inner.update_backup_acked_seq(&path, &name, ctx).await;
                    }
                }

                (name, result)
            }));
        }

        let results = futures::future::join_all(handles).await;

        let mut successes = 0usize;
        let mut errors = Vec::new();

        for result in results {
            match result {
                Ok((_name, Ok(()))) => {
                    successes += 1;
                }
                Ok((_name, Err(e))) => {
                    errors.push(e);
                }
                Err(e) => {
                    errors.push(format!("join error: {}", e));
                }
            }
        }

        if successes >= ack_count {
            Ok(())
        } else {
            Err(Error::internal(format!(
                "sync write failed: {}/{} backups succeeded. Errors: {}",
                successes,
                targets.len(),
                errors.join("; ")
            )))
        }
    }

    /// Asynchronous fanout: spawn background tasks that update acked_seq on completion.
    /// Uses per-path serialization to prevent out-of-order application on backup backends.
    async fn fanout_async(
        inner: &Arc<Inner>,
        path: &str,
        targets: Vec<FanoutTarget>,
        ctx: &FsContext,
        op: BoxedWriteOp,
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
            let queue_key = format!("{}\0{}", path, name);
            let path_lock = inner.path_queues.get_path_lock(&queue_key).await;

            tokio::spawn(async move {
                {
                    // Per (path, backup) serialization preserves FIFO without blocking other backups.
                    let _guard = path_lock.lock().await;

                    let _permit = if let Some(ref sem) = sem {
                        sem.acquire().await.ok()
                    } else {
                        None
                    };

                    // Wrap in FS_CTX.scope so encrypted backends can access account_id.
                    let result = FS_CTX.scope(ctx.clone(), op_clone(fs)).await;

                    // Update acked_seq on successful write.
                    if result.is_ok() {
                        let _ = inner.update_backup_acked_seq(&path, &name, &ctx).await;
                    }
                }
                inner
                    .path_queues
                    .cleanup_path_lock(&queue_key, &path_lock)
                    .await;
            });
        }
    }

    /// Update the acked_seq for a backup in the sync log.
    async fn update_backup_acked_seq(
        &self,
        path: &str,
        backup_name: &str,
        ctx: &FsContext,
    ) -> Result<()> {
        self.update_backend_state(path, backup_name, ctx, |state, latest_seq| {
            state.mark_acked(latest_seq);
        })
        .await
    }

    /// Record a replay failure and quarantine the target after repeated failures.
    async fn record_backup_retry_failure(
        &self,
        path: &str,
        backup_name: &str,
        ctx: &FsContext,
    ) -> Result<()> {
        self.update_backend_state(path, backup_name, ctx, |state, _latest_seq| {
            state.mark_retry_failed(QUARANTINE_AFTER_FAILURES);
        })
        .await
    }

    /// Update per-backend sync state for a file entry.
    async fn update_backend_state<F>(
        &self,
        path: &str,
        backup_name: &str,
        ctx: &FsContext,
        update: F,
    ) -> Result<()>
    where
        F: FnOnce(&mut BackendSyncState, u64) + Send,
    {
        let dir = parent_dir(path);
        let backup_name = backup_name.to_string();
        let name = file_name(path).to_string();
        self.meta_store
            .update_dir_meta(&dir, ctx, move |_redirect, sync_log| {
                if let Some(entry) = sync_log.entries.get_mut(&name) {
                    let state = entry.backends.entry(backup_name).or_default();
                    update(state, entry.latest_seq);
                }
                Ok(())
            })
            .await
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
        let seq = self.next_seq();
        let rename_op = SyncOp::Rename {
            to: new_path.to_string(),
        };

        if source_dir == target_dir {
            self.meta_store
                .update_dir_meta(&source_dir, ctx, move |redirect, sync_log| {
                    sync_log
                        .entries
                        .insert(old_name.clone(), SyncLogEntry::new(seq, rename_op));
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
                            .insert(old_name.clone(), SyncLogEntry::new(seq, rename_op));
                        if let Some(redirect_entry) = src_redirect.entries.remove(&old_name) {
                            tgt_redirect.entries.insert(new_name, redirect_entry);
                        }
                        Ok(())
                    },
                )
                .await
        }
    }

    /// Replay a single operation on a lagging backup.
    async fn replay_operation(
        &self,
        file_path: &str,
        entry: &SyncLogEntry,
        backup_name: &str,
        ctx: &FsContext,
    ) -> Result<()> {
        let backup = match self.backup_by_name(backup_name) {
            Some(b) => b,
            None => {
                return Err(Error::internal(format!(
                    "backup '{}' not found",
                    backup_name
                )))
            }
        };

        entry
            .op
            .replay(backup.backend.clone(), file_path, ctx)
            .await?;

        // Update acked_seq after successful replay.
        self.update_backup_acked_seq(file_path, backup_name, ctx)
            .await?;

        Ok(())
    }

    /// Background retry loop: periodically scans sync_log for lagging backups and replays.
    async fn retry_loop(inner: Arc<Inner>) {
        loop {
            if inner.retry_cancelled.load(Ordering::SeqCst) {
                break;
            }

            tokio::select! {
                _ = tokio::time::sleep(Duration::from_secs(30)) => {}
                _ = inner.retry_shutdown.notified() => break,
            }

            // Scan primary backend for all .sync_log.json files.
            let primary = inner.primary().backend.clone();
            let tree_result = primary.tree_directory("/", true, None, None).await;
            let tree_entries = match tree_result {
                Ok(e) => e,
                Err(_) => continue,
            };

            for entry in tree_entries {
                if !entry.info.name.ends_with(SYNC_LOG_FILE) {
                    continue;
                }

                let dir = parent_dir(&entry.path);
                let ctx = match inner.meta_store.ctx_resolver().resolve(&dir) {
                    Ok(c) => c,
                    Err(_) => continue,
                };

                let sync_log = match inner.meta_store.get_sync_log_meta(&dir, &ctx).await {
                    Ok(s) => s,
                    Err(_) => continue,
                };

                for (file_name, sync_entry) in &sync_log.entries {
                    // Construct full path from dir + file name (entries key is now file name only)
                    let file_path = if dir == "/" {
                        format!("/{}", file_name)
                    } else {
                        format!("{}/{}", dir, file_name)
                    };

                    let redirect_meta = inner
                        .meta_store
                        .get_redirect_meta(&dir, &ctx)
                        .await
                        .unwrap_or_default();
                    let target_backend_names = inner.target_backend_names(
                        &redirect_meta,
                        file_name,
                        &file_path,
                        sync_entry,
                    );

                    for backup_name in &target_backend_names {
                        if sync_entry.is_in_sync(backup_name)
                            || sync_entry.is_quarantined(backup_name)
                        {
                            continue;
                        }

                        // Retry up to MAX_RETRY_PER_ROUND times with exponential backoff.
                        let mut success = false;
                        for attempt in 0..MAX_RETRY_PER_ROUND {
                            if inner.retry_cancelled.load(Ordering::SeqCst) {
                                return;
                            }
                            if inner
                                .replay_operation(&file_path, sync_entry, backup_name, &ctx)
                                .await
                                .is_ok()
                            {
                                success = true;
                                break;
                            }
                            let base_ms = 100u64.saturating_mul(1u64 << attempt);
                            let jitter_ms = rand::thread_rng().gen_range(0..=50);
                            tokio::time::sleep(Duration::from_millis(base_ms + jitter_ms)).await;
                        }
                        if !success {
                            let _ = inner
                                .record_backup_retry_failure(&file_path, backup_name, &ctx)
                                .await;
                        }
                    }
                }
            }
        }
    }
}

/// Internal metadata file name used by retry_loop tree scan.
const SYNC_LOG_FILE: &str = ".sync_log.json";

// ── FileSystem trait implementation ──

impl Drop for MultiWriteWrappedFS {
    /// Signal retry_loop to exit when the wrapper is unmounted or dropped.
    fn drop(&mut self) {
        self.inner.retry_cancelled.store(true, Ordering::SeqCst);
        self.inner.retry_shutdown.notify_waiters();
    }
}

#[async_trait]
impl FileSystem for MultiWriteWrappedFS {
    async fn create(&self, path: &str) -> Result<()> {
        let inner = &self.inner;
        let path_owned = path.to_string();
        let backup_path = path_owned.clone();
        Inner::execute_write(
            inner,
            WriteOp {
                path: path_owned.clone(),
                size: 0,
                primary_fn: move |fs: Arc<dyn FileSystem>| async move {
                    fs.create(&path_owned).await
                },
                backup_fn: boxed_write_op(move |fs| {
                    clone_to_move!(backup_path);
                    async move { fs.create(&backup_path).await }
                }),
                sync_op: Some(SyncOp::Create),
                redirect_eligible: false,
                redirect_result: None::<()>,
            },
        )
        .await
    }

    async fn mkdir(&self, path: &str, mode: u32) -> Result<()> {
        let inner = &self.inner;
        let path_owned = path.to_string();
        let backup_path = path_owned.clone();
        Inner::execute_write(
            inner,
            WriteOp {
                path: path_owned.clone(),
                size: 0,
                primary_fn: move |fs: Arc<dyn FileSystem>| async move {
                    fs.mkdir(&path_owned, mode).await
                },
                backup_fn: boxed_write_op(move |fs| {
                    clone_to_move!(backup_path);
                    async move { fs.mkdir(&backup_path, mode).await }
                }),
                sync_op: Some(SyncOp::Mkdir { mode }),
                redirect_eligible: false,
                redirect_result: None::<()>,
            },
        )
        .await
    }

    async fn remove(&self, path: &str) -> Result<()> {
        let inner = &self.inner;
        let path_owned = path.to_string();
        let backup_path = path_owned.clone();
        Inner::execute_write(
            inner,
            WriteOp {
                path: path_owned.clone(),
                size: 0,
                primary_fn: move |fs: Arc<dyn FileSystem>| async move {
                    fs.remove(&path_owned).await
                },
                backup_fn: boxed_write_op(move |fs| {
                    clone_to_move!(backup_path);
                    async move { fs.remove(&backup_path).await }
                }),
                sync_op: Some(SyncOp::Remove),
                redirect_eligible: false,
                redirect_result: None::<()>,
            },
        )
        .await
    }

    async fn remove_all(&self, path: &str) -> Result<()> {
        let inner = &self.inner;
        let path_owned = path.to_string();
        let backup_path = path_owned.clone();
        Inner::execute_write(
            inner,
            WriteOp {
                path: path_owned.clone(),
                size: 0,
                primary_fn: move |fs: Arc<dyn FileSystem>| async move {
                    fs.remove_all(&path_owned).await
                },
                backup_fn: boxed_write_op(move |fs| {
                    clone_to_move!(backup_path);
                    async move { fs.remove_all(&backup_path).await }
                }),
                sync_op: Some(SyncOp::RemoveAll),
                redirect_eligible: false,
                redirect_result: None::<()>,
            },
        )
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
        let op_content = data.to_vec();
        let log_content = data.to_vec();
        let d = data.to_vec();
        let primary_path = path_owned.clone();
        let backup_path = path_owned.clone();
        Inner::execute_write(
            inner,
            WriteOp {
                path: path_owned,
                size: data_len,
                primary_fn: move |fs: Arc<dyn FileSystem>| async move {
                    fs.write(&primary_path, &d, offset, flags).await
                },
                backup_fn: boxed_write_op(move |fs| {
                    clone_to_move!(backup_path, op_content);
                    async move {
                        fs.ensure_parent_dirs(&backup_path, 0o755).await?;
                        fs.write(&backup_path, &op_content, offset, flags)
                            .await
                            .map(|_| ())
                    }
                }),
                sync_op: Some(SyncOp::Write {
                    offset,
                    flags,
                    size: data_len,
                    content: log_content,
                }),
                redirect_eligible: true,
                redirect_result: Some(data_len),
            },
        )
        .await
    }

    async fn read_dir(&self, path: &str) -> Result<Vec<FileInfo>> {
        let inner = &self.inner;
        let mut entries = inner.primary().backend.read_dir(path).await?;

        // Filter internal names
        entries.retain(|e| !INTERNAL_NAMES.contains(&e.name.as_str()));

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

        FS_CTX
            .scope(ctx.clone(), async {
                inner.primary().backend.rename(&old_owned, &new_owned).await
            })
            .await?;

        inner
            .record_rename_meta(&old_owned, &new_owned, &ctx)
            .await?;

        let o = old_owned.clone();
        let fanout_path = o.clone();
        let n = new_owned.clone();
        Inner::fanout_write(
            inner,
            &fanout_path,
            0,
            ctx.clone(),
            boxed_write_op(move |fs: Arc<dyn FileSystem>| {
                clone_to_move!(o, n);
                async move { fs.rename(&o, &n).await }
            }),
        )
        .await?;

        Ok(())
    }

    async fn chmod(&self, path: &str, mode: u32) -> Result<()> {
        let inner = &self.inner;
        let path_owned = path.to_string();
        let backup_path = path_owned.clone();
        Inner::execute_write(
            inner,
            WriteOp {
                path: path_owned.clone(),
                size: 0,
                primary_fn: move |fs: Arc<dyn FileSystem>| async move {
                    fs.chmod(&path_owned, mode).await
                },
                backup_fn: boxed_write_op(move |fs| {
                    clone_to_move!(backup_path);
                    async move { fs.chmod(&backup_path, mode).await }
                }),
                sync_op: Some(SyncOp::Chmod { mode }),
                redirect_eligible: false,
                redirect_result: None::<()>,
            },
        )
        .await
    }

    async fn truncate(&self, path: &str, size: u64) -> Result<()> {
        let ctx = current_required_ctx()?;
        let inner = &self.inner;
        let path_owned = path.to_string();
        let backup_path = path_owned.clone();
        let snapshot = FS_CTX
            .scope(ctx.clone(), async {
                inner.primary().backend.truncate(&path_owned, size).await?;
                inner.primary().backend.read(&path_owned, 0, 0).await
            })
            .await?;

        let dir = parent_dir(&path_owned);
        let name = file_name(&path_owned).to_string();
        let seq = inner.next_seq();
        inner
            .meta_store
            .update_dir_meta(&dir, &ctx, move |_redirect, sync_log| {
                sync_log.entries.insert(
                    name,
                    SyncLogEntry::new(
                        seq,
                        SyncOp::Truncate {
                            size,
                            content: snapshot,
                        },
                    ),
                );
                Ok(())
            })
            .await?;

        Inner::fanout_write(
            inner,
            &path_owned,
            size,
            ctx,
            boxed_write_op(move |fs| {
                clone_to_move!(backup_path);
                async move { fs.truncate(&backup_path, size).await }
            }),
        )
        .await
    }

    async fn ensure_parent_dirs(&self, path: &str, mode: u32) -> Result<()> {
        let inner = &self.inner;
        let path_owned = path.to_string();
        let backup_path = path_owned.clone();
        Inner::execute_write(
            inner,
            WriteOp {
                path: path_owned.clone(),
                size: 0,
                primary_fn: move |fs: Arc<dyn FileSystem>| async move {
                    fs.ensure_parent_dirs(&path_owned, mode).await
                },
                backup_fn: boxed_write_op(move |fs| {
                    clone_to_move!(backup_path);
                    async move { fs.ensure_parent_dirs(&backup_path, mode).await }
                }),
                sync_op: None,
                redirect_eligible: false,
                redirect_result: None::<()>,
            },
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
                            for m in target_result.matches {
                                if node_limit.is_some_and(|limit| result.count >= limit) {
                                    break;
                                }
                                result.add_match(m.file, m.line, m.content);
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
            !INTERNAL_NAMES.contains(&name)
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::context::FsContextInner;
    use crate::plugins::memfs::MemFileSystem;

    /// Create the shared test context used by multi-write tests.
    fn test_ctx() -> FsContext {
        Arc::new(FsContextInner::new("acct".to_string()))
    }

    /// Create a sync multi-write filesystem with one memfs backup.
    fn test_multiwrite_fs(redirects: Vec<RedirectPolicy>) -> MultiWriteWrappedFS {
        let primary: Arc<dyn FileSystem> = Arc::new(MemFileSystem::new());
        let backup: Arc<dyn FileSystem> = Arc::new(MemFileSystem::new());
        let builder = MultiWriteWrappedFS::builder(primary)
            .add_backups(vec![BackendEntry {
                name: "backup1".to_string(),
                role: BackendRole::Backup,
                backend: backup,
                operations: Vec::new(),
                excludes: Vec::new(),
            }])
            .sync_mode(SyncMode::Sync {
                ack_count: 1,
                timeout_ms: 0,
            });

        if redirects.is_empty() {
            builder
        } else {
            builder.with_redirects(redirects)
        }
        .build()
        .unwrap()
    }

    #[test]
    fn test_file_policy_over_size() {
        let policy = RedirectPolicy::FileOverSizePolicy {
            max_size_mb: 1,
            target: Some(vec!["backup1".to_string()]),
        };
        assert!(policy.matches("/a/big.bin", 2 * 1024 * 1024));
        assert!(!policy.matches("/a/small.txt", 512));
    }

    #[test]
    fn test_file_policy_extension() {
        let policy = RedirectPolicy::FileExtensionPolicy {
            extensions: vec!["(pdf|ppt)".to_string()],
            target: Some(vec!["backup1".to_string()]),
        };
        assert!(policy.matches("/a/doc.pdf", 0));
        assert!(policy.matches("/a/slides.ppt", 0));
        assert!(!policy.matches("/a/text.txt", 0));
    }

    #[tokio::test]
    async fn test_read_dir_redirect_entries_use_target_stat() {
        let fs = test_multiwrite_fs(vec![RedirectPolicy::FileExtensionPolicy {
            extensions: vec!["\\.pdf$".to_string()],
            target: Some(vec!["backup1".to_string()]),
        }]);
        let ctx = test_ctx();

        FS_CTX
            .scope(ctx, async {
                fs.ensure_parent_dirs("/local/acct/docs/report.pdf", 0o755)
                    .await?;
                fs.write(
                    "/local/acct/docs/report.pdf",
                    b"pdf body",
                    0,
                    WriteFlag::Create,
                )
                .await?;

                let entries = fs.read_dir("/local/acct/docs").await?;
                let report = entries
                    .iter()
                    .find(|entry| entry.name == "report.pdf")
                    .expect("redirected file should be visible in read_dir");
                assert_eq!(report.size, 8);
                assert_eq!(report.mode, 0o644);
                Ok::<(), Error>(())
            })
            .await
            .unwrap();
    }

    #[tokio::test]
    async fn test_cross_dir_rename_does_not_create_target_write_log() {
        let fs = test_multiwrite_fs(Vec::new());
        let ctx = test_ctx();

        FS_CTX
            .scope(ctx.clone(), async {
                fs.ensure_parent_dirs("/local/acct/src/file.txt", 0o755)
                    .await?;
                fs.ensure_parent_dirs("/local/acct/dst/file.txt", 0o755)
                    .await?;
                fs.write(
                    "/local/acct/src/file.txt",
                    b"rename me",
                    0,
                    WriteFlag::Create,
                )
                .await?;
                fs.rename("/local/acct/src/file.txt", "/local/acct/dst/file.txt")
                    .await?;

                let target_log = fs
                    .inner
                    .meta_store
                    .get_sync_log_meta("/local/acct/dst", &ctx)
                    .await?;
                assert!(
                    !target_log.entries.contains_key("file.txt"),
                    "target directory must not record a fake write for cross-dir rename"
                );
                let source_log = fs
                    .inner
                    .meta_store
                    .get_sync_log_meta("/local/acct/src", &ctx)
                    .await?;
                let rename_entry = source_log
                    .entries
                    .get("file.txt")
                    .expect("source directory should record rename command");
                assert!(matches!(
                    &rename_entry.op,
                    SyncOp::Rename { to } if to == "/local/acct/dst/file.txt"
                ));
                Ok::<(), Error>(())
            })
            .await
            .unwrap();
    }
}
