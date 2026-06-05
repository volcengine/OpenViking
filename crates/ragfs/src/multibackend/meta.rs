//! Multi-write metadata management.
//!
//! Provides `MetaStateStore` for serialized read-modify-write of `.redirect.json` and
//! `.sync_log.json` through `primary_backend`, and `FsContextResolver` for recovering
//! `FsContext` from paths in background tasks.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};
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

/// Internal file names for metadata files.
const REDIRECT_FILE: &str = ".redirect.json";
const SYNC_LOG_FILE: &str = ".sync_log.json";
const GLOBAL_STATE_FILE: &str = "/local/_system/.multiwrite.global.json";
const GLOBAL_STATE_VERSION: u32 = 1;
const DEFAULT_META_CACHE_CAPACITY: usize = 1024;
const DEFAULT_META_CACHE_TTL: Duration = Duration::from_secs(30);

#[derive(Clone)]
struct DirMetaCacheEntry {
    redirect: RedirectMeta,
    sync_log: SyncLogMeta,
    cached_at: Instant,
    last_accessed_at: Instant,
}

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
    dir_locks: Mutex<HashMap<String, Arc<Mutex<()>>>>,
    /// In-memory directory metadata cache to reduce repeated full JSON reads.
    meta_cache: Mutex<HashMap<String, DirMetaCacheEntry>>,
    /// Maximum number of directory cache entries to retain.
    meta_cache_capacity: usize,
    /// Time-to-live for one directory cache entry.
    meta_cache_ttl: Duration,
    /// Context resolver for background tasks
    ctx_resolver: Arc<dyn FsContextResolver>,
}

impl MetaStateStore {
    /// Create a new MetaStateStore.
    pub fn new(
        primary_backend: Arc<dyn FileSystem>,
        ctx_resolver: Arc<dyn FsContextResolver>,
    ) -> Self {
        Self::with_cache_config(
            primary_backend,
            ctx_resolver,
            DEFAULT_META_CACHE_CAPACITY,
            DEFAULT_META_CACHE_TTL,
        )
    }

    /// Create a new MetaStateStore with explicit cache sizing.
    pub fn with_cache_config(
        primary_backend: Arc<dyn FileSystem>,
        ctx_resolver: Arc<dyn FsContextResolver>,
        meta_cache_capacity: usize,
        meta_cache_ttl: Duration,
    ) -> Self {
        Self {
            primary_backend,
            dir_locks: Mutex::new(HashMap::new()),
            meta_cache: Mutex::new(HashMap::new()),
            meta_cache_capacity: meta_cache_capacity.max(1),
            meta_cache_ttl,
            ctx_resolver,
        }
    }

    /// Get or create a per-directory lock.
    async fn get_dir_lock(&self, dir: &str) -> Arc<Mutex<()>> {
        let mut locks = self.dir_locks.lock().await;
        locks
            .entry(dir.to_string())
            .or_insert_with(|| Arc::new(Mutex::new(())))
            .clone()
    }

    /// Return the dedicated `_system` context used by global metadata.
    fn system_ctx() -> FsContext {
        Arc::new(FsContextInner::new("_system".to_string()))
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
        match FS_CTX
            .scope(ctx.clone(), async {
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

    /// Read both metadata files, using the directory cache when available.
    async fn read_dir_meta_pair(
        &self,
        dir: &str,
        ctx: &FsContext,
    ) -> Result<(RedirectMeta, SyncLogMeta)> {
        let now = Instant::now();
        {
            let mut cache = self.meta_cache.lock().await;
            if let Some(cached) = cache.get_mut(dir) {
                if now.duration_since(cached.cached_at) <= self.meta_cache_ttl {
                    cached.last_accessed_at = now;
                    return Ok((cached.redirect.clone(), cached.sync_log.clone()));
                }
            }
            cache.remove(dir);
        }

        let redirect = self.read_redirect_meta(dir, ctx).await?;
        let sync_log = self.read_sync_log_meta(dir, ctx).await?;
        self.meta_cache.lock().await.insert(
            dir.to_string(),
            DirMetaCacheEntry {
                redirect: redirect.clone(),
                sync_log: sync_log.clone(),
                cached_at: now,
                last_accessed_at: now,
            },
        );
        self.prune_meta_cache().await;
        Ok((redirect, sync_log))
    }

    /// Update cached metadata for a directory.
    async fn update_meta_cache(&self, dir: &str, redirect: RedirectMeta, sync_log: SyncLogMeta) {
        let now = Instant::now();
        self.meta_cache.lock().await.insert(
            dir.to_string(),
            DirMetaCacheEntry {
                redirect,
                sync_log,
                cached_at: now,
                last_accessed_at: now,
            },
        );
        self.prune_meta_cache().await;
    }

    /// Remove expired cache entries and trim the cache to capacity.
    async fn prune_meta_cache(&self) {
        let now = Instant::now();
        let mut cache = self.meta_cache.lock().await;
        cache.retain(|_, entry| now.duration_since(entry.cached_at) <= self.meta_cache_ttl);
        while cache.len() > self.meta_cache_capacity {
            let oldest_key = cache
                .iter()
                .min_by_key(|(_, entry)| entry.last_accessed_at)
                .map(|(key, _)| key.clone());
            if let Some(oldest_key) = oldest_key {
                cache.remove(&oldest_key);
            } else {
                break;
            }
        }
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
        FS_CTX
            .scope(ctx.clone(), async {
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
        self.update_meta_cache(dir, redirect_meta, sync_log_meta)
            .await;

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
        self.update_meta_cache(source_dir, src_redirect, src_sync_log)
            .await;
        self.update_meta_cache(target_dir, tgt_redirect, tgt_sync_log)
            .await;

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
        let lock = self.get_dir_lock(GLOBAL_STATE_FILE).await;
        let _guard = lock.lock().await;
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
    queues: Mutex<HashMap<String, Arc<Mutex<()>>>>,
}

impl PathSerializer {
    /// Create a new PathSerializer.
    pub fn new() -> Self {
        Self {
            queues: Mutex::new(HashMap::new()),
        }
    }

    /// Get or create a per-path serialization lock.
    pub async fn get_path_lock(&self, path: &str) -> Arc<Mutex<()>> {
        let mut queues = self.queues.lock().await;
        queues
            .entry(path.to_string())
            .or_insert_with(|| Arc::new(Mutex::new(())))
            .clone()
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
}
