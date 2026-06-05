//! Multi-write metadata management.
//!
//! Provides `MetaStateStore` for serialized read-modify-write of `.redirect.json` and
//! `.sync_log.json` through `primary_backend`, and `FsContextResolver` for recovering
//! `FsContext` from paths in background tasks.

use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::Mutex;

use serde::{de::DeserializeOwned, Serialize};

use super::context::{FsContext, FsContextInner, FS_CTX};
use super::errors::{Error, Result};
use super::filesystem::FileSystem;
use super::types::{RedirectMeta, SyncLogMeta, WriteFlag};

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

#[derive(Clone)]
struct DirMetaCacheEntry {
    redirect: RedirectMeta,
    sync_log: SyncLogMeta,
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
    /// Context resolver for background tasks
    ctx_resolver: Arc<dyn FsContextResolver>,
}

impl MetaStateStore {
    /// Create a new MetaStateStore.
    pub fn new(
        primary_backend: Arc<dyn FileSystem>,
        ctx_resolver: Arc<dyn FsContextResolver>,
    ) -> Self {
        Self {
            primary_backend,
            dir_locks: Mutex::new(HashMap::new()),
            meta_cache: Mutex::new(HashMap::new()),
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

    /// Remove an idle directory lock from the lock map.
    async fn cleanup_dir_lock(&self, dir: &str, lock: &Arc<Mutex<()>>) {
        let mut locks = self.dir_locks.lock().await;
        if Arc::strong_count(lock) <= 2 {
            locks.remove(dir);
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
                    Ok(serde_json::from_slice(&data).unwrap_or_default())
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
        if let Some(cached) = self.meta_cache.lock().await.get(dir).cloned() {
            return Ok((cached.redirect, cached.sync_log));
        }

        let redirect = self.read_redirect_meta(dir, ctx).await?;
        let sync_log = self.read_sync_log_meta(dir, ctx).await?;
        self.meta_cache.lock().await.insert(
            dir.to_string(),
            DirMetaCacheEntry {
                redirect: redirect.clone(),
                sync_log: sync_log.clone(),
            },
        );
        Ok((redirect, sync_log))
    }

    /// Update cached metadata for a directory.
    async fn update_meta_cache(&self, dir: &str, redirect: RedirectMeta, sync_log: SyncLogMeta) {
        self.meta_cache
            .lock()
            .await
            .insert(dir.to_string(), DirMetaCacheEntry { redirect, sync_log });
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
        drop(_guard);
        self.cleanup_dir_lock(dir, &lock).await;

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
        drop(_guard2);
        drop(_guard1);
        self.cleanup_dir_lock(first_dir, &lock1).await;
        self.cleanup_dir_lock(second_dir, &lock2).await;

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

    /// Remove an idle path lock from the queue map.
    pub async fn cleanup_path_lock(&self, path: &str, lock: &Arc<Mutex<()>>) {
        let mut queues = self.queues.lock().await;
        if Arc::strong_count(lock) <= 2 {
            queues.remove(path);
        }
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
        .map_err(|_| Error::internal("FsContext not set in current task"))
}

#[cfg(test)]
mod tests {
    use super::*;

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
}
