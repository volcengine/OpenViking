//! Multi-write metadata management.
//!
//! Provides `MetaStateStore` for serialized read-modify-write of `.redirect.json` and
//! `.sync_log.json` through `primary_backend`, and `FsContextResolver` for recovering
//! `FsContext` from paths in background tasks.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::Mutex;

use serde::{de::DeserializeOwned, Deserialize, Serialize};

use crate::core::context::{FsContext, FsContextInner, FS_CTX};
use crate::core::errors::{Error, Result};
use crate::core::filesystem::FileSystem;
use crate::core::types::{RedirectMeta, SyncLogMeta, WriteFlag};

/// Trait for resolving `FsContext` from a filesystem path.
///
/// Used by background tasks (retry_loop, backfill, system_sync_retry) that lack a
/// foreground request context. Implementations extract `account_id` from the path
/// (e.g. `/local/{account_id}/...`).
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
        if parts.len() >= 2 && parts[0] == "local" && !parts[1].is_empty() {
            Ok(Arc::new(FsContextInner::new(parts[1].to_string())))
        } else {
            Err(Error::internal(format!(
                "cannot resolve FsContext from path: {}",
                path
            )))
        }
    }
}

/// Resolver that extracts `account_id` from paths under one concrete mount root.
pub struct MountRootFsContextResolver {
    mount_root: String,
}

impl MountRootFsContextResolver {
    /// Create a resolver for one normalized mount root such as `/local` or `/s3`.
    pub fn new(mount_root: &str) -> Self {
        let mut normalized = mount_root.trim_end_matches('/').to_string();
        if normalized.is_empty() {
            normalized = "/".to_string();
        } else if !normalized.starts_with('/') {
            normalized = format!("/{normalized}");
        }
        Self {
            mount_root: normalized,
        }
    }
}

impl FsContextResolver for MountRootFsContextResolver {
    fn resolve(&self, path: &str) -> Result<FsContext> {
        let normalized = if path.starts_with('/') {
            path.to_string()
        } else {
            format!("/{path}")
        };
        let suffix = if self.mount_root == "/" {
            normalized.trim_start_matches('/')
        } else {
            normalized
                .strip_prefix(&self.mount_root)
                .and_then(|suffix| suffix.strip_prefix('/'))
                .ok_or_else(|| {
                    Error::internal(format!("cannot resolve FsContext from path: {path}"))
                })?
        };
        let account_id = suffix.split('/').next().unwrap_or_default();
        if account_id.is_empty() {
            return Err(Error::internal(format!(
                "cannot resolve FsContext from path: {path}"
            )));
        }
        Ok(Arc::new(FsContextInner::new(account_id.to_string())))
    }
}

/// Internal file name for redirect metadata.
pub(crate) const REDIRECT_FILE: &str = ".redirect.json";
/// Internal file name for sync-log metadata.
pub(crate) const SYNC_LOG_FILE: &str = ".sync_log.json";
/// Hidden multi-write internal file names (redirect / sync-log metadata).
pub(crate) const MULTIWRITE_INTERNAL_NAMES: &[&str] = &[SYNC_LOG_FILE, REDIRECT_FILE];
const GLOBAL_STATE_FILE: &str = "/_system/.multiwrite.global.json";
const GLOBAL_STATE_VERSION: u32 = 1;

type TrackedLock = Mutex<()>;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
struct GlobalMultiWriteState {
    version: u32,
    next_seq: u64,
}

impl Default for GlobalMultiWriteState {
    /// Create the default persisted global state for multi-write sequencing.
    fn default() -> Self {
        Self {
            version: GLOBAL_STATE_VERSION,
            next_seq: 1,
        }
    }
}

/// Unified metadata store for `.redirect.json` and `.sync_log.json`.
///
/// All reads and writes go through `primary_backend`, inheriting its encryption
/// configuration. Directory-level locks ensure serialized access to both metadata
/// files within the same directory.
pub struct MetaStateStore {
    /// Primary backend (may be encrypted)
    primary_backend: Arc<dyn FileSystem>,
    /// Per-directory locks for serialized read-modify-write
    dir_locks: Mutex<HashMap<String, Arc<TrackedLock>>>,
    /// Dedicated global state lock for next_seq persistence.
    global_state_lock: Mutex<()>,
    /// Context resolver for background tasks
    ctx_resolver: Arc<dyn FsContextResolver>,
}

impl MetaStateStore {
    /// Create a new MetaStateStore.
    pub fn new(
        primary_backend: Arc<dyn FileSystem>,
        ctx_resolver: Arc<dyn FsContextResolver>,
    ) -> Self {
        Self::with_cache_config(primary_backend, ctx_resolver, 0, Duration::ZERO)
    }

    /// Create a new MetaStateStore; cache parameters are retained as no-op compatibility knobs.
    pub fn with_cache_config(
        primary_backend: Arc<dyn FileSystem>,
        ctx_resolver: Arc<dyn FsContextResolver>,
        _meta_cache_capacity: usize,
        _meta_cache_ttl: Duration,
    ) -> Self {
        Self {
            primary_backend,
            dir_locks: Mutex::new(HashMap::new()),
            global_state_lock: Mutex::new(()),
            ctx_resolver,
        }
    }

    /// Get or create a per-directory lock.
    async fn get_dir_lock(&self, dir: &str) -> Arc<TrackedLock> {
        let mut locks = self.dir_locks.lock().await;
        if let Some(lock) = locks.get(dir).cloned() {
            return lock;
        }
        let lock = Arc::new(TrackedLock::new(()));
        locks.insert(dir.to_string(), lock.clone());
        lock
    }

    /// Return the dedicated `_system` context used by global metadata.
    fn system_ctx() -> FsContext {
        Arc::new(FsContextInner::new("_system".to_string()))
    }

    /// - root directory using `_system` context
    fn effective_meta_ctx(dir: &str, ctx: &FsContext) -> FsContext {
        if dir == "/" {
            Self::system_ctx()
        } else {
            ctx.clone()
        }
    }

    /// Build the full path for a metadata file in a directory.
    fn meta_path(dir: &str, filename: &str) -> String {
        if dir == "/" {
            format!("/{}", filename)
        } else {
            format!("{}/{}", dir, filename)
        }
    }

    /// Read one JSON metadata file, returning default for missing or empty files.
    async fn read_meta<T>(&self, dir: &str, filename: &str, ctx: &FsContext) -> Result<T>
    where
        T: DeserializeOwned + Default,
    {
        let path = Self::meta_path(dir, filename);
        let effective_ctx = Self::effective_meta_ctx(dir, ctx);
        match FS_CTX
            .scope(effective_ctx, async {
                self.primary_backend.read(&path, 0, 0).await
            })
            .await
        {
            Ok(data) => {
                if data.is_empty() {
                    Ok(T::default())
                } else {
                    serde_json::from_slice(&data).map_err(Error::from)
                }
            }
            Err(Error::NotFound(_)) => Ok(T::default()),
            Err(e) => Err(e),
        }
    }

    /// Read redirect metadata from a directory (returns default if not found).
    async fn read_redirect_meta(&self, dir: &str, ctx: &FsContext) -> Result<RedirectMeta> {
        self.read_meta(dir, REDIRECT_FILE, ctx).await
    }

    /// Read sync log metadata from a directory (returns default if not found).
    async fn read_sync_log_meta(&self, dir: &str, ctx: &FsContext) -> Result<SyncLogMeta> {
        self.read_meta(dir, SYNC_LOG_FILE, ctx).await
    }

    /// Read both metadata files from the primary backend.
    async fn read_dir_meta_pair(
        &self,
        dir: &str,
        ctx: &FsContext,
    ) -> Result<(RedirectMeta, SyncLogMeta)> {
        let redirect = self.read_redirect_meta(dir, ctx).await?;
        let sync_log = self.read_sync_log_meta(dir, ctx).await?;
        Ok((redirect, sync_log))
    }

    /// Write one JSON metadata file to a directory.
    async fn write_meta<T>(
        &self,
        dir: &str,
        filename: &str,
        meta: &T,
        ctx: &FsContext,
    ) -> Result<()>
    where
        T: Serialize,
    {
        let path = Self::meta_path(dir, filename);
        let data = serde_json::to_vec(meta)?;
        let effective_ctx = Self::effective_meta_ctx(dir, ctx);
        FS_CTX
            .scope(effective_ctx, async {
                self.primary_backend
                    .write(&path, &data, 0, WriteFlag::Create)
                    .await
                    .map(|_| ())
            })
            .await
    }

    /// Write redirect metadata to a directory.
    async fn write_redirect_meta(
        &self,
        dir: &str,
        meta: &RedirectMeta,
        ctx: &FsContext,
    ) -> Result<()> {
        self.write_meta(dir, REDIRECT_FILE, meta, ctx).await
    }

    /// Write sync log metadata to a directory.
    async fn write_sync_log_meta(
        &self,
        dir: &str,
        meta: &SyncLogMeta,
        ctx: &FsContext,
    ) -> Result<()> {
        self.write_meta(dir, SYNC_LOG_FILE, meta, ctx).await
    }

    /// Serialized read-modify-write of both `.redirect.json` and `.sync_log.json` in a directory.
    ///
    /// Acquires the directory lock, reads both metadata files, applies `op`, and writes both back.
    /// This prevents concurrent updates from losing entries.
    pub async fn update_dir_meta<F>(&self, dir: &str, ctx: &FsContext, op: F) -> Result<()>
    where
        F: FnOnce(&mut RedirectMeta, &mut SyncLogMeta) -> Result<()>,
    {
        let lock = self.get_dir_lock(dir).await;
        let _guard = lock.lock().await;

        let (mut redirect_meta, mut sync_log_meta) = self.read_dir_meta_pair(dir, ctx).await?;
        let original_redirect = redirect_meta.clone();
        let original_sync_log = sync_log_meta.clone();

        op(&mut redirect_meta, &mut sync_log_meta)?;

        if redirect_meta != original_redirect {
            self.write_redirect_meta(dir, &redirect_meta, ctx).await?;
        }
        if sync_log_meta != original_sync_log {
            self.write_sync_log_meta(dir, &sync_log_meta, ctx).await?;
        }
        Ok(())
    }

    /// Serialized read-modify-write of two directories' metadata (for cross-directory rename).
    ///
    /// Acquires both directory locks in lexicographic order to prevent deadlock,
    /// then reads and updates all four metadata files within the same critical section.
    /// Caller must ensure source_dir != target_dir; use update_dir_meta for same-directory case.
    pub async fn update_dual_dir_meta<F>(
        &self,
        source_dir: &str,
        target_dir: &str,
        ctx: &FsContext,
        op: F,
    ) -> Result<()>
    where
        F: FnOnce(
            &mut RedirectMeta,
            &mut SyncLogMeta,
            &mut RedirectMeta,
            &mut SyncLogMeta,
        ) -> Result<()>,
    {
        // Acquire locks in lexicographic order to avoid deadlock.
        let (first_dir, second_dir) = if source_dir < target_dir {
            (source_dir, target_dir)
        } else {
            (target_dir, source_dir)
        };

        let lock1 = self.get_dir_lock(first_dir).await;
        let lock2 = self.get_dir_lock(second_dir).await;
        let _guard1 = lock1.lock().await;
        let _guard2 = lock2.lock().await;

        let (mut src_redirect, mut src_sync_log) = self.read_dir_meta_pair(source_dir, ctx).await?;
        let (mut tgt_redirect, mut tgt_sync_log) = self.read_dir_meta_pair(target_dir, ctx).await?;
        let original_src_redirect = src_redirect.clone();
        let original_src_sync_log = src_sync_log.clone();
        let original_tgt_redirect = tgt_redirect.clone();
        let original_tgt_sync_log = tgt_sync_log.clone();

        op(
            &mut src_redirect,
            &mut src_sync_log,
            &mut tgt_redirect,
            &mut tgt_sync_log,
        )?;

        if src_redirect != original_src_redirect {
            self.write_redirect_meta(source_dir, &src_redirect, ctx)
                .await?;
        }
        if src_sync_log != original_src_sync_log {
            self.write_sync_log_meta(source_dir, &src_sync_log, ctx)
                .await?;
        }
        if tgt_redirect != original_tgt_redirect {
            self.write_redirect_meta(target_dir, &tgt_redirect, ctx)
                .await?;
        }
        if tgt_sync_log != original_tgt_sync_log {
            self.write_sync_log_meta(target_dir, &tgt_sync_log, ctx)
                .await?;
        }
        Ok(())
    }

    /// Read redirect metadata for a directory (public, used by read_dir to merge redirect entries).
    pub async fn get_redirect_meta(&self, dir: &str, ctx: &FsContext) -> Result<RedirectMeta> {
        self.read_dir_meta_pair(dir, ctx)
            .await
            .map(|(redirect, _)| redirect)
    }

    /// Read sync log metadata for a directory (public, used by retry_loop).
    pub async fn get_sync_log_meta(&self, dir: &str, ctx: &FsContext) -> Result<SyncLogMeta> {
        self.read_dir_meta_pair(dir, ctx)
            .await
            .map(|(_, sync_log)| sync_log)
    }

    /// Get a reference to the context resolver.
    pub fn ctx_resolver(&self) -> &Arc<dyn FsContextResolver> {
        &self.ctx_resolver
    }

    /// Get a reference to the primary backend.
    pub fn primary_backend(&self) -> &Arc<dyn FileSystem> {
        &self.primary_backend
    }

    /// Allocate and persist the next global sequence number.
    pub async fn next_seq(&self) -> Result<u64> {
        let _guard = self.global_state_lock.lock().await;
        let mut state = self.read_global_state().await?;
        let seq = state.next_seq;
        state.next_seq = state.next_seq.saturating_add(1);
        self.write_global_state(&state).await?;
        Ok(seq)
    }

    /// Read the persisted global state file.
    async fn read_global_state(&self) -> Result<GlobalMultiWriteState> {
        let ctx = Self::system_ctx();
        match FS_CTX
            .scope(ctx.clone(), async {
                self.primary_backend.read(GLOBAL_STATE_FILE, 0, 0).await
            })
            .await
        {
            Ok(data) => {
                if data.is_empty() {
                    Ok(GlobalMultiWriteState::default())
                } else {
                    let state: GlobalMultiWriteState = serde_json::from_slice(&data)?;
                    if state.version != GLOBAL_STATE_VERSION {
                        return Err(Error::config(format!(
                            "unsupported multi-write global state version {}",
                            state.version
                        )));
                    }
                    Ok(state)
                }
            }
            Err(Error::NotFound(_)) => Ok(GlobalMultiWriteState::default()),
            Err(e) => Err(e),
        }
    }

    /// Persist the global sequence state through the primary backend.
    async fn write_global_state(&self, state: &GlobalMultiWriteState) -> Result<()> {
        let ctx = Self::system_ctx();
        let data = serde_json::to_vec(state)?;
        FS_CTX
            .scope(ctx.clone(), async {
                self.primary_backend
                    .ensure_parent_dirs(GLOBAL_STATE_FILE, 0o755)
                    .await?;
                self.primary_backend
                    .write(GLOBAL_STATE_FILE, &data, 0, WriteFlag::Create)
                    .await
                    .map(|_| ())
            })
            .await
    }
}

/// Per-path serialization queue for async write ordering.
///
/// Ensures that multiple writes to the same path are executed in FIFO order
/// on backup backends, preventing out-of-order application.
pub struct PathSerializer {
    queues: Mutex<HashMap<String, Arc<TrackedLock>>>,
}

impl PathSerializer {
    /// Create a new PathSerializer.
    pub fn new() -> Self {
        Self::with_limits(0, Duration::ZERO)
    }

    /// Create a PathSerializer; limit arguments are ignored for compatibility.
    pub fn with_limits(_capacity: usize, _ttl: Duration) -> Self {
        Self {
            queues: Mutex::new(HashMap::new()),
        }
    }

    /// Run one async operation under the per-path serialization lock.
    pub async fn with_path_lock<F, Fut, T>(&self, path: &str, op: F) -> T
    where
        F: FnOnce() -> Fut,
        Fut: std::future::Future<Output = T>,
    {
        let mut queues = self.queues.lock().await;
        let lock = queues
            .entry(path.to_string())
            .or_insert_with(|| Arc::new(TrackedLock::new(())))
            .clone();
        drop(queues);

        let _guard = lock.lock().await;
        op().await
    }

    /// Return the number of tracked queue entries.
    #[cfg(test)]
    pub async fn len(&self) -> usize {
        self.queues.lock().await.len()
    }
}

impl Default for PathSerializer {
    fn default() -> Self {
        Self::new()
    }
}

/// Extract the directory path from a file path.
pub(crate) fn parent_dir(path: &str) -> String {
    match path.rfind('/') {
        Some(0) => "/".to_string(),
        Some(pos) => path[..pos].to_string(),
        None => "/".to_string(),
    }
}

/// Extract the file name from a path.
pub(crate) fn file_name(path: &str) -> &str {
    match path.rfind('/') {
        Some(pos) => &path[pos + 1..],
        None => path,
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
    use crate::plugins::memfs::MemFileSystem;
    use std::sync::Arc;

    #[test]
    fn test_parent_dir() {
        assert_eq!(parent_dir("/a/b/c.txt"), "/a/b");
        assert_eq!(parent_dir("/a"), "/");
        assert_eq!(parent_dir("/"), "/");
    }

    #[test]
    fn test_file_name() {
        assert_eq!(file_name("/a/b/c.txt"), "c.txt");
        assert_eq!(file_name("/a"), "a");
    }

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

    #[test]
    fn test_mount_root_resolver_extracts_account_from_non_local_path() {
        let resolver = MountRootFsContextResolver::new("/s3");
        let ctx = resolver.resolve("/s3/acct-1/resources/file.txt").unwrap();
        assert_eq!(ctx.account_id(), "acct-1");
        assert!(resolver
            .resolve("/local/acct-1/resources/file.txt")
            .is_err());
    }

    #[tokio::test]
    async fn test_invalid_sync_log_json_returns_error() {
        let primary: Arc<dyn FileSystem> = Arc::new(MemFileSystem::new());
        let store = MetaStateStore::new(primary.clone(), Arc::new(DefaultFsContextResolver));
        let ctx = Arc::new(FsContextInner::new("acct".to_string()));

        FS_CTX
            .scope(ctx.clone(), async {
                primary
                    .ensure_parent_dirs("/local/acct/docs/.sync_log.json", 0o755)
                    .await?;
                primary
                    .write(
                        "/local/acct/docs/.sync_log.json",
                        b"{not valid json",
                        0,
                        WriteFlag::Create,
                    )
                    .await?;
                Ok::<(), Error>(())
            })
            .await
            .unwrap();

        let result = store.get_sync_log_meta("/local/acct/docs", &ctx).await;
        assert!(result.is_err(), "corrupted metadata must fail fast");
    }

    #[tokio::test]
    async fn test_next_seq_uses_dedicated_global_lock() {
        let primary: Arc<dyn FileSystem> = Arc::new(MemFileSystem::new());
        let store = MetaStateStore::new(primary, Arc::new(DefaultFsContextResolver));

        assert_eq!(store.next_seq().await.unwrap(), 1);
        assert_eq!(store.next_seq().await.unwrap(), 2);

        let dir_locks = store.dir_locks.lock().await;
        assert!(
            !dir_locks.contains_key(GLOBAL_STATE_FILE),
            "global sequence locking should not occupy the directory lock pool"
        );
    }
}
